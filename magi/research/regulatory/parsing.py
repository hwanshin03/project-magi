"""Agency-specific bounded metadata contracts; no body scraping or status inference."""
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from collections.abc import Sequence
from html.parser import HTMLParser
import json
import re
import xml.etree.ElementTree as ET
from ..models import instant
from .models import RegulatoryItem, RegulatoryType as T, RegulatoryStatus as S
from .catalog import source_spec, RegulatoryError, FEDERAL_REGISTER_DOCUMENT_NUMBER
from .transport import MAX_BYTES

MAX_ITEMS = 200
TYPES = {'Proposed Rule':(T.RULE_PROPOSAL,S.PROPOSED),'Rule':(T.FINAL_RULE,S.FINAL),
         'Notice':(T.PUBLIC_NOTICE,S.UNKNOWN)}
ACTIONS = {'Proposed rule.':(T.RULE_PROPOSAL,S.PROPOSED),'Final rule.':(T.FINAL_RULE,S.FINAL),
           'Interim final rule.':(T.INTERIM_FINAL_RULE,S.INTERIM),
           'Guidance.':(T.GUIDANCE,S.GUIDANCE),'Enforcement action.':(T.ENFORCEMENT,S.ENFORCEMENT_ACTION),
           'Withdrawal.':(T.OTHER,S.WITHDRAWN),'Superseded.':(T.OTHER,S.SUPERSEDED)}
FSC_CATEGORIES = {'입법예고':(T.RULE_PROPOSAL,S.PROPOSED),'행정지도':(T.GUIDANCE,S.GUIDANCE),
                  '제재':(T.ENFORCEMENT,S.ENFORCEMENT_ACTION),'보도자료':(T.PUBLIC_NOTICE,S.UNKNOWN)}


@dataclass(frozen=True)
class SkippedRegulatoryItem:
    index: int
    code: str


@dataclass(frozen=True)
class RegulatoryParseResult(Sequence):
    items: tuple
    skipped: tuple = ()
    def __len__(self): return len(self.items)
    def __getitem__(self, index): return self.items[index]
    @property
    def skipped_count(self): return len(self.skipped)
    @property
    def total_count(self): return len(self.items) + len(self.skipped)


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ('script','style') and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def plain(value, limit):
    if value is None: return None
    if not isinstance(value, str): raise RegulatoryError('INVALID_TEXT')
    parser=_Plain(); parser.feed(value); parser.close()
    return re.sub(r'\s+',' ',' '.join(parser.parts)).strip()[:limit] or None


def parse_date(value):
    if value is None or value == '': return None
    try:
        if not isinstance(value,str): raise ValueError()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): return date.fromisoformat(value)
        try: result=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError: result=parsedate_to_datetime(value)
        instant(result); return result
    except (ValueError,TypeError,OverflowError):
        raise RegulatoryError('INVALID_DATE') from None


def _federal_register(spec, row, retrieved_at):
    if not isinstance(row,dict): raise RegulatoryError('INVALID_ITEM')
    agencies=row.get('agencies')
    if not isinstance(agencies,list) or not any(isinstance(a,dict) and a.get('slug')==spec.agency_slug for a in agencies):
        raise RegulatoryError('AGENCY_MISMATCH')
    doc=row.get('document_number')
    if not isinstance(doc,str) or not re.fullmatch(FEDERAL_REGISTER_DOCUMENT_NUMBER,doc): raise RegulatoryError('INVALID_REFERENCE')
    kind,status=TYPES.get(row.get('type'),(T.OTHER,S.UNKNOWN))
    action=row.get('action')
    if action in ACTIONS: kind,status=ACTIONS[action]
    meta={'source_record_id':doc,'classification_explicit':row.get('type') in TYPES or action in ACTIONS}
    if row.get('type'): meta['source_type_label']=row['type']
    if action: meta['source_status_label']=action
    rins=row.get('regulation_id_numbers',[])
    if not isinstance(rins,list): raise RegulatoryError('INVALID_REFERENCE')
    if rins: meta['rule_references']=tuple(rins)
    related=[]
    for key,relation in (('correction_of','CORRECTS'),('amends','AMENDS'),('supersedes','SUPERSEDES')):
        if row.get(key): related.append((relation,row[key]))
    if related: meta['related_references']=tuple(related)
    cfr=row.get('cfr_references',[])
    if not isinstance(cfr,list) or len(cfr)>20: raise RegulatoryError('INVALID_REFERENCE')
    refs=[]
    for ref in cfr:
        if not isinstance(ref,dict): raise RegulatoryError('INVALID_REFERENCE')
        title,part=str(ref.get('title','')),str(ref.get('part',''))
        if not re.fullmatch(r'\d{1,3}',title) or not re.fullmatch(r'\d{1,5}',part): raise RegulatoryError('INVALID_REFERENCE')
        refs.append(title+' CFR '+part)
    # No headline, abstract or effective-date inference. Missing remains unknown.
    summary=plain(row.get('abstract'),601)
    if summary and len(summary)>600: meta['excerpt_truncated']=True
    return RegulatoryItem(spec.key,spec.agency,spec.jurisdiction,plain(row.get('title'),500),
        summary[:600] if summary else None,row.get('html_url'),parse_date(row.get('publication_date')),
        parse_date(row.get('effective_on')),retrieved_at,spec.language,kind,status,
        '; '.join(refs) or None,doc,metadata=meta)


def _fsc_publication_date(node):
    # Preserve RSS precedence, including conservative failure for malformed pubDate.
    if node.find('pubDate') is not None:
        return parse_date(node.findtext('pubDate'))
    value = node.findtext('{http://purl.org/dc/elements/1.1/}date')
    # Observed FSC values are timezone-free midnight labels. Preserve only the
    # explicit calendar date; do not manufacture a timezone or an instant.
    if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} 00:00:00', value):
        return parse_date(value[:10])
    return parse_date(value)


def _fsc(spec, node, retrieved_at):
    category=node.findtext('category')
    kind,status=FSC_CATEGORIES.get(category,(T.PUBLIC_NOTICE,S.UNKNOWN))
    meta={'classification_explicit':category in FSC_CATEGORIES}
    if category: meta['source_type_label']=category
    # Optional explicitly labeled metadata fields are a candidate contract,
    # not a claim that the live feed supplies them. Never parse dates from prose.
    explicit_type=node.findtext('regulatoryType')
    explicit_status=node.findtext('regulatoryStatus')
    if explicit_type: kind=T(explicit_type); meta['classification_explicit']=True
    if explicit_status: status=S(explicit_status); meta['classification_explicit']=True
    identifier=node.findtext('guid')
    if identifier: meta['source_record_id']=identifier
    summary=plain(node.findtext('description'),601)
    if summary and len(summary)>600: meta['excerpt_truncated']=True
    language=node.get('{http://www.w3.org/XML/1998/namespace}lang',spec.language)
    if language not in ('ko','ko-KR'): raise RegulatoryError('INVALID_LANGUAGE')
    return RegulatoryItem(spec.key,spec.agency,spec.jurisdiction,plain(node.findtext('title'),500),
        summary[:600] if summary else None,node.findtext('link'),_fsc_publication_date(node),
        parse_date(node.findtext('effectiveDate')),retrieved_at,'ko-KR',kind,status,
        node.findtext('legalReference'),node.findtext('reference'),metadata=meta)


def parse(source_key, raw, retrieved_at):
    spec=source_spec(source_key)
    try:
        instant(retrieved_at)
        if not isinstance(raw,bytes) or len(raw)>MAX_BYTES: raise RegulatoryError('RESPONSE_LIMIT')
        data=raw.decode('utf-8-sig')
        if spec.format=='json':
            payload=json.loads(data)
            if not isinstance(payload,dict) or not isinstance(payload.get('results'),list): raise RegulatoryError('INVALID_RESPONSE')
            rows=payload['results']; mapper=_federal_register
        else:
            if '\x00' in data or re.search(r'<!\s*(?:DOCTYPE|ENTITY)',data,re.I): raise RegulatoryError('UNSAFE_XML')
            root=ET.fromstring(data)
            if root.tag!='rss' or root.find('channel') is None: raise RegulatoryError('INVALID_FEED')
            rows=root.find('channel').findall('item'); mapper=_fsc
        if len(rows)>MAX_ITEMS: raise RegulatoryError('ITEM_LIMIT')
        items=[]; skipped=[]
        for index,row in enumerate(rows):
            try: items.append(mapper(spec,row,retrieved_at))
            except (ValueError,TypeError,AttributeError,KeyError,OverflowError) as error:
                code=str(error) if isinstance(error,RegulatoryError) else 'REGULATORY_INVALID_ITEM'
                skipped.append(SkippedRegulatoryItem(index,code))
        return RegulatoryParseResult(tuple(items),tuple(skipped))
    except RegulatoryError: raise
    except (ValueError,TypeError,AttributeError,KeyError,ET.ParseError,RecursionError):
        raise RegulatoryError('INVALID_RESPONSE') from None
