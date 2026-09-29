"""Bounded immutable first-party records; logical identity excludes retrieval time."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from ..models import FrozenMetadata, freeze, identity, instant, enum
from ..security import text
from .catalog import source_spec, official_url, CompanySourceError
from .compatibility import normalize_language


class OfficialItemType(str, Enum):
    PRESS_RELEASE = 'PRESS_RELEASE'
    EARNINGS_RELEASE = 'EARNINGS_RELEASE'
    IR_PRESENTATION = 'IR_PRESENTATION'
    IR_EVENT = 'IR_EVENT'
    COMPANY_NOTICE = 'COMPANY_NOTICE'
    PRODUCT_ANNOUNCEMENT = 'PRODUCT_ANNOUNCEMENT'
    CORPORATE_NEWS = 'CORPORATE_NEWS'
    DIVIDEND_NOTICE = 'DIVIDEND_NOTICE'
    SHAREHOLDER_NOTICE = 'SHAREHOLDER_NOTICE'
    OTHER = 'OTHER'


@dataclass(frozen=True)
class OfficialCompanyItem:
    ticker: str
    market: str
    company_name: str
    provider: str
    source_name: str
    item_type: OfficialItemType
    title: str
    summary: Optional[str]
    url: str
    published_at: Optional[datetime]
    retrieved_at: datetime
    language: str
    external_id: Optional[str] = None
    metadata: FrozenMetadata = field(default_factory=FrozenMetadata)
    item_id: str = ''

    def __post_init__(self):
        spec = source_spec(self.source_name)
        if (self.ticker, self.market, self.company_name, self.provider) != (
                spec.ticker, spec.market, spec.company_name, spec.provider):
            raise CompanySourceError('IDENTITY_MISMATCH')
        try:
            object.__setattr__(self, 'language', normalize_language(self.language))
        except ValueError:
            raise CompanySourceError('LANGUAGE_MISMATCH') from None
        if self.language not in ('en', 'ko', 'en-US', 'ko-KR') or (self.provider == 'nvidia_official' and not self.language.startswith('en')):
            raise CompanySourceError('LANGUAGE_MISMATCH')
        enum(self.item_type, OfficialItemType)
        text(self.title); text(self.summary, optional=True); text(self.external_id, optional=True)
        if len(self.title) > 500 or self.summary is not None and len(self.summary) > 600:
            raise CompanySourceError('TEXT_LIMIT')
        if self.external_id is not None and len(self.external_id) > 256:
            raise CompanySourceError('ID_LIMIT')
        object.__setattr__(self, 'url', official_url(self.url, self.provider))
        instant(self.published_at, True); instant(self.retrieved_at)
        if self.published_at and self.published_at > self.retrieved_at:
            raise CompanySourceError('INVALID_DATE')
        meta = freeze(self.metadata)
        allowed = {'format', 'category', 'translation_urls', 'event_time', 'publication_date', 'excerpt_truncated'}
        if not isinstance(meta, FrozenMetadata) or set(meta) - allowed:
            raise CompanySourceError('UNSUPPORTED_METADATA')
        for k in ('format', 'category', 'event_time', 'publication_date'):
            if k in meta and (not isinstance(meta[k], str) or len(meta[k]) > 128):
                raise CompanySourceError('INVALID_METADATA')
        if 'excerpt_truncated' in meta and type(meta['excerpt_truncated']) is not bool:
            raise CompanySourceError('INVALID_METADATA')
        links = meta.get('translation_urls', ())
        if not isinstance(links, tuple) or len(links) > 4:
            raise CompanySourceError('INVALID_TRANSLATION')
        for link in links:
            official_url(link, self.provider)
        if links:
            meta = freeze({**meta, 'translation_urls': tuple(sorted({official_url(link, self.provider) for link in links}))})
        if meta.get('event_time'):
            instant(datetime.fromisoformat(meta['event_time']))
        object.__setattr__(self, 'metadata', meta)
        computed = identity('', 'OCI', (self.provider, self.ticker, self.market, self.url, self.language))
        if self.item_id not in ('', computed):
            raise CompanySourceError('INVALID_ID')
        object.__setattr__(self, 'item_id', computed)
