"""Closed source registry. Endpoint format compatibility awaits controlled verification."""
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit, unquote, quote
import re
import unicodedata

from ..security import url as safe_url


class CompanySourceError(ValueError):
    """Only static codes cross the provider boundary; never response/URL text."""
    def __init__(self, code):
        super().__init__('COMPANY_SOURCE_' + code)


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    provider: str
    ticker: str
    market: str
    company_name: str
    endpoint: str
    format: str
    language: str
    item_type: str


def _spec(key, company, endpoint, format, language, kind):
    provider, ticker, market, name = company
    return SourceSpec(key, provider, ticker, market, name, endpoint, format, language, kind)


NVIDIA = ('nvidia_official', 'NVDA', 'US', 'NVIDIA')
SAMSUNG = ('samsung_electronics_official', '005930', 'KR', 'Samsung Electronics')
_SPECS = (
    _spec('nvidia_newsroom', NVIDIA, 'https://nvidianews.nvidia.com/releases.xml', 'xml', 'en', 'PRESS_RELEASE'),
    _spec('nvidia_ir_releases', NVIDIA, 'https://investor.nvidia.com/news-and-events/press-releases/default.aspx', 'html', 'en', 'PRESS_RELEASE'),
    _spec('nvidia_ir_events', NVIDIA, 'https://investor.nvidia.com/events-and-presentations/default.aspx', 'html', 'en', 'IR_EVENT'),
    _spec('nvidia_ir_results', NVIDIA, 'https://investor.nvidia.com/financial-info/financial-reports-and-sec-filings/default.aspx', 'html', 'en', 'IR_PRESENTATION'),
    _spec('samsung_newsroom_en', SAMSUNG, 'https://news.samsung.com/global/feed', 'xml', 'en', 'CORPORATE_NEWS'),
    _spec('samsung_newsroom_ko', SAMSUNG, 'https://news.samsung.com/kr/feed', 'xml', 'ko', 'CORPORATE_NEWS'),
    _spec('samsung_ir_earnings_en', SAMSUNG, 'https://www.samsung.com/global/ir/financial-information/earnings-release/', 'html', 'en', 'EARNINGS_RELEASE'),
    _spec('samsung_ir_earnings_ko', SAMSUNG, 'https://www.samsung.com/sec/ir/financial-information/earnings-release/', 'html', 'ko', 'EARNINGS_RELEASE'),
    _spec('samsung_ir_notices_en', SAMSUNG, 'https://www.samsung.com/global/ir/stock-information/announcements/', 'html', 'en', 'COMPANY_NOTICE'),
    _spec('samsung_ir_notices_ko', SAMSUNG, 'https://www.samsung.com/sec/ir/stock-information/announcements/', 'html', 'ko', 'COMPANY_NOTICE'),
)
SOURCES = MappingProxyType({s.source_id: s for s in _SPECS})

# Item links can be retained, never fetched. The transport accepts exact endpoints only.
ITEM_PATHS = MappingProxyType({
    'nvidia_official': {
        'nvidianews.nvidia.com': ('/news/', '/releases/'),
        'investor.nvidia.com': ('/news-and-events/press-releases/', '/events-and-presentations/'),
    },
    'samsung_electronics_official': {
        'news.samsung.com': ('/global/', '/kr/'),
        'www.samsung.com': ('/global/ir/', '/sec/ir/'),
    },
})


def source_spec(source_id):
    try:
        return SOURCES[source_id]
    except (KeyError, TypeError):
        raise CompanySourceError('UNSUPPORTED_SOURCE') from None


def official_url(value, provider, *, endpoint=False):
    try:
        safe_url(value)
        p = urlsplit(value)
        # Request endpoints remain exact. Item paths may contain encoded UTF-8
        # Unicode, but never encoded ASCII syntax or nested percent encoding.
        if (p.scheme != 'https' or p.port not in (None, 443) or p.query or p.fragment
                or any(x in ('.', '..') for x in p.path.split('/'))
                or '//' in p.path or not re.fullmatch(r'[a-z0-9.-]+', p.netloc)):
            raise ValueError()
        if endpoint:
            if not any(s.endpoint == value and s.provider == provider for s in SOURCES.values()):
                raise ValueError()
        else:
            hosts = ITEM_PATHS[provider]
            if p.hostname not in hosts:
                raise CompanySourceError('UNSUPPORTED_ITEM_HOST')
            prefixes = hosts[p.hostname]
            if not any(p.path.startswith(prefix) and len(p.path) > len(prefix) for prefix in prefixes):
                raise ValueError()
            segments = []
            for segment in p.path.split('/'):
                if any(int(m, 16) < 128 for m in re.findall(r'%([0-9A-Fa-f]{2})', segment)):
                    raise ValueError()
                decoded = unquote(segment, encoding='utf-8', errors='strict')
                for candidate in (decoded, unicodedata.normalize('NFKC', decoded)):
                    if (candidate in ('.', '..') or any(c in '/\\?#:@%' or c.isspace()
                            or unicodedata.category(c).startswith('C') for c in candidate)):
                        raise ValueError()
                segments.append(quote(decoded, safe="!$&'()*+,-.;=_~"))
            value = urlunsplit((p.scheme, p.netloc, '/'.join(segments), '', ''))
            if any(value == s.endpoint for s in SOURCES.values()) or p.path.rstrip('/').endswith(('/feed', '/default.aspx')):
                raise ValueError()
            # SEC/DART and group-company documents have separate ingestion paths.
            if re.search(r'(?i)sec-filings|/sec-filing|samsung-sdi|samsung-biologics|electro-mechanics', p.path):
                raise ValueError()
        return value
    except CompanySourceError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise CompanySourceError('UNSAFE_URL') from None
