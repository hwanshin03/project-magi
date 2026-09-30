"""Closed source contracts for the live-verified regulatory milestone."""
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit, quote, unquote
import re
import unicodedata
from ..security import url as safe_url


class RegulatoryError(ValueError):
    def __init__(self, code):
        super().__init__('REGULATORY_' + code)


@dataclass(frozen=True)
class RegulatorySourceSpec:
    key: str
    agency: str
    jurisdiction: str
    agency_slug: str | None
    endpoint: str
    format: str
    language: str


# Only the observed C1 correction prefix is accepted; it conveys no relationship.
FEDERAL_REGISTER_DOCUMENT_NUMBER = r'(?:C1-)?[0-9]{4}-[0-9]{4,6}'

SOURCES = MappingProxyType({
    'bis_rules': RegulatorySourceSpec('bis_rules', 'BIS', 'US', 'industry-and-security-bureau',
        'https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau&per_page=20&order=newest', 'json', 'en-US'),
    'sec_rules': RegulatorySourceSpec('sec_rules', 'SEC', 'US', 'securities-and-exchange-commission',
        'https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=securities-and-exchange-commission&per_page=20&order=newest', 'json', 'en-US'),
    'fsc_releases': RegulatorySourceSpec('fsc_releases', 'FSC', 'KR', None,
        'https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111', 'xml', 'ko-KR'),
})


def source_spec(key):
    try: return SOURCES[key]
    except (KeyError, TypeError): raise RegulatoryError('UNSUPPORTED_SOURCE') from None


def item_url(value, source_key):
    spec = source_spec(source_key)
    try:
        if not isinstance(value,str) or len(value)>2048: raise ValueError()
        safe_url(value); p = urlsplit(value)
        host = 'www.fsc.go.kr' if spec.agency == 'FSC' else 'www.federalregister.gov'
        if p.scheme != 'https' or p.netloc != host or p.query or p.fragment:
            raise ValueError()
        if spec.agency == 'FSC':
            valid = re.fullmatch(r'/(?:no010101|po040301)/[0-9]+/?', p.path)
        else:
            valid = re.fullmatch(r'/documents/[0-9]{4}/[0-9]{2}/[0-9]{2}/' + FEDERAL_REGISTER_DOCUMENT_NUMBER + r'/[^/]+/?', p.path)
        if not valid: raise ValueError()
        segments = []
        for part in p.path.split('/'):
            if any(int(v, 16) < 128 for v in re.findall(r'%([A-Fa-f0-9]{2})', part)):
                raise ValueError()
            decoded = unquote(part, encoding='utf-8', errors='strict')
            for check in (decoded, unicodedata.normalize('NFKC', decoded)):
                if check in ('.', '..') or any(c in '/\\?#:@%' or c.isspace() or unicodedata.category(c).startswith('C') for c in check):
                    raise ValueError()
            segments.append(quote(decoded, safe="!$&'()*+,-.;=_~"))
        return urlunsplit(('https', host, '/'.join(segments), '', ''))
    except (ValueError, TypeError, AttributeError):
        raise RegulatoryError('UNSAFE_ITEM_URL') from None
