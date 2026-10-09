"""Pure categorical assessment. No ranking, direction, selection or inference."""
from magi.research.validation import operation
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from ..models import ResearchSource, SourceType, instant
from ..news.models import NewsArticle
from ..regulatory.models import RegulatoryItem
from ..snapshot_models import DerivedMetric, MetricStatus
from .models import TargetIdentity, QualifiedReference, label, sequence, strings, finish, fingerprint
from .grouping import GroupedEvidence
from .inputs import validate_container
from ..temporal import TEMPORAL_VERSION, availability, publication_time, relation, derived_available


class RelevanceLevel(str, Enum):
    DIRECT = 'DIRECT'
    LINKED = 'LINKED'
    CONTEXTUAL = 'CONTEXTUAL'
    UNRESOLVED = 'UNRESOLVED'
    UNRELATED = 'UNRELATED'


class AttentionLevel(str, Enum):
    ELEVATED = 'ELEVATED'
    STANDARD = 'STANDARD'
    BACKGROUND = 'BACKGROUND'
    UNASSESSED = 'UNASSESSED'


class TemporalFitness(str, Enum):
    AS_OF_COMPATIBLE = 'AS_OF_COMPATIBLE'
    FUTURE_RELATIVE_TO_AS_OF = 'FUTURE_RELATIVE_TO_AS_OF'
    OUTSIDE_HORIZON = 'OUTSIDE_HORIZON'
    UNKNOWN = 'UNKNOWN'
    MIXED = 'MIXED'


@dataclass(frozen=True)
class TargetExposure:
    """Caller-supplied, dated assertion about one qualified source record."""
    target: TargetIdentity
    reference: QualifiedReference
    level: RelevanceLevel
    relationship: str
    asserted_by: str
    basis_reference: str
    known_at: datetime
    mapping_id: str = ''

    @operation
    def __post_init__(self):
        if type(self.target) is not TargetIdentity or type(self.reference) is not QualifiedReference:
            raise ValueError('Invalid exposure endpoint')
        if type(self.level) is not RelevanceLevel or self.level not in (RelevanceLevel.LINKED, RelevanceLevel.CONTEXTUAL):
            raise ValueError('Exposure assertions support linked/contextual relevance only')
        if self.relationship not in ('SUPPLIER','CUSTOMER','PRODUCT','SEGMENT','INDUSTRY','JURISDICTION','EVENT'):
            raise ValueError('Unsupported exposure relationship')
        label(self.asserted_by); label(self.basis_reference, 1000); instant(self.known_at)
        finish(self, 'mapping_id', 'AX')


@dataclass(frozen=True)
class AssessmentPolicy:
    version: str = '7E.3-v1'
    requested_metrics: tuple = ()
    published_since: datetime | None = None
    temporal_version: str = TEMPORAL_VERSION

    @operation
    def __post_init__(self):
        if self.temporal_version != TEMPORAL_VERSION: raise ValueError('Unsupported temporal policy')
        if self.version != '7E.3-v1': raise ValueError('Unsupported assessment policy version')
        object.__setattr__(self, 'requested_metrics', strings(self.requested_metrics))
        instant(self.published_since, True)


@dataclass(frozen=True)
class AssessmentBasis:
    rule_id: str
    references: tuple = ()
    mapping_id: str | None = None

    @operation
    def __post_init__(self):
        label(self.rule_id)
        refs = sequence(self.references, QualifiedReference)
        object.__setattr__(self, 'references', tuple(sorted(set(refs), key=lambda r: r.reference_id)))
        if self.mapping_id is not None: label(self.mapping_id)


@dataclass(frozen=True)
class ReferenceFitness:
    reference: QualifiedReference
    fitness: TemporalFitness
    reasons: tuple = ()

    @operation
    def __post_init__(self):
        if type(self.reference) is not QualifiedReference or type(self.fitness) is not TemporalFitness:
            raise ValueError('Invalid temporal assessment')
        if self.fitness == TemporalFitness.MIXED: raise ValueError('Mixed is a group-level state')
        object.__setattr__(self, 'reasons', strings(self.reasons))


@dataclass(frozen=True)
class GroupAssessment:
    group_id: str
    relevance: RelevanceLevel
    attention: AttentionLevel
    temporal: TemporalFitness
    temporal_records: tuple
    basis: tuple
    uncertainties: tuple
    assessment_id: str = ''

    @operation
    def __post_init__(self):
        label(self.group_id)
        for value, cls in ((self.relevance, RelevanceLevel), (self.attention, AttentionLevel), (self.temporal, TemporalFitness)):
            if type(value) is not cls: raise ValueError('Invalid assessment category')
        records = sequence(self.temporal_records, ReferenceFitness)
        if len({r.reference for r in records}) != len(records): raise ValueError('Duplicate temporal reference')
        object.__setattr__(self, 'temporal_records', tuple(sorted(records, key=lambda r: r.reference.reference_id)))
        object.__setattr__(self, 'basis', tuple(sorted(set(sequence(self.basis, AssessmentBasis)), key=fingerprint)))
        object.__setattr__(self, 'uncertainties', strings(self.uncertainties))
        finish(self, 'assessment_id', 'AA')


@dataclass(frozen=True)
class AssessmentSet:
    grouped: GroupedEvidence
    policy: AssessmentPolicy = field(default_factory=AssessmentPolicy)
    exposures: tuple = ()
    assessments: tuple = field(init=False)
    assessment_set_id: str = ''

    @operation
    def __post_init__(self):
        if type(self.grouped) is not GroupedEvidence or type(self.policy) is not AssessmentPolicy:
            raise ValueError('Invalid assessment input')
        validate_container(self.grouped)
        universe = self.grouped.universe
        if self.policy.published_since and self.policy.published_since > universe.request.as_of:
            raise ValueError('Horizon begins after as_of')
        exposures = tuple(sorted(set(sequence(self.exposures, TargetExposure)), key=lambda x: x.mapping_id))
        for x in exposures:
            if x.target != universe.request.target or x.reference not in universe.references:
                raise ValueError('Exposure target/reference mismatch')
        object.__setattr__(self, 'exposures', exposures)
        object.__setattr__(self, 'assessments', _assess(self))
        finish(self, 'assessment_set_id', 'AS')


def _fitness(universe, ref, policy, obj):
    boundary = universe.request.as_of
    basis, ordering = availability(obj, boundary)
    reasons = ('RETRIEVAL_TIME_FALLBACK',) if basis == 'retrieved_at' else ()
    if ordering in ('AFTER','DATE_AFTER'):
        return ReferenceFitness(ref, TemporalFitness.FUTURE_RELATIVE_TO_AS_OF, (*reasons, 'AFTER_AS_OF'))
    if ordering in ('UNKNOWN','SAME_DATE_UNORDERED'):
        return ReferenceFitness(ref, TemporalFitness.UNKNOWN, (*reasons, 'PUBLICATION_TIME_UNRESOLVED'))
    if policy.published_since:
        value = publication_time(obj)
        horizon = relation(value, policy.published_since)
        if horizon == 'UNKNOWN':
            return ReferenceFitness(ref, TemporalFitness.UNKNOWN, (*reasons, 'PUBLICATION_TIME_UNRESOLVED'))
        if horizon == 'SAME_DATE_UNORDERED':
            return ReferenceFitness(ref, TemporalFitness.UNKNOWN, ('HORIZON_DATE_UNORDERED',))
        outside = value < policy.published_since if isinstance(value, datetime) else horizon == 'DATE_BEFORE'
        if outside: return ReferenceFitness(ref, TemporalFitness.OUTSIDE_HORIZON, ('BEFORE_DECLARED_HORIZON',))
    return ReferenceFitness(ref, TemporalFitness.AS_OF_COMPATIBLE, reasons)


def _relevance(obj, target):
    """Match structured identity roles, never names, mentions, authority or direction."""
    instrument = target.instrument; entity = target.entity
    ticker = getattr(obj, 'ticker', None); market = getattr(obj, 'market', None)
    if type(obj) is NewsArticle:
        ticker = obj.tickers[0] if len(obj.tickers) == 1 else None
        market = obj.metadata.get('market')
    mismatch = bool(instrument and ((market and market != instrument.market) or (ticker and ticker != instrument.symbol)))
    source_entity = obj.metadata.get('issuer_id')
    # Entity identifiers are qualified tokens. Display-name strings never match.
    entity_key = entity.namespace+':'+entity.identifier if entity else None
    affected = type(obj) is RegulatoryItem and entity_key in obj.affected_entities if entity_key else False
    entity_match = bool(entity_key and (source_entity == entity_key or affected))
    entity_mismatch = bool(entity_key and isinstance(source_entity, str)
                           and source_entity.partition(':')[0] == entity.namespace and source_entity != entity_key)
    if mismatch and entity_match or entity_mismatch and instrument and ticker == instrument.symbol and market == instrument.market:
        return RelevanceLevel.UNRESOLVED, 'IDENTITY_CONFLICT'
    if entity_match: return RelevanceLevel.DIRECT, 'EXPLICIT_ENTITY_MATCH'
    if mismatch or entity_mismatch: return RelevanceLevel.UNRELATED, 'EXPLICIT_IDENTITY_MISMATCH'
    issuer_role = (type(obj) is ResearchSource and obj.source_type in (SourceType.SEC_FILING, SourceType.DART_FILING, SourceType.COMPANY_IR, SourceType.EARNINGS_RELEASE)
                   or type(obj) is NewsArticle and bool(obj.metadata.get('official_item_id')))
    if issuer_role and instrument and (ticker, market) == (instrument.symbol, instrument.market):
        return RelevanceLevel.DIRECT, 'EXPLICIT_ISSUER_INSTRUMENT'
    return RelevanceLevel.UNRESOLVED, 'TARGET_CONNECTION_UNRESOLVED'


def _assess(result):
    universe = result.grouped.universe
    objects = universe._objects
    generated = [x for x in universe.lineage if x.asserted_by == 'structured-input-v1']
    derived_sources = {x.child.content_fingerprint for x in generated if x.basis in ('source-article','source-regulatory-item')}
    roots = {r for r, o in objects.items() if type(o) in (NewsArticle, RegulatoryItem)
             or type(o) is ResearchSource and r.content_fingerprint not in derived_sources}
    if any(x.reference not in roots for x in result.exposures):
        raise ValueError('Exposure must reference an original document/report root')
    fitness = {r: _fitness(universe, r, result.policy, objects[r]) for r in roots}
    output = []
    for group in result.grouped.groups:
        records = tuple(fitness[r] for r in group.members if r in roots)
        eligible = {r.reference for r in records if r.fitness == TemporalFitness.AS_OF_COMPATIBLE}
        states = {r.fitness for r in records}
        temporal = next(iter(states)) if len(states) == 1 else TemporalFitness.MIXED if states else TemporalFitness.UNKNOWN
        uncertainty = {reason for r in records for reason in r.reasons}
        basis = []; support = {}
        for r in sorted(eligible, key=lambda r: r.reference_id):
            level, rule = _relevance(objects[r], universe.request.target)
            basis.append(AssessmentBasis(rule, (r,)))
            support.setdefault(level, set()).add(r)
            if level == RelevanceLevel.UNRESOLVED: uncertainty.add(rule)
            for x in result.exposures:
                if x.reference == r:
                    if x.known_at > universe.request.as_of:
                        uncertainty.add('EXPOSURE_AFTER_AS_OF')
                    elif rule != 'IDENTITY_CONFLICT':
                        basis.append(AssessmentBasis('EXPLICIT_'+x.relationship, (r,), x.mapping_id))
                        support.setdefault(x.level, set()).add(r)
        supported = [x for x in (RelevanceLevel.DIRECT, RelevanceLevel.LINKED, RelevanceLevel.CONTEXTUAL) if x in support]
        relevance = supported[0] if supported else RelevanceLevel.UNRELATED if support and all(x == RelevanceLevel.UNRELATED for x in support) else RelevanceLevel.UNRESOLVED
        if not eligible: uncertainty.add('NO_TEMPORALLY_ELIGIBLE_ROOT')
        attention = {RelevanceLevel.DIRECT:AttentionLevel.STANDARD, RelevanceLevel.LINKED:AttentionLevel.STANDARD,
                     RelevanceLevel.CONTEXTUAL:AttentionLevel.BACKGROUND}.get(relevance, AttentionLevel.UNASSESSED)
        rule = 'SUPPORTED_RELATION_STANDARD' if attention == AttentionLevel.STANDARD else 'CONTEXT_BACKGROUND' if attention == AttentionLevel.BACKGROUND else 'INSUFFICIENT_STRUCTURED_BASIS'
        basis.append(AssessmentBasis(rule, tuple(support.get(relevance, ()))))
        if result.policy.requested_metrics:
            # Consume existing Phase 7C comparisons, never recalculate or threshold.
            satisfied = set()
            for ref in group.members:
                metric = objects[ref]
                if type(metric) is not DerivedMetric or metric.name not in result.policy.requested_metrics: continue
                links = [x for x in generated if x.child == ref and x.basis == 'explicit-evidence-reference']
                evidence = {x.parent for x in links}
                source_links = [x for x in generated if x.child in evidence and x.basis == 'evidence-source']
                sources = {x.parent for x in source_links}
                evidence_known = all(derived_available(objects[r], universe.request.as_of) for r in evidence)
                if (metric.status == MetricStatus.AVAILABLE and evidence and sources <= eligible
                        and len(source_links) == len(evidence) and evidence_known
                        and relevance == RelevanceLevel.DIRECT):
                    satisfied.add(metric.name)
                    basis.append(AssessmentBasis('REQUESTED_COMPARABLE_METRIC', tuple(evidence)))
            if satisfied: attention = AttentionLevel.ELEVATED
            else:
                attention = AttentionLevel.UNASSESSED
                uncertainty.add('REQUESTED_COMPARABLE_METRIC_UNAVAILABLE')
            if satisfied != set(result.policy.requested_metrics): uncertainty.add('REQUESTED_METRIC_COVERAGE_INCOMPLETE')
        output.append(GroupAssessment(group.group_id,relevance,attention,temporal,records,tuple(basis),tuple(uncertainty)))
    return tuple(sorted(output, key=lambda a: a.group_id))
