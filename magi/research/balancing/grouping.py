"""Deterministic event contexts over references, without inference or source voting."""
from dataclasses import dataclass, field
from datetime import timezone
import re
from ..models import ResearchSource, SourceType
from ..news.models import NewsArticle
from ..regulatory.models import RegulatoryItem
from .models import EvidenceUniverse, QualifiedReference, LineageKind, label, sequence, finish, fingerprint
from .inputs import members, reference, validate_container


@dataclass(frozen=True)
class GroupAnchor:
    """An attributed external key, not a universal or verified event identity."""
    namespace: str
    key: str
    scope: str
    compatibility: tuple = ()

    def __post_init__(self):
        for value in (self.namespace, self.key, self.scope): label(value, 1000)
        values = sequence(self.compatibility, str)
        for value in values: label(value, 1000)
        object.__setattr__(self, 'compatibility', values)


@dataclass(frozen=True)
class AnalyticalGroup:
    """One anchored context or one unanchored content identity, with all versions."""
    anchor: GroupAnchor | None
    members: tuple
    group_id: str = ''

    def __post_init__(self):
        if self.anchor is not None and type(self.anchor) is not GroupAnchor:
            raise ValueError('Invalid group anchor')
        refs = sequence(self.members, QualifiedReference)
        if not refs: raise ValueError('Empty analytical group')
        object.__setattr__(self, 'members', tuple(sorted(set(refs), key=lambda r: r.reference_id)))
        finish(self, 'group_id', 'AG')


@dataclass(frozen=True)
class GroupedEvidence:
    """Original universe plus reproducible groups; unassigned aggregates stay visible."""
    universe: EvidenceUniverse
    groups: tuple = field(init=False)
    unassigned: tuple = field(init=False)
    grouping_id: str = ''

    def __post_init__(self):
        if type(self.universe) is not EvidenceUniverse: raise ValueError('Invalid evidence universe')
        validate_container(self.universe)
        groups, unassigned = _group(self.universe)
        object.__setattr__(self, 'groups', groups)
        object.__setattr__(self, 'unassigned', unassigned)
        finish(self, 'grouping_id', 'GA')


def _scope(universe, obj, container):
    # The request is an explicit context, not an inferred issuer mapping. Conflicting
    # known instruments are isolated; absent markets are never filled into records.
    target = universe.request.target.instrument
    ticker = getattr(obj, 'ticker', None) or getattr(container, 'ticker', None)
    market = (getattr(obj, 'market', None) or getattr(obj, 'metadata', {}).get('market')
              or getattr(container, 'market', None))
    if ((not target and (ticker or market))
            or target and ((ticker and ticker != target.symbol) or (market and market != target.market))):
        return fingerprint(('other-instrument', market, ticker))
    return fingerprint(universe.request.target)


def _anchor(obj, scope):
    if type(obj) is RegulatoryItem:
        key = obj.metadata.get('source_record_id')
        if key:
            # Document identity, never docket/rule family or inferred C1 relation.
            return GroupAnchor('GOVERNMENT_REGULATORY:'+obj.jurisdiction+':'+obj.agency,
                               key, scope, (obj.regulatory_type.value, obj.status.value))
    elif type(obj) is NewsArticle:
        c = obj.classification
        if c.event_namespace and c.event_key and c.classification_source:
            day = c.event_time.astimezone(timezone.utc).date().isoformat() if c.event_time else 'UNKNOWN'
            return GroupAnchor(c.event_namespace, c.event_key, scope, (c.event_type.value, day))
    elif type(obj) is ResearchSource and obj.source_type == SourceType.SEC_FILING:
        cik = obj.metadata.get('cik'); accession = obj.metadata.get('accession')
        if (isinstance(cik, str) and re.fullmatch(r'\d{10}', cik)
                and isinstance(accession, str) and re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession)):
            return GroupAnchor('SEC_FILING:'+cik, accession, scope, (obj.document_type or 'UNKNOWN',))
    return None


def _group(universe):
    objects = {reference(s, obj): (obj, s.container) for s in universe.inputs for obj in members(s.container)}
    # Only document/report roots originate contexts. Normalized sources, events,
    # metrics and catalysts inherit context through *generated* derivation links.
    generated = [x for x in universe.lineage if x.asserted_by == 'structured-input-v1']
    derived_sources = {x.child.content_fingerprint for x in generated
                       if x.basis in ('source-article', 'source-regulatory-item')}
    roots = {r: (o, c) for r, (o, c) in objects.items()
             if type(o) in (NewsArticle, RegulatoryItem)
             or type(o) is ResearchSource and r.content_fingerprint not in derived_sources}
    anchors = {r: _anchor(o, _scope(universe, o, c)) for r, (o, c) in roots.items()}
    # News can explicitly cite a supplied regulatory document using its existing
    # agency-family namespace and source record ID. Conflicting versions/statuses
    # are ambiguous: leave the report singleton instead of bridging developments.
    regulatory = {}
    for r, (o, _) in roots.items():
        a = anchors[r]
        if type(o) is RegulatoryItem and a:
            regulatory.setdefault((a.namespace, a.key, a.scope), set()).add(a)
    for r, (o, _) in roots.items():
        a = anchors[r]
        if type(o) is NewsArticle and a and a.namespace.startswith('GOVERNMENT_REGULATORY:'):
            candidates = regulatory.get((a.namespace, a.key, a.scope), set())
            anchors[r] = next(iter(candidates)) if len(candidates) == 1 else None
    buckets = {}; assigned = {}
    for r in roots:
        a = anchors[r]
        key = fingerprint(a) if a else fingerprint((_scope(universe, *roots[r]), r.object_kind, r.object_id, r.content_fingerprint))
        buckets.setdefault(key, (a, set()))[1].add(r)
        assigned[r] = {key}
    # Propagate context membership only, never union event buckets. A summary may
    # reference multiple contexts without making those contexts equivalent.
    links = [x for x in generated if x.kind == LineageKind.DERIVED_FROM
             or x.basis in ('identical-normalized-object', 'existing-news-cluster')]
    changed = True
    while changed:
        changed = False
        for x in links:
            if x.child in roots: continue
            parent = assigned.get(x.parent, set())
            prior = assigned.setdefault(x.child, set())
            if not parent <= prior:
                prior.update(parent); changed = True
            if x.basis == 'identical-normalized-object' and x.parent not in roots:
                reverse = assigned.setdefault(x.parent, set())
                if not prior <= reverse:
                    reverse.update(prior); changed = True
    for r, keys in assigned.items():
        for key in keys: buckets[key][1].add(r)
    groups = tuple(sorted((AnalyticalGroup(a, tuple(refs)) for a, refs in buckets.values()), key=lambda g: g.group_id))
    unassigned = tuple(sorted((r for r in universe.references if not assigned.get(r)), key=lambda r: r.reference_id))
    return groups, unassigned
