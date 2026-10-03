"""Offline, citation-closed structural views. No agents, retrieval or truth scoring."""
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, date
import json
from ..models import EvidenceItem, ResearchClaim, RelationKind, Category
from ..news.models import NewsArticle
from .models import QualifiedReference, LineageKind, sequence, strings, finish
from .inputs import members, reference, validate_container
from .assessment import AssessmentSet, RelevanceLevel, AttentionLevel, TemporalFitness


class OmissionReason(str, Enum):
    BUDGET_LIMIT = 'BUDGET_LIMIT'
    REDUNDANT_COVERAGE = 'REDUNDANT_COVERAGE'
    TEMPORAL_EXCLUSION = 'TEMPORAL_EXCLUSION'
    UNRELATED = 'UNRELATED'
    REVIEW_LIMIT = 'REVIEW_LIMIT'
    UNSUPPORTED_PRESENTATION = 'UNSUPPORTED_PRESENTATION'


@dataclass(frozen=True)
class EvidenceSelectionPolicy:
    version: str = '7E.4-v1'
    budget_profile: str = 'structural-v1'
    max_groups: int = 8
    max_units: int = 16
    max_citations: int = 32
    max_bytes: int = 16000
    excerpt_bytes: int = 320
    soft_family_limit: int = 2
    review_slots: int = 1
    required_groups: tuple = ()

    def __post_init__(self):
        if self.version != '7E.4-v1' or self.budget_profile != 'structural-v1':
            raise ValueError('Unsupported selection policy/profile')
        for name in ('max_groups','max_units','max_citations','max_bytes','review_slots'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError('Invalid structural budget')
        for name in ('excerpt_bytes','soft_family_limit'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError('Invalid selection bound')
        object.__setattr__(self, 'required_groups', strings(self.required_groups))


@dataclass(frozen=True)
class BudgetUsage:
    groups: int
    units: int
    citations: int
    rendered_bytes: int

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (self.groups,self.units,self.citations,self.rendered_bytes)):
            raise ValueError('Invalid budget usage')


def _refs(values):
    return tuple(sorted(set(sequence(values, QualifiedReference)), key=lambda r: r.reference_id))


@dataclass(frozen=True)
class SelectedGroup:
    group_id: str
    units: tuple
    citations: tuple
    conflicts: tuple = ()
    notice: str = 'NONE'
    review_only: bool = False
    audit_codes: tuple = ()

    def __post_init__(self):
        strings((self.group_id,))
        for name in ('units','citations','conflicts'):
            object.__setattr__(self, name, _refs(getattr(self, name)))
        if self.notice not in ('NONE','DISAGREEMENT','DISAGREEMENT_DETAILS_OMITTED'):
            raise ValueError('Invalid disagreement notice')
        if type(self.review_only) is not bool: raise ValueError('Invalid review lane')
        if self.notice == 'DISAGREEMENT_DETAILS_OMITTED' and (self.units or self.citations or self.conflicts):
            raise ValueError('Compact conflict notice must not show one side')
        object.__setattr__(self, 'audit_codes', strings(self.audit_codes))


@dataclass(frozen=True)
class OmittedGroup:
    group_id: str
    reason: OmissionReason

    def __post_init__(self):
        strings((self.group_id,))
        if type(self.reason) is not OmissionReason: raise ValueError('Invalid omission reason')


@dataclass(frozen=True)
class AgentEvidenceView:
    agent: str
    group_ids: tuple
    usage: BudgetUsage

    def __post_init__(self):
        if self.agent not in ('Melchior','Balthasar','Casper'): raise ValueError('Unknown agent view')
        ids = sequence(self.group_ids, str)
        if len(set(ids)) != len(ids): raise ValueError('Duplicate view group')
        strings(ids)
        object.__setattr__(self, 'group_ids', ids)  # priority order, never sort by identity
        if type(self.usage) is not BudgetUsage: raise ValueError('Invalid view usage')


@dataclass(frozen=True)
class EvidenceSelection:
    assessment: AssessmentSet
    policy: EvidenceSelectionPolicy = field(default_factory=EvidenceSelectionPolicy)
    common_core: tuple = field(init=False)
    omissions: tuple = field(init=False)
    views: tuple = field(init=False)
    usage: BudgetUsage = field(init=False)
    selection_id: str = ''

    def __post_init__(self):
        if type(self.assessment) is not AssessmentSet or type(self.policy) is not EvidenceSelectionPolicy:
            raise ValueError('Invalid selection input')
        validate_container(self.assessment)
        index = _Index(self.assessment)
        if not set(self.policy.required_groups) <= set(index.groups): raise ValueError('Unknown required group')
        chosen, omissions = _select(index, self.policy)
        object.__setattr__(self, 'common_core', chosen)
        object.__setattr__(self, 'omissions', omissions)
        usage = _usage(chosen, index, self.policy)
        object.__setattr__(self, 'usage', usage)
        views = tuple(AgentEvidenceView(agent, tuple(g.group_id for g in sorted(chosen,
            key=lambda g: (*_priority(index, self.policy, g.group_id),
                           0 if index.topics[g.group_id] & preferences else 1, g.group_id))), usage)
            for agent, preferences in _SPECIALTIES.items())
        object.__setattr__(self, 'views', views)
        finish(self, 'selection_id', 'ES')


_SPECIALTIES = {
    'Melchior': frozenset(('FINANCIAL','VALUATION','GUIDANCE','GROWTH','PROFITABILITY','CASH_FLOW',
                          'MANAGEMENT','EARNINGS','CUSTOMER','CONTRACT','PRODUCT','SUPPLIER','SEGMENT')),
    'Balthasar': frozenset(('MARKET_PRICE','MARKET_VOLUME','PRICE_MOVEMENT','MACRO','INDUSTRY',
                           'CATALYST','PRODUCT','PARTNERSHIP','SUPPLY_CHAIN')),
    'Casper': frozenset(('RISK','BALANCE_SHEET','REGULATORY','LEGAL','COMPETITION','SUPPLY_CHAIN')),
}


class _Index:
    """Ephemeral graph index, not a second serialized evidence store."""
    def __init__(self, assessment):
        self.universe = assessment.grouped.universe
        self.groups = {g.group_id:g for g in assessment.grouped.groups}
        self.assessments = {a.group_id:a for a in assessment.assessments}
        self.objects = {reference(s,o):o for s in self.universe.inputs for o in members(s.container)}
        self.parents = {}
        for link in self.universe.lineage:
            if link.asserted_by == 'structured-input-v1' and link.kind == LineageKind.DERIVED_FROM:
                self.parents.setdefault(link.child,set()).add(link.parent)
        self.fitness = {r.reference:r.fitness for a in assessment.assessments for r in a.temporal_records}
        self.eligible = {r for r,f in self.fitness.items() if f == TemporalFitness.AS_OF_COMPATIBLE}
        self.safe = {r for r in self.objects if self.safe_reference(r)}
        self.roots = {k:set(g.members) & self.eligible for k,g in self.groups.items()}
        self.topics = {}; self.families = {}
        for key,g in self.groups.items():
            topics = set(); families = set()
            for r in g.members:
                if r not in self.safe: continue
                o = self.objects[r]
                if type(o) is EvidenceItem and o.category != Category.OTHER: topics.add(o.category.value)
                if type(o) is NewsArticle and o.classification.classification_source and o.classification.event_type.value != 'OTHER':
                    topics.add(o.classification.event_type.value)
            for r in self.roots[key]:
                o = self.objects[r]
                family = ('REGULATORY' if r.object_kind == 'RegulatoryItem' else
                          getattr(o, 'source_type').value)
                families.add(family)
            for basis in self.assessments[key].basis:
                if basis.mapping_id and set(basis.references) <= self.safe and basis.rule_id.startswith('EXPLICIT_'):
                    topics.add(basis.rule_id.removeprefix('EXPLICIT_'))
            self.topics[key] = topics; self.families[key] = families
        self.incomplete_conflicts = set()
        self.conflicts = self._conflicts()
        # Only explicit representation/translation or exact content establishes
        # redundancy. Neither publisher counts nor mere co-membership does so.
        self.components = {r:r for r in self.eligible}
        aliases = {}
        for r in sorted(self.eligible,key=lambda r:r.reference_id):
            key = (r.object_kind,r.content_fingerprint)
            self.merge(r,aliases.setdefault(key,r))
            obj = self.objects[r]
            if type(obj) is NewsArticle:
                origin = obj.metadata.get('syndication_origin'); story = obj.metadata.get('syndication_id')
                if origin and story: self.merge(r,aliases.setdefault(('wire',origin,story),r))
        for link in self.universe.lineage:
            if (link.kind in (LineageKind.TRANSLATION_OF,LineageKind.REPRESENTATION_OF,LineageKind.DERIVED_FROM,LineageKind.VERSION_OF)
                    and link.child in self.eligible and link.parent in self.eligible):
                self.merge(link.child,link.parent)
        self.origins = {k:{self.origin(r) for r in roots} for k,roots in self.roots.items()}

    def origin(self,r):
        while self.components[r] != r: r = self.components[r]
        return r

    def merge(self,a,b):
        a,b = self.origin(a),self.origin(b)
        first,last = sorted((a,b),key=lambda r:r.reference_id)
        self.components[last] = first

    def closure(self,refs):
        found = set(refs); pending = list(refs)
        while pending:
            for parent in self.parents.get(pending.pop(),()):
                if parent not in found: found.add(parent); pending.append(parent)
        return found

    def safe_reference(self,r):
        closure = self.closure((r,))
        roots = closure & set(self.fitness)
        if not roots or not roots <= self.eligible: return False
        # Root publication precision has already been adjudicated by Phase 7E.3.
        # Check the actual derived records' availability without repeatedly resolving
        # the same qualified object through a full container serialization.
        for ref in closure - self.eligible:
            obj = self.objects[ref]
            for name in ('published_at','retrieved_at','created_at','as_of'):
                value = getattr(obj,name,None)
                if isinstance(value,datetime):
                    if value > self.universe.request.as_of: return False
                elif type(value) is date and value >= self.universe.request.as_of.date():
                    return False
        return True

    def _conflicts(self):
        result = []
        for s in self.universe.inputs:
            c = s.container
            evidence = {o.evidence_id:reference(s,o) for o in members(c) if type(o) is EvidenceItem}
            for o in (*getattr(c,'claims',()), *getattr(c,'relations',())):
                if type(o) is ResearchClaim:
                    if o.created_at > self.universe.request.as_of: continue
                    if not (o.contrary_evidence_ids or o.unresolved_evidence_ids): continue
                    ids = (*o.supporting_evidence_ids,*o.contrary_evidence_ids,*o.unresolved_evidence_ids)
                else:
                    if c.created_at > self.universe.request.as_of or o.kind == RelationKind.CORROBORATING: continue
                    ids = o.evidence_ids
                result.append((reference(s,o), frozenset(evidence[k] for k in ids)))
        # Existing attributed assertion keys, not headline/sentiment differences.
        for g in self.groups.values():
            assertions = {}
            for r in g.members:
                o = self.objects[r]
                if r in self.eligible and type(o) is NewsArticle:
                    c = o.classification
                    if c.status.value == 'DISPUTED':
                        result.append((r,frozenset((r,))))
                        self.incomplete_conflicts.add(r)
                    if c.assertion_key and c.classification_source:
                        assertions.setdefault((c.event_namespace,c.event_key,c.assertion_key),{}).setdefault(c.assertion_value,set()).add(r)
            for values in assertions.values():
                if len(values)>1:
                    refs = frozenset(r for rows in values.values() for r in rows)
                    # The original reports themselves carry the assertion metadata.
                    for r in sorted(refs,key=lambda r:r.reference_id): result.append((r,refs))
        return tuple(result)


def _priority(index,policy,key):
    a = index.assessments[key]
    required = key in policy.required_groups or any(b.rule_id == 'REQUESTED_COMPARABLE_METRIC' for b in a.basis)
    disputed = any(refs <= index.safe and set(index.groups[key].members) & refs for _,refs in index.conflicts)
    return (0 if required else 1, list(RelevanceLevel).index(a.relevance),
            list(AttentionLevel).index(a.attention), 0 if disputed else 1)


def _representation(index,key):
    g = index.groups[key]; a = index.assessments[key]
    units = set(); conflicts = set(); affected = set(g.members)
    # Transitive claim closure prevents a second relation hiding a counter-position.
    changed = True
    while changed:
        changed = False
        for ref,refs in index.conflicts:
            if affected & refs and ref not in conflicts:
                conflicts.add(ref); affected.update(refs); units.update(refs); changed = True
    # Requested derived comparisons retain all explicitly cited inputs.
    for b in a.basis:
        if b.rule_id == 'REQUESTED_COMPARABLE_METRIC': units.update(b.references)
    if not units:
        candidates = [r for r in g.members if r in index.safe and r.object_kind == 'EvidenceItem']
        # Prefer a reference that supports the existing assessment; no authority rank.
        support = {r for b in a.basis if b.rule_id in ('SUPPORTED_RELATION_STANDARD','CONTEXT_BACKGROUND') for r in b.references}
        candidates.sort(key=lambda r:(0 if index.closure((r,)) & support else 1,r.reference_id))
        units.add(candidates[0] if candidates else min(index.roots[key],key=lambda r:r.reference_id))
    if conflicts & index.incomplete_conflicts or not units <= index.safe:
        if conflicts: return SelectedGroup(key,(),(),notice='DISAGREEMENT_DETAILS_OMITTED',review_only=a.relevance==RelevanceLevel.UNRESOLVED)
        return None
    citations = index.closure(units) - units
    citations |= {r for r in units if r in index.eligible}
    return SelectedGroup(key,tuple(units),tuple(citations),tuple(conflicts),
                         'DISAGREEMENT' if conflicts else 'NONE',a.relevance==RelevanceLevel.UNRESOLVED)


def _excerpt(value,limit):
    raw = value.encode('utf-8')
    return raw[:limit].decode('utf-8',errors='ignore'),len(raw)>limit


def _render(groups,index,policy):
    rows = []
    for g in groups:
        a = index.assessments[g.group_id]
        units = []
        for r in g.units:
            o = index.objects[r]
            value = getattr(o,'statement',None) or getattr(o,'summary',None) or getattr(o,'title','')
            excerpt,truncated = _excerpt(value,policy.excerpt_bytes)
            units.append({'reference':r.reference_id,'excerpt':excerpt,'truncated':truncated})
        rows.append({'group':g.group_id,'relevance':a.relevance.value,'attention':a.attention.value,
                     'review_only':g.review_only,'notice':g.notice,'units':units,
                     'citations':[r.reference_id for r in g.citations],
                     'conflicts':[r.reference_id for r in g.conflicts],
                     'uncertainties':list(a.uncertainties),'audit_codes':list(g.audit_codes)})
    return json.dumps({'untrusted_evidence':rows},sort_keys=True,ensure_ascii=False,separators=(',',':'))


def _usage(groups,index,policy):
    return BudgetUsage(len(groups),sum(len(g.units) for g in groups),
                       sum(len(set((*g.citations,*g.conflicts))) for g in groups),
                       len(_render(groups,index,policy).encode('utf-8')))


def _fits(groups,index,policy):
    u = _usage(groups,index,policy)
    return (u.groups <= policy.max_groups and u.units <= policy.max_units and
            u.citations <= policy.max_citations and u.rendered_bytes <= policy.max_bytes)


def _select(index,policy):
    from dataclasses import replace
    chosen = []; omissions = {}; remaining = {}; families = {}; topics = set(); origins = set(); reviews = 0
    if not _fits((),index,policy): raise ValueError('Byte budget cannot fit empty view envelope')
    for key,a in index.assessments.items():
        if not index.roots[key]: omissions[key] = OmissionReason.TEMPORAL_EXCLUSION
        elif a.relevance == RelevanceLevel.UNRELATED: omissions[key] = OmissionReason.UNRELATED
        else:
            value = _representation(index,key)
            if value is None: omissions[key] = OmissionReason.UNSUPPORTED_PRESENTATION
            else: remaining[key] = value
    while remaining:
        # Recompute coverage every round, strictly after requirement/relevance/attention.
        best = min(_priority(index,policy,k) for k in remaining)
        comparable = [k for k in remaining if _priority(index,policy,k)==best]
        def diversity(k):
            over = bool(index.families[k]) and all(families.get(f,0)>=policy.soft_family_limit for f in index.families[k])
            return (over,0 if index.topics[k]-topics else 1,k)
        key = min(comparable,key=diversity); g = remaining.pop(key)
        if g.review_only and reviews >= policy.review_slots:
            omissions[key] = OmissionReason.REVIEW_LIMIT; continue
        if index.origins[key] <= origins and not g.conflicts and g.notice == 'NONE' and key not in policy.required_groups:
            omissions[key] = OmissionReason.REDUNDANT_COVERAGE; continue
        if diversity(key)[0]: g = replace(g,audit_codes=('SOFT_FAMILY_CEILING_RELAXED',))
        # A root summary can survive a tighter citation bound than its normalized
        # evidence/source chain. Never substitute it for requested comparisons.
        if (g.notice == 'NONE' and len(g.units) == 1
                and not any(b.rule_id == 'REQUESTED_COMPARABLE_METRIC' for b in index.assessments[key].basis)
                and _usage((*chosen,g),index,policy).citations > policy.max_citations):
            root = min(index.closure(g.units) & index.eligible,key=lambda r:r.reference_id)
            g = replace(g,units=(root,),citations=(root,),audit_codes=(*g.audit_codes,'ROOT_SUMMARY_ONLY'))
        if not _fits((*chosen,g),index,policy) and g.notice == 'DISAGREEMENT':
            g = replace(g,units=(),citations=(),conflicts=(),notice='DISAGREEMENT_DETAILS_OMITTED')
        if not _fits((*chosen,g),index,policy):
            omissions[key] = OmissionReason.BUDGET_LIMIT; continue
        chosen.append(g); reviews += g.review_only
        topics.update(index.topics[key]); origins.update(index.origins[key])
        for f in index.families[key]: families[f] = families.get(f,0)+1
    return tuple(chosen),tuple(OmittedGroup(k,omissions[k]) for k in sorted(omissions))


def render_view(selection,agent=None):
    """Bounded inspectable JSON, not an agent request or an exact token guarantee.

    The enclosing selection/universe is the unbounded audit artifact; only this
    projection is subject to the structural/UTF-8 view budget. Do not send the
    audit serialization as a bounded context.
    """
    if type(selection) is not EvidenceSelection: raise ValueError('Invalid selection')
    validate_container(selection)
    groups = selection.common_core
    if agent is not None:
        view = next((v for v in selection.views if v.agent==agent),None)
        if view is None: raise ValueError('Unknown agent view')
        by_id = {g.group_id:g for g in groups}
        groups = tuple(by_id[k] for k in view.group_ids)
    return _render(groups,_Index(selection.assessment),selection.policy)
