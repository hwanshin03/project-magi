"""Immutable research snapshots. Authority and claim status are not truth scores."""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
import re
from typing import Optional, Tuple
from .security import text, safe_text, url

SCHEMA_VERSION = 1


class SourceType(str, Enum):
    SEC_FILING='SEC_FILING'
    DART_FILING='DART_FILING'
    COMPANY_IR='COMPANY_IR'
    EARNINGS_RELEASE='EARNINGS_RELEASE'
    EARNINGS_CALL='EARNINGS_CALL'
    MARKET_DATA='MARKET_DATA'
    MACRO_DATA='MACRO_DATA'
    NEWS='NEWS'
    ANALYST_RESEARCH='ANALYST_RESEARCH'
    WEB_SOURCE='WEB_SOURCE'
    MAGI_MEMORY='MAGI_MEMORY'


class Authority(str, Enum):
    PRIMARY='PRIMARY'
    AUTHORITATIVE_DATA='AUTHORITATIVE_DATA'
    SECONDARY='SECONDARY'
    TERTIARY='TERTIARY'
    INTERNAL_HISTORY='INTERNAL_HISTORY'


class Category(str, Enum):
    FINANCIAL='FINANCIAL'
    VALUATION='VALUATION'
    GROWTH='GROWTH'
    PROFITABILITY='PROFITABILITY'
    BALANCE_SHEET='BALANCE_SHEET'
    CASH_FLOW='CASH_FLOW'
    GUIDANCE='GUIDANCE'
    CATALYST='CATALYST'
    RISK='RISK'
    MACRO='MACRO'
    INDUSTRY='INDUSTRY'
    COMPETITION='COMPETITION'
    MANAGEMENT='MANAGEMENT'
    MARKET_PRICE='MARKET_PRICE'
    MARKET_VOLUME='MARKET_VOLUME'
    SENTIMENT='SENTIMENT'
    REGULATORY='REGULATORY'
    OTHER='OTHER'


class ClaimStatus(str, Enum):
    SUPPORTED='SUPPORTED'
    PARTIALLY_SUPPORTED='PARTIALLY_SUPPORTED'
    UNSUPPORTED='UNSUPPORTED'
    CONFLICTED='CONFLICTED'


class RelationKind(str, Enum):
    CORROBORATING='CORROBORATING'
    CONFLICTING='CONFLICTING'
    UNRESOLVED='UNRESOLVED'


class WarningCode(str, Enum):
    NO_PRIMARY_SOURCE='NO_PRIMARY_SOURCE'
    STALE_SOURCE='STALE_SOURCE'
    CONFLICTING_EVIDENCE='CONFLICTING_EVIDENCE'
    MISSING_FINANCIALS='MISSING_FINANCIALS'
    MISSING_MARKET_DATA='MISSING_MARKET_DATA'
    MISSING_PUBLICATION_DATE='MISSING_PUBLICATION_DATE'


@dataclass(frozen=True)
class FrozenMetadata(Mapping):
    entries: tuple = ()

    def __post_init__(self):
        pairs = tuple(self.entries)
        if any(not isinstance(pair, (tuple,list)) or len(pair)!=2 for pair in pairs):
            raise ValueError('Metadata requires key/value pairs')
        keys = [text(k) for k,v in pairs]
        if len(keys)!=len(set(keys)):
            raise ValueError('Duplicate metadata key')
        for k in keys:
            if re.search(r'(?i)secret|token|password|authorization|api.?key|crtfc_key|client.?id|credential|\.env',k):
                raise ValueError('Sensitive metadata key')
        object.__setattr__(self,'entries',tuple(sorted((k,freeze(v)) for k,v in pairs)))

    def __getitem__(self,key):
        for k,v in self.entries:
            if k==key: return v
        raise KeyError(key)

    def __iter__(self):
        return (k for k,v in self.entries)

    def __len__(self):
        return len(self.entries)


def freeze(value):
    if isinstance(value,FrozenMetadata): return value
    if isinstance(value,Mapping): return FrozenMetadata(tuple(value.items()))
    if isinstance(value,(list,tuple)): return tuple(freeze(v) for v in value)
    if isinstance(value,str): return safe_text(value)
    if value is None or type(value) in (bool,int): return value
    if isinstance(value,Decimal) and value.is_finite(): return value
    raise ValueError('Metadata must be structured JSON-like data; use Decimal, not float')


def instant(value, optional=False):
    if value is None and optional: return
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware datetime required')


def ticker(value):
    if value is not None and (not isinstance(value,str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,31}',value)):
        raise ValueError('Ticker must already be normalized uppercase')


def common(subject,tick):
    text(subject);ticker(tick)


def enum(value,kind):
    if not isinstance(value,kind): raise ValueError('Invalid controlled enum')


def identity(value,prefix,payload):
    if not isinstance(value,str): raise ValueError('Identifier must be text')
    if value:
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value):
            raise ValueError('Invalid identifier')
        safe_text(value)
        return value
    return prefix+'_'+hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def ids(values):
    if not isinstance(values,(tuple,list)) or any(not isinstance(v,str) or not v for v in values):
        raise ValueError('Expected evidence identifiers')
    if len(values)!=len(set(values)): raise ValueError('Duplicate evidence reference')
    for v in values: identity(v,'',None)
    return tuple(sorted(values))


@dataclass(frozen=True)
class ResearchSource:
    source_type: SourceType
    authority: Authority
    provider: str
    title: str
    publisher: str
    retrieved_at: datetime
    source_id: str = ''
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    document_type: Optional[str] = None
    ticker: Optional[str] = None
    company_name: Optional[str] = None
    market: Optional[str] = None
    language: Optional[str] = None
    external_id: Optional[str] = None
    metadata: FrozenMetadata = field(default_factory=FrozenMetadata)

    def __post_init__(self):
        enum(self.source_type,SourceType);enum(self.authority,Authority)
        if self.source_type==SourceType.MAGI_MEMORY and self.authority!=Authority.INTERNAL_HISTORY:
            raise ValueError('MAGI memory must be labeled internal history')
        for v in (self.provider,self.title,self.publisher): text(v)
        for v in (self.document_type,self.company_name,self.external_id): text(v,optional=True)
        instant(self.retrieved_at);instant(self.published_at,True);ticker(self.ticker);url(self.url)
        if self.published_at and self.published_at>self.retrieved_at: raise ValueError('Publication is after retrieval')
        if self.market is not None and (not isinstance(self.market,str) or not re.fullmatch('[A-Z][A-Z0-9_-]{0,15}',self.market)): raise ValueError('Invalid market')
        if self.language is not None and (not isinstance(self.language,str) or not re.fullmatch('[a-z]{2,3}(?:-[A-Z]{2})?',self.language)): raise ValueError('Invalid language')
        if not isinstance(self.metadata,Mapping): raise ValueError('Metadata requires a mapping')
        object.__setattr__(self,'metadata',freeze(self.metadata))
        # Retrieval time is not source identity. Versions at one URL share identity
        # but remain independent snapshot objects; dedup rejects conflicting content.
        key = ('external',self.external_id) if self.external_id else ('url',self.url) if self.url else (
            'document',self.publisher,self.title,self.published_at,self.document_type,self.ticker)
        object.__setattr__(self,'source_id',identity(self.source_id,'S',(self.source_type.value,self.provider,key)))


@dataclass(frozen=True)
class SourceLocator:
    page: Optional[int] = None
    section: Optional[str] = None
    filing_item: Optional[str] = None
    table: Optional[str] = None
    paragraph: Optional[str] = None
    xbrl_concept: Optional[str] = None
    timestamp: Optional[str] = None  # Media/document position, not publication time.
    article_section: Optional[str] = None

    def __post_init__(self):
        if self.page is not None and (type(self.page) is not int or self.page<1): raise ValueError('Invalid page')
        for f in fields(self):
            if f.name!='page': text(getattr(self,f.name),optional=True)


@dataclass(frozen=True)
class EvidenceItem:
    source_id: str
    subject: str
    category: Category
    statement: str
    retrieved_at: datetime
    evidence_id: str = ''
    ticker: Optional[str] = None
    value: object = None
    unit: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    as_of: Optional[datetime] = None
    source_locator: Optional[SourceLocator] = None
    extraction_confidence: Optional[Decimal] = None
    metadata: FrozenMetadata = field(default_factory=FrozenMetadata)

    def __post_init__(self):
        if not isinstance(self.metadata,Mapping): raise ValueError('Metadata requires a mapping')
        object.__setattr__(self,'metadata',freeze(self.metadata))
        identity(self.source_id,'',None)
        if not self.source_id: raise ValueError('Source reference required')
        common(self.subject,self.ticker);enum(self.category,Category);text(self.statement)
        instant(self.retrieved_at);instant(self.as_of,True)
        if self.value is not None:
            if isinstance(self.value,str): text(self.value)
            elif type(self.value) is int: pass
            elif not isinstance(self.value,Decimal) or not self.value.is_finite(): raise ValueError('Use Decimal-safe evidence values')
        if self.unit is not None and (not isinstance(self.unit,str) or not re.fullmatch(r'[A-Za-z%][A-Za-z0-9%_./ -]{0,39}',self.unit)):
            raise ValueError('Invalid unit')
        if self.unit is not None: safe_text(self.unit)
        for v in (self.period_start,self.period_end):
            if v is not None and type(v) is not date: raise ValueError('Period requires a date')
        if self.period_start and self.period_end and self.period_start>self.period_end: raise ValueError('Invalid period')
        if self.source_locator is not None and not isinstance(self.source_locator,SourceLocator): raise ValueError('Invalid locator')
        if self.extraction_confidence is not None and (not isinstance(self.extraction_confidence,Decimal)
                or not self.extraction_confidence.is_finite() or not 0<=self.extraction_confidence<=1):
            raise ValueError('Confidence must be Decimal in [0,1]')
        payload=[getattr(self,f.name) for f in fields(self) if f.name not in ('evidence_id','retrieved_at','extraction_confidence')]
        object.__setattr__(self,'evidence_id',identity(self.evidence_id,'E',payload))


@dataclass(frozen=True)
class ResearchClaim:
    subject: str
    claim_text: str
    category: Category
    created_by: str
    created_at: datetime
    claim_id: str = ''
    ticker: Optional[str] = None
    supporting_evidence_ids: Tuple[str,...] = ()
    contrary_evidence_ids: Tuple[str,...] = ()
    unresolved_evidence_ids: Tuple[str,...] = ()
    status: ClaimStatus = field(init=False)

    def __post_init__(self):
        common(self.subject,self.ticker);text(self.claim_text);text(self.created_by)
        enum(self.category,Category);instant(self.created_at)
        for name in ('supporting_evidence_ids','contrary_evidence_ids','unresolved_evidence_ids'):
            object.__setattr__(self,name,ids(getattr(self,name)))
        if set(self.supporting_evidence_ids)&set(self.contrary_evidence_ids): raise ValueError('Same evidence cannot both support and oppose one claim')
        status = (ClaimStatus.CONFLICTED if self.supporting_evidence_ids and self.contrary_evidence_ids else
                  ClaimStatus.PARTIALLY_SUPPORTED if self.supporting_evidence_ids and self.unresolved_evidence_ids else
                  ClaimStatus.SUPPORTED if self.supporting_evidence_ids else ClaimStatus.UNSUPPORTED)
        object.__setattr__(self,'status',status)
        payload=[getattr(self,f.name) for f in fields(self) if f.name not in ('claim_id','status')]
        object.__setattr__(self,'claim_id',identity(self.claim_id,'C',payload))


@dataclass(frozen=True)
class EvidenceRelation:
    kind: RelationKind
    evidence_ids: Tuple[str,...]
    note: Optional[str] = None

    def __post_init__(self):
        enum(self.kind,RelationKind);text(self.note,optional=True)
        object.__setattr__(self,'evidence_ids',ids(self.evidence_ids))
        if len(self.evidence_ids)<(1 if self.kind==RelationKind.UNRESOLVED else 2): raise ValueError('Relation needs evidence')


COVERAGE = {
    'financial': {Category.FINANCIAL,Category.PROFITABILITY,Category.BALANCE_SHEET,Category.CASH_FLOW},
    'valuation': {Category.VALUATION}, 'market': {Category.MARKET_PRICE,Category.MARKET_VOLUME},
    'macro': {Category.MACRO}, 'risk': {Category.RISK}, 'regulatory': {Category.REGULATORY},
}


@dataclass(frozen=True)
class EvidencePack:
    subject: str
    created_at: datetime
    sources: Tuple[ResearchSource,...] = ()
    evidence_items: Tuple[EvidenceItem,...] = ()
    claims: Tuple[ResearchClaim,...] = ()
    ticker: Optional[str] = None
    market: Optional[str] = None
    relations: Tuple[EvidenceRelation,...] = ()
    stale_after_days: int = 365
    schema_version: int = SCHEMA_VERSION
    pack_id: str = ''
    warnings: Tuple[WarningCode,...] = field(init=False)
    coverage: FrozenMetadata = field(init=False)

    def __post_init__(self):
        common(self.subject,self.ticker);instant(self.created_at)
        if self.market is not None and (not isinstance(self.market,str) or not re.fullmatch('[A-Z][A-Z0-9_-]{0,15}',self.market)): raise ValueError('Invalid market')
        if type(self.schema_version) is not int or self.schema_version!=SCHEMA_VERSION: raise ValueError('Unsupported research schema')
        if type(self.stale_after_days) is not int or not 0<=self.stale_after_days<=36500: raise ValueError('Invalid stale policy')
        for name,cls,key in [('sources',ResearchSource,'source_id'),('evidence_items',EvidenceItem,'evidence_id'),('claims',ResearchClaim,'claim_id')]:
            values=getattr(self,name)
            if not isinstance(values,(tuple,list)) or any(not isinstance(v,cls) for v in values): raise ValueError('Invalid pack members')
            if len({getattr(v,key) for v in values})!=len(values): raise ValueError('Duplicate IDs')
            object.__setattr__(self,name,tuple(sorted(values,key=lambda v:getattr(v,key))))
        if not isinstance(self.relations,(tuple,list)) or any(not isinstance(r,EvidenceRelation) for r in self.relations): raise ValueError('Invalid relations')
        if len(set(self.relations))!=len(self.relations): raise ValueError('Duplicate relations')
        object.__setattr__(self,'relations',tuple(sorted(self.relations,key=lambda r:(r.kind.value,r.evidence_ids,r.note or ''))))
        source_map={s.source_id:s for s in self.sources};evidence_map={e.evidence_id:e for e in self.evidence_items}
        for s in self.sources:
            if s.retrieved_at>self.created_at: raise ValueError('Source retrieved after snapshot')
        for e in self.evidence_items:
            if e.source_id not in source_map: raise ValueError('Missing evidence source')
            if e.retrieved_at>self.created_at: raise ValueError('Evidence retrieved after snapshot')
        for c in self.claims:
            if c.created_at>self.created_at: raise ValueError('Claim created after snapshot')
            for ref in (*c.supporting_evidence_ids,*c.contrary_evidence_ids,*c.unresolved_evidence_ids):
                if ref not in evidence_map: raise ValueError('Missing claim evidence')
        for r in self.relations:
            if any(ref not in evidence_map for ref in r.evidence_ids): raise ValueError('Missing relation evidence')
        coverage={name:any(e.category in categories for e in self.evidence_items) for name,categories in COVERAGE.items()}
        coverage['news']=any(source_map[e.source_id].source_type==SourceType.NEWS for e in self.evidence_items)
        object.__setattr__(self,'coverage',FrozenMetadata(tuple(coverage.items())))
        used={e.source_id for e in self.evidence_items}
        warnings=[]
        if not any(s.authority==Authority.PRIMARY and s.source_id in used for s in self.sources): warnings.append(WarningCode.NO_PRIMARY_SOURCE)
        if any(s.published_at is None for s in self.sources): warnings.append(WarningCode.MISSING_PUBLICATION_DATE)
        if any(s.published_at and self.created_at-s.published_at>timedelta(days=self.stale_after_days) for s in self.sources): warnings.append(WarningCode.STALE_SOURCE)
        if any(c.contrary_evidence_ids for c in self.claims) or any(r.kind==RelationKind.CONFLICTING for r in self.relations): warnings.append(WarningCode.CONFLICTING_EVIDENCE)
        if not coverage['financial']: warnings.append(WarningCode.MISSING_FINANCIALS)
        if not coverage['market']: warnings.append(WarningCode.MISSING_MARKET_DATA)
        object.__setattr__(self,'warnings',tuple(sorted(warnings,key=lambda w:w.value)))
        # Hash full versioned content, including source/evidence text, not IDs alone.
        from .serialization import encode
        payload={f.name:encode(getattr(self,f.name)) for f in fields(self) if f.name!='pack_id'}
        object.__setattr__(self,'pack_id',identity(self.pack_id,'P',payload))

    def source(self,source_id):
        return next((s for s in self.sources if s.source_id==source_id),None)

    def evidence(self,evidence_id):
        return next((e for e in self.evidence_items if e.evidence_id==evidence_id),None)
