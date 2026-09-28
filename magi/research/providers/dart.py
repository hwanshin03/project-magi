"""OpenDART official corporation, disclosures and full financial statement adapter."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from io import BytesIO
import re
from xml.etree import ElementTree
from zipfile import ZipFile, BadZipFile
from ..models import ResearchSource,SourceType,Authority,EvidenceItem,SourceLocator,Category,EvidencePack
from ..selection import deduplicate
from ..security import url
from .base import Issuer,CompanyProfile,FinancialFacts,ResearchError,ErrorCode,boundary
from .transport import ResearchHTTP

REPORTS={'q1':'11013','half':'11012','q3':'11014','annual':'11011'}
STATUSES={'010':ErrorCode.AUTHENTICATION,'011':ErrorCode.AUTHENTICATION,'012':ErrorCode.AUTHENTICATION,
          '013':ErrorCode.NO_DATA,'014':ErrorCode.NOT_FOUND,'020':ErrorCode.RATE_LIMITED,
          '021':ErrorCode.INVALID_REQUEST,'100':ErrorCode.INVALID_REQUEST,'101':ErrorCode.INVALID_REQUEST,
          '800':ErrorCode.UNAVAILABLE,'900':ErrorCode.UNAVAILABLE,'901':ErrorCode.AUTHENTICATION}


def checked(data):
    if not isinstance(data,dict) or 'status' not in data: raise ResearchError(ErrorCode.INVALID_RESPONSE)
    if data['status']!='000': raise ResearchError(STATUSES.get(data['status'],ErrorCode.INVALID_RESPONSE))
    return data


def number(value):
    if value is None: return None
    if not isinstance(value,str): raise ValueError('Expected financial text')
    value=value.strip()
    if value in ('','-'): return None
    if ',' in value and not re.fullmatch(r'-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\(\d{1,3}(?:,\d{3})+(?:\.\d+)?\)',value):
        raise ValueError('Invalid grouping')
    value=value.replace(',','')
    if re.fullmatch(r'\(\d+(?:\.\d+)?\)',value): value='-'+value[1:-1]
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',value): raise ValueError('Invalid financial amount')
    return Decimal(value)


def period(value):
    if not value: return None,None
    found=re.findall(r'\d{4}[.-]\d{2}[.-]\d{2}',value)
    if len(found) not in (1,2): return None,None
    dates=[date.fromisoformat(v.replace('.','-')) for v in found]
    return (None,dates[0]) if len(dates)==1 else tuple(dates)


def receipt(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{14}',value): raise ValueError('Invalid receipt')
    date.fromisoformat(value[:4]+'-'+value[4:6]+'-'+value[6:8])
    return value


def stamp(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{8}',value): raise ValueError('Invalid receipt date')
    return datetime.strptime(value,'%Y%m%d').replace(tzinfo=timezone(timedelta(hours=9)))


class DARTProvider:
    def __init__(self,*,http=None,**options):
        self.http=http if http is not None else ResearchHTTP('DART',**options)
        self._owns=http is None
        self._codes_payload=None;self._code_records=None
    def close(self):
        self._codes_payload=None;self._code_records=None
        if self._owns: self.http.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def _codes(self):
        payload=self.http.get('/api/corpCode.xml')
        if payload is self._codes_payload: return self._code_records
        try:
            if b'<!DOCTYPE' in payload.body.upper() or b'<!ENTITY' in payload.body.upper(): raise ValueError('Unsafe XML')
            if not payload.body.startswith(b'PK'):
                root=ElementTree.fromstring(payload.body)
                raise ResearchError(STATUSES.get(root.findtext('status'),ErrorCode.INVALID_RESPONSE))
            with ZipFile(BytesIO(payload.body)) as archive:
                entries=archive.infolist()
                if len(entries)!=1 or entries[0].filename.upper()!='CORPCODE.XML' or entries[0].file_size>64*1024*1024:
                    raise ValueError('Invalid corporation archive')
                data=archive.read(entries[0])
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper(): raise ValueError('Unsafe XML')
            root=ElementTree.fromstring(data)
            if root.tag!='result': raise ValueError('Invalid corporation document')
            records=[]
            for row in root.findall('list'):
                code=row.findtext('corp_code');stock=(row.findtext('stock_code') or '').strip() or None
                modified=row.findtext('modify_date') or None
                if modified: stamp(modified)
                records.append(Issuer('DART',code,row.findtext('corp_name'),'KR',payload.retrieved_at,stock,modified_at=modified))
            self._codes_payload=payload;self._code_records=tuple(records)
            return self._code_records
        except (BadZipFile,ElementTree.ParseError,ValueError,RuntimeError,KeyError):
            raise ResearchError(ErrorCode.INVALID_RESPONSE) from None

    def _resolve(self,identifier):
        if not isinstance(identifier,str) or not identifier.strip(): raise ResearchError(ErrorCode.INVALID_REQUEST)
        name=identifier.strip()
        if name.isdigit() and len(name) not in (6,8): raise ResearchError(ErrorCode.INVALID_REQUEST)
        records=self._codes()
        matches=[r for r in records if (r.ticker==name if re.fullmatch(r'[0-9][0-9A-Z]{5}',name) else
            r.provider_issuer_id==name if re.fullmatch(r'\d{8}',name) else r.company_name==name)]
        if not matches: raise ResearchError(ErrorCode.NOT_FOUND)
        if len(matches)!=1: raise ResearchError(ErrorCode.AMBIGUOUS)
        return matches[0]

    @boundary
    def resolve_issuer(self,identifier): return self._resolve(identifier)

    @boundary
    def get_company_profile(self,identifier):
        issuer=self._resolve(identifier);payload=self.http.get('/api/company.json',{'corp_code':issuer.provider_issuer_id})
        data=checked(payload.json())
        if data['corp_code']!=issuer.provider_issuer_id: raise ValueError('Issuer mismatch')
        if data.get('stock_code') not in (None,'',issuer.ticker): raise ValueError('Stock code mismatch')
        result={k:data[k] for k in ('corp_name','corp_name_eng','stock_name','stock_code','ceo_nm','corp_cls','hm_url','ir_url') if data.get(k)}
        for key in ('hm_url','ir_url'):
            if key in result:
                # Retain official values only; don't invent a scheme for bare hosts.
                if not result[key].startswith(('http://','https://')): result.pop(key)
                else: url(result[key])
        return CompanyProfile(issuer,result,payload.retrieved_at)

    def _source(self,issuer,rcept,name,retrieved,*,receipt_date=None,metadata=None):
        rcept=receipt(rcept)
        return ResearchSource(SourceType.DART_FILING,Authority.PRIMARY,'DART',name,issuer.company_name,retrieved,
            url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+rcept,published_at=stamp(receipt_date or rcept[:8]),
            ticker=issuer.ticker,market='KR',company_name=issuer.company_name,document_type=name,external_id=rcept,
            metadata={'issuer_id':issuer.issuer_id,'corp_code':issuer.provider_issuer_id,'receipt_number':rcept,
                'receipt_date':receipt_date or rcept[:8],'publication_precision':'date; midnight KST convention',**(metadata or {})})

    @boundary
    def list_filings(self,identifier,*,start=None,end=None,page=1):
        if type(page) is not int or not 1<=page<=10: raise ResearchError(ErrorCode.INVALID_REQUEST)
        issuer=self._resolve(identifier)
        params={'corp_code':issuer.provider_issuer_id,'page_no':str(page),'page_count':'100','last_reprt_at':'N'}
        for key,value in (('bgn_de',start),('end_de',end)):
            if value is not None: stamp(value);params[key]=value
        payload=self.http.get('/api/list.json',params)
        data=checked(payload.json());rows=data['list']
        if not isinstance(rows,list): raise ValueError('Invalid disclosure list')
        sources=[]
        for row in rows:
            if row['corp_code']!=issuer.provider_issuer_id: raise ValueError('Issuer mismatch')
            title=row['report_nm']
            sources.append(self._source(issuer,row['rcept_no'],title,payload.retrieved_at,receipt_date=row['rcept_dt'],
                metadata={'filing_corp_name':row['corp_name'],'submitter':row.get('flr_nm'),'remarks':row.get('rm'),'official_report_name':title,
                    'correction_marker':'정정' in title,'listing_page':page,'listing_total_pages':data.get('total_page')}))
        return deduplicate(sources)

    @boundary
    def get_financial_facts(self,identifier,*,year,report='annual',division='CFS'):
        if type(year) is not int or not 1999<=year<=self.http.now().year or report not in REPORTS or division not in ('CFS','OFS'):
            raise ResearchError(ErrorCode.INVALID_REQUEST)
        issuer=self._resolve(identifier);code=REPORTS[report]
        payload=self.http.get('/api/fnlttSinglAcntAll.json',{'corp_code':issuer.provider_issuer_id,
            'bsns_year':str(year),'reprt_code':code,'fs_div':division})
        data=checked(payload.json());rows=data['list']
        if not isinstance(rows,list): raise ValueError('Invalid financial list')
        sources={};items=[]
        for row in rows:
            if row['corp_code']!=issuer.provider_issuer_id or row['bsns_year']!=str(year) or row['reprt_code']!=code:
                raise ValueError('Statement context mismatch')
            rcept=receipt(row['rcept_no'])
            if rcept not in sources:
                sources[rcept]=self._source(issuer,rcept,issuer.company_name+' '+str(year)+' '+report,payload.retrieved_at,
                    metadata={'business_year':year,'report_code':code,'report_period':report,'statement_division':division})
            source=sources[rcept]
            currency=row.get('currency') or None
            if currency is not None and not re.fullmatch('[A-Z]{3}',currency): raise ValueError('Invalid currency')
            cat={'BS':Category.BALANCE_SHEET,'CF':Category.CASH_FLOW,'IS':Category.FINANCIAL,'CIS':Category.FINANCIAL}.get(row['sj_div'],Category.OTHER)
            if row.get('account_id') in ('ifrs-full_ProfitLoss','ifrs-full_ProfitLossAttributableToOwnersOfParent'): cat=Category.PROFITABILITY
            for field,prefix in [('thstrm_amount','thstrm'),('thstrm_add_amount','thstrm'),('frmtrm_amount','frmtrm'),
                                 ('frmtrm_q_amount','frmtrm'),('frmtrm_add_amount','frmtrm'),('bfefrmtrm_amount','bfefrmtrm')]:
                if field not in row: continue
                value=number(row[field]);start,end=period(row.get(prefix+'_dt'))
                context={k:row[k] for k in ('account_id','account_nm','account_detail','sj_div','sj_nm','ord') if k in row}
                context.update(business_year=year,report_code=code,report_period=report,statement_division=division,
                    amount_field=field,period_name=row.get(prefix+'_nm'),period_text=row.get(prefix+'_dt'),receipt_number=rcept)
                items.append(EvidenceItem(source.source_id,issuer.company_name,cat,
                    row['account_nm']+' ('+field+'): '+('not supplied' if value is None else str(value))+((' '+currency) if currency else ''),
                    payload.retrieved_at,ticker=issuer.ticker,value=value,unit=currency,period_start=start,period_end=end,
                    as_of=datetime.combine(end,datetime.min.time(),timezone(timedelta(hours=9))) if start is None and end else None,
                    source_locator=SourceLocator(table=row['sj_nm'],xbrl_concept=row.get('account_id') or None),metadata=context))
        if not items: raise ResearchError(ErrorCode.NO_DATA)
        return FinancialFacts(issuer,deduplicate(tuple(sources.values())),deduplicate(items),payload.retrieved_at)

    @boundary
    def build_evidence_pack(self,identifier,**options):
        data=self.get_financial_facts(identifier,**options).require()
        return EvidencePack(data.issuer.company_name,self.http.now(),sources=data.sources,evidence_items=data.evidence_items,
                            ticker=data.issuer.ticker,market='KR')
