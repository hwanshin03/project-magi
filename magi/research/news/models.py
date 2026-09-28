"""Immutable, provider-neutral news. Classifications describe reporting, not trades."""
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple
from ..models import (Authority, SourceType, ResearchSource, EvidenceItem, FrozenMetadata,
                      freeze, instant, ticker as check_ticker, enum, ids, identity)
from ..security import text
from .urls import canonical_url


def controlled(name, names):
    return Enum(name, {v:v for v in names.split()}, type=str)

EventType = controlled('EventType', 'EARNINGS GUIDANCE PRODUCT CUSTOMER PARTNERSHIP M_AND_A MANAGEMENT REGULATORY LEGAL CAPITAL_RAISE BUYBACK DIVIDEND CONTRACT SUPPLY_CHAIN MACRO INDUSTRY ANALYST_ACTION RATING_CHANGE PRICE_MOVEMENT OTHER')
CatalystType = controlled('CatalystType', 'EARNINGS_BEAT EARNINGS_MISS GUIDANCE_RAISE GUIDANCE_CUT NEW_PRODUCT MAJOR_CUSTOMER_WIN CUSTOMER_LOSS REGULATORY_APPROVAL REGULATORY_RISK LEGAL_RISK MARGIN_EXPANSION MARGIN_PRESSURE DEMAND_STRENGTH DEMAND_WEAKNESS SUPPLY_IMPROVEMENT SUPPLY_DISRUPTION CAPEX_ACCELERATION CAPEX_SLOWDOWN MANAGEMENT_CHANGE M_AND_A BUYBACK DIVIDEND_CHANGE INDUSTRY_TAILWIND INDUSTRY_HEADWIND MACRO_TAILWIND MACRO_HEADWIND OTHER')
Direction = controlled('Direction', 'POSITIVE NEGATIVE MIXED NEUTRAL UNKNOWN')
TimeHorizon = controlled('TimeHorizon', 'IMMEDIATE SHORT_TERM MEDIUM_TERM LONG_TERM UNKNOWN')
VerificationStatus = controlled('VerificationStatus', 'CONFIRMED_OFFICIAL CORROBORATED SINGLE_SOURCE UNCONFIRMED RUMOR DISPUTED')
ContentKind = controlled('ContentKind', 'FACT REPORTED_CLAIM OPINION ANALYST_VIEW')
Relevance = controlled('Relevance', 'PRIMARY_SUBJECT DIRECTLY_RELATED INDUSTRY_RELATED MACRO_RELATED MENTION_ONLY')
Freshness = controlled('Freshness', 'BREAKING RECENT CURRENT AGING STALE UNKNOWN')
Sentiment = controlled('Sentiment', 'POSITIVE NEGATIVE NEUTRAL MIXED UNKNOWN')
NewsWarning = controlled('NewsWarning', 'NO_RECENT_NEWS NO_PRIMARY_SOURCE CONFLICTING_NEWS UNCONFIRMED_EVENT STALE_NEWS LOW_SOURCE_DIVERSITY MISSING_PUBLICATION_DATE BOUNDED_SELECTION')


def normalize_ticker(value):
    text(value)
    value=value.strip().upper();check_ticker(value)
    return value


def normalized_strings(values):
    if not isinstance(values,(tuple,list)): raise ValueError('Expected text sequence')
    return tuple(sorted({text(v) for v in values}))


def version(value):
    if type(value) is not int or value!=1: raise ValueError('Unsupported news schema')


def finish(obj, name, prefix):
    from ..serialization import encode
    payload={f.name:encode(getattr(obj,f.name)) for f in fields(obj) if f.name!=name}
    computed=identity('',prefix,payload)
    if getattr(obj,name) not in ('',computed): raise ValueError('News content ID does not match content')
    object.__setattr__(obj,name,computed)


def references(obj):
    for name in ('source_ids','evidence_ids'):
        object.__setattr__(obj,name,ids(getattr(obj,name)))
        if not getattr(obj,name): raise ValueError('News requires source and evidence references')


@dataclass(frozen=True)
class NewsClassification:
    """Explicit adapter/editor annotations; never inferred from headline keywords."""
    event_type: EventType = EventType.OTHER
    status: VerificationStatus = VerificationStatus.SINGLE_SOURCE
    content_kind: ContentKind = ContentKind.REPORTED_CLAIM
    claimant: Optional[str] = None
    event_namespace: Optional[str] = None
    event_key: Optional[str] = None
    event_time: Optional[datetime] = None
    assertion_key: Optional[str] = None
    assertion_value: Optional[str] = None
    catalyst_type: Optional[CatalystType] = None
    direction: Direction = Direction.UNKNOWN
    time_horizon: TimeHorizon = TimeHorizon.UNKNOWN
    confidence: Optional[Decimal] = None
    sentiment: Sentiment = Sentiment.UNKNOWN
    classification_source: Optional[str] = None
    market_moving: Optional[bool] = None
    relevance: FrozenMetadata = field(default_factory=FrozenMetadata)

    def __post_init__(self):
        for name,kind in [('event_type',EventType),('status',VerificationStatus),('content_kind',ContentKind),
                          ('direction',Direction),('time_horizon',TimeHorizon),('sentiment',Sentiment)]:
            enum(getattr(self,name),kind)
        if self.catalyst_type is not None: enum(self.catalyst_type,CatalystType)
        for name in ('claimant','event_namespace','event_key','assertion_key','assertion_value','classification_source'):
            text(getattr(self,name),optional=True)
        if bool(self.event_key)!=bool(self.event_namespace): raise ValueError('Event key needs an explicit namespace')
        if bool(self.assertion_key)!=bool(self.assertion_value): raise ValueError('Assertion needs key and value')
        if self.assertion_key and not self.event_key: raise ValueError('Assertion comparison requires event identity')
        instant(self.event_time,True)
        if self.confidence is not None and (not isinstance(self.confidence,Decimal) or not self.confidence.is_finite() or not 0<=self.confidence<=1):
            raise ValueError('Confidence must be Decimal in [0,1]')
        if self.market_moving is not None and type(self.market_moving) is not bool: raise ValueError('Invalid event flag')
        if (self.catalyst_type is not None or self.sentiment!=Sentiment.UNKNOWN or self.market_moving is not None or self.status!=VerificationStatus.SINGLE_SOURCE) and not self.classification_source:
            raise ValueError('Explicit classifications need provenance')
        if self.catalyst_type is None and (self.direction!=Direction.UNKNOWN or self.confidence is not None):
            raise ValueError('Directional annotation needs catalyst type')
        relevance=freeze(self.relevance)
        if not isinstance(relevance,FrozenMetadata): raise ValueError('Invalid relevance mapping')
        for k,v in relevance.items():
            if normalize_ticker(k)!=k: raise ValueError('Relevance ticker must be normalized')
            Relevance(v)
        object.__setattr__(self,'relevance',relevance)


@dataclass(frozen=True)
class NewsArticle:
    provider: str
    publisher: str
    title: str
    summary: Optional[str]
    url: str
    published_at: Optional[datetime]
    retrieved_at: datetime
    language: str
    tickers: Tuple[str,...]
    company_names: Tuple[str,...]
    source_type: SourceType = SourceType.NEWS
    authority: Authority = Authority.SECONDARY
    external_id: Optional[str] = None
    authors: Tuple[str,...] = ()
    metadata: FrozenMetadata = field(default_factory=FrozenMetadata)
    classification: NewsClassification = field(default_factory=NewsClassification)
    article_id: str = ''
    schema_version: int = 1

    def __post_init__(self):
        version(self.schema_version)
        for name in ('provider','publisher','title','language'): text(getattr(self,name))
        text(self.summary,optional=True);text(self.external_id,optional=True)
        if len(self.title)>1000 or (self.summary and len(self.summary)>4000): raise ValueError('Headline/summary limit exceeded')
        instant(self.retrieved_at);instant(self.published_at,True)
        if self.published_at and self.published_at>self.retrieved_at: raise ValueError('Publication after retrieval')
        enum(self.authority,Authority);enum(self.source_type,SourceType)
        if self.source_type==SourceType.MAGI_MEMORY and self.authority!=Authority.INTERNAL_HISTORY: raise ValueError('Internal memory authority required')
        if not isinstance(self.tickers,(tuple,list)): raise ValueError('Invalid tickers')
        object.__setattr__(self,'tickers',tuple(sorted({normalize_ticker(v) for v in self.tickers})))
        for name in ('company_names','authors'): object.__setattr__(self,name,normalized_strings(getattr(self,name)))
        object.__setattr__(self,'url',canonical_url(self.url))
        metadata=freeze(self.metadata)
        if not isinstance(metadata,FrozenMetadata): raise ValueError('Invalid article metadata')
        if any(k.lower() in ('body','full_text','article_body','raw_payload','html') for k in metadata): raise ValueError('Full article/raw payload storage is unsupported')
        object.__setattr__(self,'metadata',metadata)
        if not isinstance(self.classification,NewsClassification): raise ValueError('Invalid annotation')
        if not set(self.classification.relevance)<=set(self.tickers): raise ValueError('Relevance references unknown ticker')
        if self.classification.status==VerificationStatus.CONFIRMED_OFFICIAL and self.authority!=Authority.PRIMARY:
            raise ValueError('Official confirmation requires primary source')
        # Validate shared language and source semantics without making an I/O call.
        ResearchSource(self.source_type,self.authority,self.provider,self.title,self.publisher,self.retrieved_at,
                       url=self.url,published_at=self.published_at,language=self.language)
        finish(self,'article_id','N')


@dataclass(frozen=True)
class ResearchEvent:
    subject: str
    ticker: str
    event_type: EventType
    headline: str
    description: str
    occurred_at: Optional[datetime]
    published_at: Optional[datetime]
    source_ids: Tuple[str,...]
    evidence_ids: Tuple[str,...]
    status: VerificationStatus
    metadata: FrozenMetadata = field(default_factory=FrozenMetadata)
    event_id: str = ''
    schema_version: int = 1

    def __post_init__(self):
        version(self.schema_version);text(self.ticker);check_ticker(self.ticker)
        for v in (self.subject,self.headline,self.description): text(v)
        enum(self.event_type,EventType);enum(self.status,VerificationStatus)
        instant(self.occurred_at,True);instant(self.published_at,True);references(self)
        meta=freeze(self.metadata)
        if not isinstance(meta,FrozenMetadata): raise ValueError('Invalid event metadata')
        object.__setattr__(self,'metadata',meta);finish(self,'event_id','NE')


@dataclass(frozen=True)
class ResearchCatalyst:
    ticker: str
    catalyst_type: CatalystType
    direction: Direction
    time_horizon: TimeHorizon
    description: str
    evidence_ids: Tuple[str,...]
    source_ids: Tuple[str,...]
    confidence: Optional[Decimal]
    status: VerificationStatus
    created_at: datetime
    classification_source: str
    catalyst_id: str = ''
    schema_version: int = 1

    def __post_init__(self):
        version(self.schema_version);text(self.ticker);check_ticker(self.ticker);text(self.description);text(self.classification_source)
        for name,kind in [('catalyst_type',CatalystType),('direction',Direction),('time_horizon',TimeHorizon),('status',VerificationStatus)]: enum(getattr(self,name),kind)
        instant(self.created_at);references(self)
        if self.confidence is not None and (not isinstance(self.confidence,Decimal) or not self.confidence.is_finite() or not 0<=self.confidence<=1): raise ValueError('Invalid catalyst confidence')
        finish(self,'catalyst_id','NC')


@dataclass(frozen=True)
class EventCluster:
    ticker: str
    event_type: EventType
    event_time: Optional[datetime]
    source_ids: Tuple[str,...]
    evidence_ids: Tuple[str,...]
    primary_source_ids: Tuple[str,...]
    secondary_source_ids: Tuple[str,...]
    event_ids: Tuple[str,...]
    status: VerificationStatus
    cluster_id: str = ''
    schema_version: int = 1

    def __post_init__(self):
        version(self.schema_version);text(self.ticker);check_ticker(self.ticker);enum(self.event_type,EventType);enum(self.status,VerificationStatus)
        instant(self.event_time,True);references(self)
        for name in ('primary_source_ids','secondary_source_ids','event_ids'): object.__setattr__(self,name,ids(getattr(self,name)))
        if not self.event_ids or not set(self.primary_source_ids+self.secondary_source_ids)<=set(self.source_ids): raise ValueError('Invalid cluster references')
        if set(self.primary_source_ids)&set(self.secondary_source_ids): raise ValueError('Overlapping source authority')
        finish(self,'cluster_id','NG')


@dataclass(frozen=True)
class NewsEvidencePack:
    ticker: str
    subject: str
    created_at: datetime
    articles: Tuple[NewsArticle,...] = ()
    sources: Tuple[ResearchSource,...] = ()
    evidence_items: Tuple[EvidenceItem,...] = ()
    events: Tuple[ResearchEvent,...] = ()
    event_clusters: Tuple[EventCluster,...] = ()
    catalysts: Tuple[ResearchCatalyst,...] = ()
    selection_omissions: Tuple[str,...] = ()
    pack_id: str = ''
    schema_version: int = 1
    warnings: Tuple[NewsWarning,...] = field(init=False)
    coverage: FrozenMetadata = field(init=False)

    def __post_init__(self):
        version(self.schema_version);text(self.ticker);check_ticker(self.ticker);text(self.subject);instant(self.created_at)
        groups=[('articles',NewsArticle,'article_id'),('sources',ResearchSource,'source_id'),('evidence_items',EvidenceItem,'evidence_id'),
                ('events',ResearchEvent,'event_id'),('event_clusters',EventCluster,'cluster_id'),('catalysts',ResearchCatalyst,'catalyst_id')]
        for name,cls,key in groups:
            values=getattr(self,name)
            if not isinstance(values,(tuple,list)) or any(not isinstance(v,cls) for v in values): raise ValueError('Invalid news pack members')
            if len({getattr(v,key) for v in values})!=len(values): raise ValueError('Duplicate news IDs')
            object.__setattr__(self,name,tuple(sorted(values,key=lambda v:getattr(v,key))))
        object.__setattr__(self,'selection_omissions',ids(self.selection_omissions))
        from .validation import validate_pack, quality
        validate_pack(self)
        warnings,coverage=quality(self)
        object.__setattr__(self,'warnings',warnings);object.__setattr__(self,'coverage',freeze(coverage))
        finish(self,'pack_id','NP')

    def as_evidence_pack(self):
        from ..models import EvidencePack, EvidenceRelation, RelationKind
        relations=tuple(EvidenceRelation(RelationKind.CONFLICTING,c.evidence_ids,'Explicit conflicting news reports')
                        for c in self.event_clusters if c.status==VerificationStatus.DISPUTED and len(c.evidence_ids)>1)
        return EvidencePack(self.subject,self.created_at,self.sources,self.evidence_items,ticker=self.ticker,relations=relations)
