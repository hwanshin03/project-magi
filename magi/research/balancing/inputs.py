"""Pure bridges from validated normalized containers; never imports fetch services."""
from magi.research.validation import operation
from dataclasses import dataclass, field
from datetime import date, datetime
from ..models import EvidencePack, EvidenceItem, ResearchSource, instant
from ..snapshot_models import CompanyResearchSnapshot, BaseMetric
from ..news.models import NewsEvidencePack
from ..regulatory.models import RegulatoryBundle
from .models import (InputFamily, Availability, InputSnapshot, InputAvailability,
                     QualifiedReference, LineageKind, LineageReference, EvidenceUniverse,
                     fingerprint)


def family_for(container):
    if type(container) is EvidencePack: return InputFamily.RESEARCH
    if type(container) is CompanyResearchSnapshot: return InputFamily.FINANCIAL_SNAPSHOT
    if type(container) is NewsEvidencePack: return InputFamily.NEWS
    if type(container) is RegulatoryBundle: return InputFamily.REGULATORY
    raise ValueError('Unsupported normalized container; market provenance bridge is deferred')


@operation
def validate_container(container):
    # Re-run existing graph/derived-value checks, including objects constructed
    # outside normal builders. The caller's original immutable object is retained.
    from ..serialization import encode, decode
    from ..validation import _already_validated, _remember_validated, _verify_runtime_indexes
    encoded = encode(container)
    if _already_validated(container, encoded): return
    rebuilt = decode(encoded)
    if rebuilt != container: raise ValueError('Invalid normalized container')
    _verify_runtime_indexes(container, rebuilt)
    _remember_validated(container, encoded)


def record_count(container):
    if type(container) is EvidencePack: return len(container.evidence_items)
    if type(container) is CompanyResearchSnapshot: return len(container.evidence_references)
    if type(container) is NewsEvidencePack: return len(container.articles)
    if type(container) is RegulatoryBundle: return len(container.items)
    raise ValueError('Unsupported container')


def adapt(container):
    return InputSnapshot(family_for(container), container)


def members(container):
    yield container
    names = ('sources', 'evidence_items', 'claims', 'relations')
    if type(container) is CompanyResearchSnapshot:
        names = ('sources', 'evidence_references', 'income_statement', 'growth',
                 'profitability', 'balance_sheet', 'cash_flow', 'per_share')
    elif type(container) is NewsEvidencePack:
        names = ('articles', 'sources', 'evidence_items', 'events', 'event_clusters', 'catalysts')
    elif type(container) is RegulatoryBundle:
        names = ('items', 'sources', 'evidence_items', 'events', 'relationships')
    for name in names:
        for value in getattr(container, name):
            yield value
            if type(value) is BaseMetric:
                yield value.current
                yield value.prior


def reference(snapshot, obj):
    # Evidence objects have source_id too: prefer their own identifiers.
    identifier = next((getattr(obj, key) for key in (
        'pack_id', 'item_id', 'catalyst_id', 'cluster_id', 'event_id',
        'article_id', 'claim_id', 'evidence_id', 'source_id') if hasattr(obj, key)), None)
    return QualifiedReference(snapshot.snapshot_id, type(obj).__name__, identifier, fingerprint(obj))


@operation
def index_inputs(inputs):
    refs = {}; links = set(); snapshots = {s.snapshot_id: s for s in inputs}
    def link(kind, child, parent, basis):
        if child != parent:
            links.add(LineageReference(kind, child, parent, 'structured-input-v1', basis))
    for snapshot in inputs:
        container = snapshot.container
        validate_container(snapshot)
        pairs = snapshot._reference_pairs
        for obj, ref in pairs:
            old = refs.get(ref.reference_id)
            if old is not None and old != ref: raise ValueError('Reference collision')
            refs[ref.reference_id] = ref
        sources = {o.source_id: r for o, r in pairs if type(o) is ResearchSource}
        evidence = {o.evidence_id: r for o, r in pairs if type(o) is EvidenceItem}
        articles = {o.article_id: r for o, r in pairs if type(o).__name__ == 'NewsArticle'}
        events = {o.event_id: r for o, r in pairs if type(o).__name__ in ('ResearchEvent', 'RegulatoryEvent')}
        items = {o.item_id: r for o, r in pairs if type(o).__name__ == 'RegulatoryItem'}
        root = pairs[0][1]
        for obj, ref in pairs[1:]:
            link(LineageKind.REPRESENTATION_OF, root, ref, 'container-member')
            if type(obj) is EvidenceItem:
                link(LineageKind.DERIVED_FROM, ref, sources[obj.source_id], 'evidence-source')
            elif type(obj) is ResearchSource:
                article_id = obj.metadata.get('article_id')
                item_id = obj.metadata.get('item_id')
                if article_id in articles: link(LineageKind.DERIVED_FROM, ref, articles[article_id], 'source-article')
                if item_id in items: link(LineageKind.DERIVED_FROM, ref, items[item_id], 'source-regulatory-item')
            elif type(obj).__name__ in ('ResearchEvent', 'RegulatoryEvent', 'ResearchCatalyst', 'MetricValue', 'DerivedMetric'):
                for key in getattr(obj, 'input_evidence_ids', getattr(obj, 'evidence_ids', ())):
                    link(LineageKind.DERIVED_FROM, ref, evidence[key], 'explicit-evidence-reference')
            elif type(obj).__name__ == 'EventCluster':
                for key in obj.event_ids: link(LineageKind.REPRESENTATION_OF, ref, events[key], 'existing-news-cluster')
        if type(container) is RegulatoryBundle:
            for relation in container.relationships:
                if relation.kind == 'TRANSLATION_OF':
                    link(LineageKind.TRANSLATION_OF, items[relation.from_item_id], items[relation.to_item_id], 'explicit-regulatory-translation')
        if type(container) is NewsEvidencePack:
            official = {a.metadata['official_item_id']: a for a in container.articles if a.metadata.get('official_item_id')}
            for key, article in official.items():
                for translated_id in article.metadata.get('translation_item_ids', ()):
                    other = official.get(translated_id)
                    if other and key < translated_id and key in other.metadata.get('translation_item_ids', ()):
                        link(LineageKind.TRANSLATION_OF, articles[article.article_id], articles[other.article_id], 'explicit-reciprocal-translation')
        if type(container) is CompanyResearchSnapshot:
            # A pack ID alone is insufficient: require exact cited evidence and
            # source versions before linking an independently supplied container.
            for other in snapshots.values():
                pack = other.container
                if type(pack) is EvidencePack and pack.pack_id == container.underlying_pack_id:
                    if (set(container.evidence_references) <= set(pack.evidence_items)
                            and set(container.sources) <= set(pack.sources)):
                        link(LineageKind.DERIVED_FROM, root, reference(other, pack), 'snapshot-underlying-pack')
    # Exact normalized object aliases across containers share representation
    # lineage, not additional corroboration. Changed versions never alias.
    aliases = {}
    for ref in sorted(refs.values(), key=lambda r: r.reference_id):
        key = (ref.object_kind, ref.object_id, ref.content_fingerprint)
        prior = aliases.setdefault(key, ref)
        if prior != ref:
            link(LineageKind.REPRESENTATION_OF, ref, prior, 'identical-normalized-object')
    return tuple(refs[k] for k in sorted(refs)), tuple(sorted(links, key=fingerprint))


def validate_lineage(refs, links):
    known = {r.reference_id: r for r in refs}
    graph = {}
    for link in links:
        if any(known.get(r.reference_id) != r for r in (link.child, link.parent)):
            raise ValueError('Dangling lineage reference')
        if link.kind in (LineageKind.DERIVED_FROM, LineageKind.VERSION_OF):
            graph.setdefault(link.child.reference_id, set()).add(link.parent.reference_id)
    visiting = set(); visited = set()
    def visit(key):
        if key in visiting: raise ValueError('Cyclic derivation/version lineage')
        if key in visited: return
        visiting.add(key)
        for parent in sorted(graph.get(key, ())): visit(parent)
        visiting.remove(key); visited.add(key)
    for key in sorted(graph): visit(key)


@operation
def build_universe(request, containers=(), *, availability=(), declared_lineage=()):
    inputs = tuple(c if type(c) is InputSnapshot else adapt(c) for c in containers)
    by_id = {s.snapshot_id: s for s in inputs}
    explicit = tuple(availability)
    if any(type(a) is not InputAvailability for a in explicit): raise ValueError('Invalid availability')
    overrides = {}
    for a in explicit: overrides.setdefault(a.family, []).append(a)
    manifest = []
    for family in InputFamily:
        supplied = tuple(s for s in by_id.values() if s.family == family)
        if family in overrides:
            manifest.extend(overrides[family])
            continue
        omissions = tuple(sorted({key for s in supplied for key in getattr(s.container, 'selection_omissions', ())}))
        state = (Availability.NOT_SUPPLIED if not supplied else Availability.PARTIAL if omissions
                 else Availability.AVAILABLE if any(record_count(s.container) for s in supplied) else Availability.EMPTY)
        entry = InputAvailability(family, state, tuple(s.snapshot_id for s in supplied), omissions=omissions)
        manifest.append(entry)
    return EvidenceUniverse(request, inputs, tuple(manifest), tuple(declared_lineage))


@operation
def resolve(universe, ref):
    if ref not in universe.references: raise ValueError('Unknown qualified reference')
    obj = universe._objects.get(ref)
    if obj is None: raise ValueError('Unknown qualified reference')
    snapshot = universe._snapshots[ref.snapshot_id]
    if fingerprint(obj) != ref.content_fingerprint:
        raise ValueError('Unresolved qualified reference')
    if not any(value is obj for value in members(snapshot.container)):
        raise ValueError('Changed container membership')
    return obj


@dataclass(frozen=True)
class TemporalObservation:
    field: str
    value: date | datetime | None
    relation_to_as_of: str
    knowledge_boundary: bool = field(init=False)

    @operation
    def __post_init__(self):
        if self.field not in ('published_at', 'retrieved_at', 'created_at', 'occurred_at', 'as_of', 'effective_at', 'period_start', 'period_end'):
            raise ValueError('Invalid temporal field')
        if isinstance(self.value, datetime): instant(self.value)
        elif self.value is not None and type(self.value) is not date: raise ValueError('Invalid temporal value')
        if self.relation_to_as_of not in ('UNKNOWN', 'AFTER', 'AT_OR_BEFORE', 'DATE_AFTER', 'DATE_BEFORE', 'SAME_DATE_UNORDERED'):
            raise ValueError('Invalid temporal comparison')
        object.__setattr__(self, 'knowledge_boundary', self.field in ('published_at', 'retrieved_at', 'created_at', 'as_of'))
        allowed = ('UNKNOWN',) if self.value is None else ('AFTER', 'AT_OR_BEFORE') if isinstance(self.value, datetime) else ('DATE_AFTER', 'DATE_BEFORE', 'SAME_DATE_UNORDERED')
        if self.relation_to_as_of not in allowed: raise ValueError('Temporal comparison loses precision')


def temporal_observations(universe, ref):
    """Report precision without filtering. Effective/period dates are not availability.

    Date comparisons use the explicitly supplied as_of calendar only; they never
    claim an instant or invent source timezone/intraday ordering.
    """
    obj = resolve(universe, ref); boundary = universe.request.as_of
    values = {}
    for name in ('published_at', 'retrieved_at', 'created_at', 'occurred_at', 'as_of', 'effective_at', 'period_start', 'period_end'):
        if hasattr(obj, name): values[name] = getattr(obj, name)
    meta = getattr(obj, 'metadata', {})
    for name, precision in (('published_at', 'publication_precision'), ('effective_at', 'effective_precision')):
        if values.get(name) is None and meta.get(precision) == 'DATE' and meta.get(name):
            values[name] = date.fromisoformat(meta[name])
    period = getattr(obj, 'period', None)
    if period:
        values['period_start'] = period.start; values['period_end'] = period.end
    result = []
    for name, value in sorted(values.items()):
        if value is None: relation = 'UNKNOWN'
        elif isinstance(value, datetime): relation = 'AFTER' if value > boundary else 'AT_OR_BEFORE'
        elif type(value) is date:
            relation = 'DATE_AFTER' if value > boundary.date() else 'DATE_BEFORE' if value < boundary.date() else 'SAME_DATE_UNORDERED'
        else: raise ValueError('Invalid temporal metadata')
        result.append(TemporalObservation(name, value, relation))
    return tuple(result)
