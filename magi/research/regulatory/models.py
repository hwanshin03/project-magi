"""Immutable publications and events. Date precision and legal status are explicit."""
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from enum import Enum
from ..models import FrozenMetadata, freeze, instant, enum, identity, ids
from ..security import text
from .catalog import SOURCES, source_spec, item_url, RegulatoryError


class RegulatoryType(str, Enum):
    RULE_PROPOSAL = 'RULE_PROPOSAL'
    FINAL_RULE = 'FINAL_RULE'
    INTERIM_FINAL_RULE = 'INTERIM_FINAL_RULE'
    GUIDANCE = 'GUIDANCE'
    ENFORCEMENT = 'ENFORCEMENT'
    ANTITRUST = 'ANTITRUST'
    EXPORT_CONTROL = 'EXPORT_CONTROL'
    MONETARY_POLICY = 'MONETARY_POLICY'
    SANCTIONS = 'SANCTIONS'
    INDUSTRIAL_POLICY = 'INDUSTRIAL_POLICY'
    PUBLIC_NOTICE = 'PUBLIC_NOTICE'
    OTHER = 'OTHER'


class RegulatoryStatus(str, Enum):
    PROPOSED = 'PROPOSED'
    FINAL = 'FINAL'
    INTERIM = 'INTERIM'
    EFFECTIVE = 'EFFECTIVE'
    SCHEDULED = 'SCHEDULED'
    WITHDRAWN = 'WITHDRAWN'
    SUPERSEDED = 'SUPERSEDED'
    GUIDANCE = 'GUIDANCE'
    ENFORCEMENT_ACTION = 'ENFORCEMENT_ACTION'
    UNKNOWN = 'UNKNOWN'


def bounded(value, limit, optional=False):
    text(value, optional=optional)
    if value is not None and len(value) > limit: raise RegulatoryError('TEXT_LIMIT')


def regulatory_date(value):
    if value is None: return
    if isinstance(value, datetime): instant(value)
    elif type(value) is not date: raise RegulatoryError('INVALID_DATE')


def labels(values):
    if not isinstance(values, (tuple, list)) or len(values) > 20:
        raise RegulatoryError('INVALID_LABELS')
    for value in values: bounded(value, 160)
    return tuple(sorted(set(values)))


def finish(obj, name, prefix, excluded=()):
    payload = {f.name: getattr(obj, f.name) for f in fields(obj) if f.name not in (name, *excluded)}
    computed = identity('', prefix, payload)
    if getattr(obj, name) not in ('', computed): raise RegulatoryError('INVALID_ID')
    object.__setattr__(obj, name, computed)


@dataclass(frozen=True)
class RegulatoryItem:
    source_key: str
    agency: str
    jurisdiction: str
    title: str
    summary: str | None
    url: str
    published_at: date | datetime | None
    effective_at: date | datetime | None
    retrieved_at: datetime
    language: str
    regulatory_type: RegulatoryType = RegulatoryType.OTHER
    status: RegulatoryStatus = RegulatoryStatus.UNKNOWN
    legal_reference: str | None = None
    docket_or_reference: str | None = None
    affected_industries: tuple = ()
    affected_entities: tuple = ()
    metadata: FrozenMetadata = field(default_factory=FrozenMetadata)
    item_id: str = ''

    def __post_init__(self):
        spec = source_spec(self.source_key)
        if (self.agency, self.jurisdiction) != (spec.agency, spec.jurisdiction):
            raise RegulatoryError('IDENTITY_MISMATCH')
        if self.language not in ('en-US', 'ko-KR') or (self.jurisdiction == 'US' and self.language != 'en-US'):
            raise RegulatoryError('INVALID_LANGUAGE')
        bounded(self.title, 500); bounded(self.summary, 600, True)
        bounded(self.legal_reference, 500, True); bounded(self.docket_or_reference, 200, True)
        object.__setattr__(self, 'url', item_url(self.url, self.source_key))
        instant(self.retrieved_at); regulatory_date(self.published_at); regulatory_date(self.effective_at)
        if isinstance(self.published_at, datetime):
            if self.published_at > self.retrieved_at: raise RegulatoryError('FUTURE_PUBLICATION')
        elif self.published_at and self.published_at > self.retrieved_at.astimezone(timezone.utc).date():
            raise RegulatoryError('FUTURE_PUBLICATION')
        enum(self.regulatory_type, RegulatoryType); enum(self.status, RegulatoryStatus)
        for name in ('affected_industries', 'affected_entities'):
            object.__setattr__(self, name, labels(getattr(self, name)))
        meta = freeze(self.metadata)
        allowed = {'classification_explicit','source_record_id','source_type_label','source_status_label',
                   'rule_references','related_references','translation_urls','excerpt_truncated'}
        if not isinstance(meta, FrozenMetadata) or set(meta) - allowed:
            raise RegulatoryError('INVALID_METADATA')
        for key in ('classification_explicit', 'excerpt_truncated'):
            if key in meta and type(meta[key]) is not bool: raise RegulatoryError('INVALID_METADATA')
        for key in ('source_record_id','source_type_label','source_status_label'):
            if key in meta: bounded(meta[key], 200)
        for key in ('rule_references',):
            if key in meta: labels(meta[key])
        if 'related_references' in meta:
            if not isinstance(meta['related_references'], tuple) or len(meta['related_references']) > 20:
                raise RegulatoryError('INVALID_METADATA')
            for pair in meta['related_references']:
                if not isinstance(pair, tuple) or len(pair) != 2 or pair[0] not in ('AMENDS','CORRECTS','SUPERSEDES'):
                    raise RegulatoryError('INVALID_RELATION')
                bounded(pair[1], 160)
        links=meta.get('translation_urls', ())
        if not isinstance(links,tuple) or len(links)>4: raise RegulatoryError('INVALID_RELATION')
        if links: meta=freeze({**meta,'translation_urls':tuple(sorted({item_url(link,self.source_key) for link in links}))})
        object.__setattr__(self, 'metadata', meta)
        # Retain distinct content/status versions, collapse only repeated retrieval.
        finish(self, 'item_id', 'RI', ('retrieved_at',))


@dataclass(frozen=True)
class RegulatoryEvent:
    agency: str
    jurisdiction: str
    regulatory_type: RegulatoryType
    status: RegulatoryStatus
    published_at: date | datetime | None
    effective_at: date | datetime | None
    legal_reference: str | None
    docket_or_reference: str | None
    source_ids: tuple
    evidence_ids: tuple
    affected_industries: tuple = ()
    affected_entities: tuple = ()
    metadata: FrozenMetadata = field(default_factory=FrozenMetadata)
    event_id: str = ''

    def __post_init__(self):
        if (self.agency, self.jurisdiction) not in {(s.agency, s.jurisdiction) for s in SOURCES.values()}:
            raise RegulatoryError('IDENTITY_MISMATCH')
        enum(self.regulatory_type, RegulatoryType); enum(self.status, RegulatoryStatus)
        regulatory_date(self.published_at); regulatory_date(self.effective_at)
        bounded(self.legal_reference, 500, True); bounded(self.docket_or_reference, 200, True)
        for name in ('source_ids','evidence_ids'):
            refs = ids(getattr(self, name))
            if not refs: raise RegulatoryError('MISSING_PROVENANCE')
            object.__setattr__(self, name, refs)
        for name in ('affected_industries','affected_entities'): object.__setattr__(self, name, labels(getattr(self, name)))
        meta = freeze(self.metadata)
        if not isinstance(meta, FrozenMetadata) or set(meta) - {'item_id','source_family','agency_family','rule_references'}:
            raise RegulatoryError('INVALID_METADATA')
        bounded(meta.get('item_id'), 80); bounded(meta.get('source_family'), 40); bounded(meta.get('agency_family'), 80)
        labels(meta.get('rule_references', ()))
        object.__setattr__(self, 'metadata', meta)
        finish(self, 'event_id', 'RE')


@dataclass(frozen=True)
class RegulatoryRelationship:
    kind: str
    from_item_id: str
    to_item_id: str

    def __post_init__(self):
        if self.kind not in ('RULE_FAMILY','AMENDS','CORRECTS','SUPERSEDES','TRANSLATION_OF'):
            raise RegulatoryError('INVALID_RELATION')
        if self.from_item_id==self.to_item_id: raise RegulatoryError('INVALID_RELATION')
        ids((self.from_item_id,self.to_item_id))


@dataclass(frozen=True)
class RegulatoryBundle:
    items: tuple
    sources: tuple
    evidence_items: tuple
    events: tuple
    relationships: tuple
    created_at: datetime

    def __post_init__(self):
        instant(self.created_at)
        for name in ('items','sources','evidence_items','events','relationships'):
            if not isinstance(getattr(self,name),(tuple,list)): raise RegulatoryError('INVALID_BUNDLE')
            object.__setattr__(self,name,tuple(getattr(self,name)))
        if len(self.items)>200 or any(not isinstance(i,RegulatoryItem) for i in self.items):
            raise RegulatoryError('INVALID_BUNDLE')
        if len({i.item_id for i in self.items})!=len(self.items) or any(i.retrieved_at>self.created_at for i in self.items):
            raise RegulatoryError('INVALID_BUNDLE')
        from .service import normalize, relationships_for
        rows=[normalize(i) for i in self.items]
        for index,name in enumerate(('sources','evidence_items','events')):
            expected=tuple(row[index] for row in rows if row[index] is not None)
            if getattr(self,name)!=expected: raise RegulatoryError('INVALID_PROVENANCE_GRAPH')
        if self.relationships!=relationships_for(self.items): raise RegulatoryError('INVALID_RELATION_GRAPH')

    def as_evidence_pack(self):
        from ..models import EvidencePack
        return EvidencePack('Government/regulatory publications',self.created_at,
                            sources=self.sources,evidence_items=self.evidence_items)
