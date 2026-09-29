"""Closed RSS/Atom and schema.org metadata extraction; never crawl item links."""
from datetime import datetime
from dataclasses import dataclass
from collections.abc import Sequence
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from ..models import instant
from .catalog import CompanySourceError, official_url
from .models import OfficialCompanyItem, OfficialItemType as Kind
from .transport import MAX_BYTES
from .compatibility import date_preview

MAX_ITEMS = 200
ATOM = '{http://www.w3.org/2005/Atom}'
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'
XHTML = '{http://www.w3.org/1999/xhtml}'
CATEGORIES = {
    'Earnings': Kind.EARNINGS_RELEASE, 'Earnings Release': Kind.EARNINGS_RELEASE,
    'Earnings Releases': Kind.EARNINGS_RELEASE, '실적발표': Kind.EARNINGS_RELEASE,
    'Product Announcement': Kind.PRODUCT_ANNOUNCEMENT, '제품 발표': Kind.PRODUCT_ANNOUNCEMENT,
    'Investor Event': Kind.IR_EVENT, 'Presentation': Kind.IR_PRESENTATION,
    'IR Presentation': Kind.IR_PRESENTATION, 'IR Notice': Kind.COMPANY_NOTICE,
    'Dividend': Kind.DIVIDEND_NOTICE, '배당': Kind.DIVIDEND_NOTICE,
    'Shareholder Notice': Kind.SHAREHOLDER_NOTICE, '주주 공지': Kind.SHAREHOLDER_NOTICE,
    'Press Release': Kind.PRESS_RELEASE, 'Corporate News': Kind.CORPORATE_NEWS,
}
SCHEMA_KINDS = {'NewsArticle': None, 'Article': None, 'Event': Kind.IR_EVENT,
                'PresentationDigitalDocument': Kind.IR_PRESENTATION}


@dataclass(frozen=True)
class SkippedItem:
    index: int  # Zero-based source entry position; never article text or URL.
    code: str
    date_value: str | None = None


@dataclass(frozen=True)
class ParseResult(Sequence):
    items: tuple
    skipped: tuple

    def __len__(self): return len(self.items)
    def __getitem__(self, index): return self.items[index]
    @property
    def skipped_count(self): return len(self.skipped)
    @property
    def total_count(self): return len(self.items) + len(self.skipped)


class InvalidDate(CompanySourceError):
    def __init__(self, value):
        self.date_value = date_preview(value)
        super().__init__('INVALID_DATE')


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden: self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def plain(value, limit):
    if value is None: return None
    if not isinstance(value, str): raise CompanySourceError('INVALID_TEXT')
    p = _Plain(); p.feed(value); p.close()
    return re.sub(r'\s+', ' ', ' '.join(p.parts)).strip()[:limit] or None


def timestamp(value, metadata):
    if value is None or value == '': return None
    try:
        if not isinstance(value, str): raise ValueError()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            # Keep existing date formats and timezone rules unchanged.
            datetime.strptime(value, '%Y-%m-%d')
            metadata['publication_date'] = value
            return None
        try:
            result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            result = parsedate_to_datetime(value)
        instant(result)
        return result
    except (ValueError, TypeError, OverflowError):
        raise InvalidDate(value) from None


def _kind(categories, default):
    if any(c in ('SEC Filing', 'SEC Filings', 'DART Filing') for c in categories):
        return None
    kinds = {CATEGORIES[c] for c in categories if c in CATEGORIES}
    if len(kinds) > 1: raise CompanySourceError('AMBIGUOUS_CATEGORY')
    return next(iter(kinds)) if kinds else Kind(default)


def _item(spec, row, retrieved_at):
    categories = row.get('categories', ())
    if not isinstance(categories, (tuple, list)) or any(not isinstance(c, str) for c in categories):
        raise CompanySourceError('INVALID_CATEGORY')
    kind = _kind(categories, row.get('kind') or spec.item_type)
    if kind is None: return None
    meta = {'format': spec.format}
    recognized = [c for c in categories if c in CATEGORIES]
    if recognized: meta['category'] = recognized[0]
    published = timestamp(row.get('published'), meta)
    if published is not None and published > retrieved_at:
        raise InvalidDate(row.get('published'))
    if row.get('event_time'):
        event_time = timestamp(row['event_time'], {})
        if event_time: meta['event_time'] = event_time.isoformat()
    links = tuple(sorted(set(row.get('translations', ()))))
    if links: meta['translation_urls'] = links
    if spec.provider == 'nvidia_official' and row.get('issuer') and row['issuer'] not in {
            'NVIDIA', 'NVIDIA Corporation'}:
        raise CompanySourceError('IDENTITY_MISMATCH')
    if spec.provider == 'samsung_electronics_official' and row.get('issuer') and row['issuer'] not in {
            'Samsung Electronics', 'Samsung Electronics Co., Ltd.', '삼성전자'}:
        raise CompanySourceError('IDENTITY_MISMATCH')
    title = plain(row.get('title'), 500)
    excerpt = plain(row.get('summary'), 601)
    if excerpt and len(excerpt) > 600: meta['excerpt_truncated'] = True
    link = row.get('url')
    # Relative metadata links are resolved only against the fixed official endpoint.
    if not isinstance(link, str) or not link: raise CompanySourceError('MISSING_ITEM_URL')
    link = official_url(urljoin(spec.endpoint, link), spec.provider)
    return OfficialCompanyItem(spec.ticker, spec.market, spec.company_name, spec.provider,
        spec.source_id, kind, title, excerpt[:600] if excerpt else None, link,
        published, retrieved_at, row.get('language') or spec.language,
        row.get('external_id'), meta)


def _xml_rows(raw, spec):
    # Reject declarations before parsing, including UTF-16 representations.
    data = raw.decode('utf-8-sig')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', data, re.I) or '\x00' in data:
        raise CompanySourceError('UNSAFE_XML')
    root = ET.fromstring(data)
    if root.tag == 'rss':
        channel = root.find('channel')
        if channel is None: raise CompanySourceError('INVALID_FEED')
        nodes = channel.findall('item')
        rows = []
        for node in nodes:
            links = node.findall(ATOM + 'link') + node.findall(XHTML + 'link')
            rows.append({'title': node.findtext('title'), 'url': node.findtext('link'),
                'summary': node.findtext('description'), 'published': node.findtext('pubDate'),
                'external_id': node.findtext('guid'), 'language': node.get(XML_LANG) or channel.findtext('language') or spec.language,
                'categories': [c.text or '' for c in node.findall('category')],
                'translations': [l.get('href') for l in links if l.get('rel') == 'alternate' and l.get('hreflang') in ('ko', 'en')]})
        return rows
    if root.tag == ATOM + 'feed':
        rows = []
        for node in root.findall(ATOM + 'entry'):
            links = node.findall(ATOM + 'link')
            canonical = [l.get('href') for l in links if l.get('rel', 'alternate') == 'alternate' and not l.get('hreflang')]
            rows.append({'title': node.findtext(ATOM + 'title'), 'url': canonical[0] if len(canonical) == 1 else None,
                'summary': node.findtext(ATOM + 'summary'), 'published': node.findtext(ATOM + 'published'),
                'external_id': node.findtext(ATOM + 'id'), 'language': node.get(XML_LANG) or root.get(XML_LANG) or spec.language,
                'categories': [c.get('term', '') for c in node.findall(ATOM + 'category')],
                'translations': [l.get('href') for l in links if l.get('rel') == 'alternate' and l.get('hreflang') in ('ko', 'en')]})
        return rows
    raise CompanySourceError('INVALID_FEED')


class _StructuredHTML(HTMLParser):
    """Only JSON-LD metadata; arbitrary HTML text and document bodies are discarded."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []; self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type') == 'application/ld+json': self.current = []

    def handle_data(self, data):
        if self.current is not None: self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append(''.join(self.current)); self.current = None


def _html_rows(raw):
    p = _StructuredHTML(); p.feed(raw.decode('utf-8-sig')); p.close()
    rows = []
    for script in p.scripts:
        data = json.loads(script)
        nodes = data if isinstance(data, list) else data.get('@graph', [data]) if isinstance(data, dict) else []
        for node in nodes:
            if not isinstance(node, dict): raise CompanySourceError('INVALID_HTML_METADATA')
            schema = node.get('@type')
            if not isinstance(schema, str) or schema not in SCHEMA_KINDS: continue
            publisher = node.get('publisher', node.get('organizer'))
            issuer = publisher.get('name') if isinstance(publisher, dict) else publisher
            category = node.get('articleSection')
            rows.append({'title': node.get('headline') or node.get('name'), 'url': node.get('url'),
                'summary': node.get('description'), 'published': node.get('datePublished'),
                'external_id': node.get('identifier'), 'language': node.get('inLanguage'),
                'categories': [category] if category else [], 'kind': SCHEMA_KINDS[schema],
                'event_time': node.get('startDate') if schema == 'Event' else None, 'issuer': issuer})
    # Absence of supported metadata is NOT a successful empty scrape.
    if not rows: raise CompanySourceError('UNSUPPORTED_HTML_STRUCTURE')
    return rows


def parse(spec, raw, retrieved_at):
    try:
        instant(retrieved_at)
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES: raise CompanySourceError('RESPONSE_LIMIT')
        rows = _xml_rows(raw, spec) if spec.format == 'xml' else _html_rows(raw)
        if len(rows) > MAX_ITEMS: raise CompanySourceError('ITEM_LIMIT')
        items, skipped = [], []
        for index, row in enumerate(rows):
            try:
                item = _item(spec, row, retrieved_at)
                if item is None:
                    skipped.append(SkippedItem(index, 'COMPANY_SOURCE_FILING_COVERED_ELSEWHERE'))
                else:
                    items.append(item)
            except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as error:
                # Partial acceptance is limited to feeds. HTML remains unchanged.
                if spec.format != 'xml': raise
                code = str(error) if isinstance(error, CompanySourceError) else 'COMPANY_SOURCE_INVALID_ITEM'
                skipped.append(SkippedItem(index, code, error.date_value if isinstance(error, InvalidDate) else None))
        return ParseResult(tuple(items), tuple(skipped))
    except CompanySourceError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ET.ParseError, RecursionError, OverflowError):
        raise CompanySourceError('INVALID_RESPONSE') from None
