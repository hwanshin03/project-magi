"""Immutable orchestration contracts, reusing existing identities and decisions."""
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
import json
from magi.decision import AgentResult, Availability as AgentAvailability, Position
from magi.voting import VotingEngine
from magi.research.models import identity
from magi.research.security import safe_text
from magi.research.serialization import encode, decode
from magi.research.balancing.models import TargetIdentity, SelectionRequest, InputFamily, strings, fingerprint
from magi.research.balancing.inputs import adapt, validate_container
from magi.research.balancing.assessment import AssessmentPolicy
from magi.research.balancing.selection import EvidenceSelection, EvidenceSelectionPolicy

CHANNELS = {'sec': InputFamily.RESEARCH, 'dart': InputFamily.RESEARCH,
            'snapshot': InputFamily.FINANCIAL_SNAPSHOT, 'news': InputFamily.NEWS,
            'company': InputFamily.NEWS, 'regulatory': InputFamily.REGULATORY}


class AnalysisError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def checked_id(obj, name, prefix, payload):
    computed = identity('', prefix, payload)
    if getattr(obj, name) not in ('', computed): raise ValueError('Forged analysis identity')
    object.__setattr__(obj, name, computed)


@dataclass(frozen=True)
class AnalysisRequest:
    target: TargetIdentity
    question: str
    as_of: datetime
    requested: tuple = ()
    required: tuple = ()
    languages: tuple = ()
    scope: str | None = None
    history: str = ''
    assessment_policy: AssessmentPolicy = field(default_factory=AssessmentPolicy)
    selection_policy: EvidenceSelectionPolicy = field(default_factory=EvidenceSelectionPolicy)
    year: int | None = None
    report: str = 'annual'
    currency: str | None = None
    division: str = 'CFS'
    limit: int = 20
    regulatory_sources: tuple = ('bis_rules', 'sec_rules', 'fsc_releases')
    version: str = '7F-v1'
    request_id: str = ''

    def __post_init__(self):
        from magi.research.regulatory.catalog import source_spec
        for name in ('requested','required','languages','regulatory_sources'):
            object.__setattr__(self, name, strings(getattr(self, name)))
        if not set(self.requested) <= CHANNELS.keys() or not set(self.required) <= set(self.requested):
            raise ValueError('Invalid requested/required collection channels')
        if self.version != '7F-v1': raise ValueError('Unsupported analysis version')
        if not isinstance(self.question,str) or not self.question.strip() or len(self.question)>4000:
            raise ValueError('Invalid analysis question')
        safe_text(self.question); safe_text(self.history)
        validate_container(self.selection_request)
        if type(self.assessment_policy) is not AssessmentPolicy or type(self.selection_policy) is not EvidenceSelectionPolicy:
            raise ValueError('Invalid analysis policies')
        validate_container(self.assessment_policy); validate_container(self.selection_policy)
        if self.assessment_policy.published_since and self.assessment_policy.published_since>self.as_of:
            raise ValueError('Invalid research horizon')
        if self.year is not None and (type(self.year) is not int or not 1900<=self.year<=self.as_of.year):
            raise ValueError('Invalid financial year')
        if self.report not in ('annual','quarter','q1','half','q3') or self.division not in ('CFS','OFS'):
            raise ValueError('Invalid financial request')
        if self.currency is not None:
            import re
            if not isinstance(self.currency,str) or not re.fullmatch('[A-Z]{3}',self.currency): raise ValueError('Invalid currency')
        if type(self.limit) is not int or not 1<=self.limit<=100: raise ValueError('Invalid collection limit')
        for key in self.regulatory_sources: source_spec(key)
        if 'regulatory' in self.requested and not self.regulatory_sources: raise ValueError('Regulatory sources required')
        checked_id(self,'request_id','AR',request_data(self,include_id=False))

    @property
    def selection_request(self):
        return SelectionRequest(self.target,self.as_of,self.scope if self.scope is not None else self.question,self.languages)


@dataclass(frozen=True)
class ResearchInput:
    """Normalized collection boundary. No response bodies or exception text."""
    channel: str
    containers: tuple = ()
    error_codes: tuple = ()
    omissions: tuple = ()

    def __post_init__(self):
        if self.channel not in CHANNELS: raise ValueError('Unknown collection channel')
        if not isinstance(self.containers,(tuple,list)): raise ValueError('Invalid normalized inputs')
        unique = {}
        for container in self.containers:
            snapshot = adapt(container)
            if snapshot.family != CHANNELS[self.channel]: raise ValueError('Collection family mismatch')
            unique[snapshot.snapshot_id] = container
        object.__setattr__(self,'containers',tuple(unique[k] for k in sorted(unique)))
        from magi.research.balancing.models import InputAvailability, Availability
        for name in ('error_codes','omissions'): object.__setattr__(self,name,strings(getattr(self,name)))
        # Reuse the foundation's static-code validation without inventing a new grammar.
        if self.error_codes:
            InputAvailability(CHANNELS[self.channel],Availability.UNAVAILABLE,error_codes=self.error_codes)
        if not self.containers and not self.error_codes: raise ValueError('Missing data requires an explicit failure')

    @property
    def complete(self):
        from magi.research.balancing.inputs import record_count
        return (bool(self.containers) and not (self.error_codes or self.omissions)
                and not any(getattr(c,'selection_omissions',()) for c in self.containers)
                and any(record_count(c) for c in self.containers))


def request_data(request, *, include_id=True):
    return {f.name:encode(getattr(request,f.name)) for f in fields(request) if include_id or f.name!='request_id'}


@dataclass(frozen=True)
class AnalysisResult:
    request: AnalysisRequest
    selection: EvidenceSelection
    agent_results: tuple
    explanation: str | None = None
    explanation_error: str | None = None
    artifact_id: str = ''
    vote: object = field(init=False)

    def __post_init__(self):
        if type(self.request) is not AnalysisRequest or replace(self.request)!=self.request:
            raise ValueError('Invalid analysis request')
        if type(self.selection) is not EvidenceSelection: raise ValueError('Invalid selection')
        validate_container(self.selection)
        if self.selection.assessment.grouped.universe.request != self.request.selection_request:
            raise ValueError('Analysis request mismatch')
        if self.selection.policy != self.request.selection_policy or self.selection.assessment.policy != self.request.assessment_policy:
            raise ValueError('Analysis policy mismatch')
        if not isinstance(self.agent_results,(tuple,list)) or len(self.agent_results)!=3:
            raise ValueError('Three agent results required')
        results={}
        for result in self.agent_results:
            if type(result) is not AgentResult or replace(result)!=result or result.agent in results:
                raise ValueError('Invalid agent result')
            safe_text(str(result)); results[result.agent]=result
        vote=VotingEngine().vote(results)
        object.__setattr__(self,'vote',vote)
        object.__setattr__(self,'agent_results',vote.agent_results)
        if self.explanation is not None: safe_text(self.explanation)
        if self.explanation_error not in (None,'EXPLANATION_UNAVAILABLE'): raise ValueError('Invalid explanation failure')
        if self.explanation is not None and self.explanation_error is not None: raise ValueError('Conflicting explanation state')
        checked_id(self,'artifact_id','AN',fingerprint((self.request.request_id,self.selection.selection_id,
            tuple(str(r) for r in self.agent_results),self.explanation,self.explanation_error)))

    @property
    def universe(self): return self.selection.assessment.grouped.universe

    @property
    def warnings(self):
        return tuple(code for condition,code in (
            (self.request.target.entity is None,'ISSUER_MAPPING_NOT_SUPPLIED'),
            (True,'MARKET_PROVENANCE_DEFERRED'),
            (not self.selection.common_core,'NO_SELECTED_EVIDENCE')) if condition)


def dumps(result):
    """Explicit lossless export only; never writes a file or production database."""
    if type(result) is not AnalysisResult: raise ValueError('Expected analysis result')
    return json.dumps({'schema':'7F-v1','request':request_data(result.request),'selection':encode(result.selection),
        'agent_results':[json.loads(str(r)) for r in result.agent_results],
        'explanation':result.explanation,'explanation_error':result.explanation_error,'artifact_id':result.artifact_id},
        sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def loads(value):
    from magi.research.serialization import _pairs
    try:
        raw=json.loads(value,object_pairs_hook=_pairs)
        if set(raw)!={'schema','request','selection','agent_results','explanation','explanation_error','artifact_id'} or raw['schema']!='7F-v1':
            raise ValueError('Invalid analysis serialization')
        request=AnalysisRequest(**{k:decode(v) for k,v in raw['request'].items()})
        results=[]
        for row in raw['agent_results']:
            row['position']=Position(row['position']) if row['position'] is not None else None
            row['availability']=AgentAvailability(row['availability'])
            results.append(AgentResult(**row))
        return AnalysisResult(request,decode(raw['selection']),tuple(results),raw['explanation'],raw['explanation_error'],raw['artifact_id'])
    except (KeyError,TypeError,OverflowError,RecursionError):
        raise ValueError('Invalid analysis serialization') from None
