"""Immutable, citation-bearing financial summaries; no I/O or investment claims."""
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple
from .models import EvidenceItem, ResearchSource, FrozenMetadata, freeze, instant, ids
from .security import text


class MetricStatus(str, Enum):
    AVAILABLE='AVAILABLE'
    UNAVAILABLE='UNAVAILABLE'
    CONFLICTED='CONFLICTED'
    NOT_MEANINGFUL='NOT_MEANINGFUL'


class PeriodKind(str, Enum):
    ANNUAL='ANNUAL'
    QUARTER='QUARTER'
    HALF_YEAR='HALF_YEAR'
    NINE_MONTHS='NINE_MONTHS'


class Calculation(str, Enum):
    YOY='YOY'
    MARGIN='MARGIN'


def strings(obj, name):
    values=getattr(obj,name)
    if not isinstance(values,(tuple,list)): raise ValueError('Expected immutable sequence')
    for v in values: text(v)
    object.__setattr__(obj,name,tuple(sorted(set(values))))


@dataclass(frozen=True)
class FinancialPeriod:
    kind: PeriodKind
    start: Optional[date] = None
    end: Optional[date] = None
    business_year: Optional[int] = None
    report_code: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.kind,PeriodKind): raise ValueError('Invalid period kind')
        for value in (self.start,self.end):
            if value is not None and type(value) is not date: raise ValueError('Invalid period date')
        if self.start and (not self.end or self.start>self.end): raise ValueError('Invalid period range')
        if self.business_year is not None and (type(self.business_year) is not int or not 1900<=self.business_year<=9999):
            raise ValueError('Invalid business year')
        if self.report_code is not None and self.report_code not in ('11011','11013','11012','11014'):
            raise ValueError('Invalid report code')
        if self.report_code and {'11011':PeriodKind.ANNUAL,'11013':PeriodKind.QUARTER,
                '11012':PeriodKind.HALF_YEAR,'11014':PeriodKind.NINE_MONTHS}[self.report_code]!=self.kind:
            raise ValueError('Incompatible report semantics')


@dataclass(frozen=True)
class SnapshotIdentity:
    company_name: str
    ticker: str
    market: str
    reporting_currency: str
    provider: str
    provider_issuer_id: str

    def __post_init__(self):
        import re
        for name in self.__dataclass_fields__: text(getattr(self,name))
        if (self.provider,self.market) not in (('SEC','US'),('DART','KR')): raise ValueError('Invalid issuer scope')
        if not re.fullmatch(r'\d{10}' if self.provider=='SEC' else r'\d{8}',self.provider_issuer_id):
            raise ValueError('Invalid issuer identifier')
        if not re.fullmatch('[A-Z]{3}',self.reporting_currency): raise ValueError('Invalid reporting currency')


@dataclass(frozen=True)
class MetricValue:
    value: Optional[Decimal]
    unit: str
    period: Optional[FinancialPeriod]
    evidence_ids: Tuple[str,...] = ()
    status: MetricStatus = MetricStatus.UNAVAILABLE
    warnings: Tuple[str,...] = ()

    def __post_init__(self):
        text(self.unit)
        if not isinstance(self.status,MetricStatus): raise ValueError('Invalid metric status')
        if self.period is not None and not isinstance(self.period,FinancialPeriod): raise ValueError('Invalid period')
        object.__setattr__(self,'evidence_ids',ids(self.evidence_ids));strings(self,'warnings')
        if self.status==MetricStatus.AVAILABLE:
            if not isinstance(self.value,Decimal) or not self.value.is_finite() or not self.evidence_ids or self.period is None:
                raise ValueError('Available values require Decimal, period, and citations')
        elif self.value is not None: raise ValueError('Unavailable values cannot contain a number')
        if self.status==MetricStatus.CONFLICTED and len(self.evidence_ids)<2: raise ValueError('Conflict needs alternatives')


@dataclass(frozen=True)
class BaseMetric:
    name: str
    current: MetricValue
    prior: MetricValue

    def __post_init__(self):
        text(self.name)
        if not isinstance(self.current,MetricValue) or not isinstance(self.prior,MetricValue): raise ValueError('Invalid base metric')


@dataclass(frozen=True)
class DerivedMetric:
    name: str
    value: Optional[Decimal]
    unit: str
    period: Optional[FinancialPeriod]
    calculation: Calculation
    input_evidence_ids: Tuple[str,...]
    status: MetricStatus
    warnings: Tuple[str,...] = ()

    def __post_init__(self):
        text(self.name)
        if not isinstance(self.calculation,Calculation) or self.unit!='ratio': raise ValueError('Invalid calculation')
        check=MetricValue(self.value,self.unit,self.period,self.input_evidence_ids,self.status,self.warnings)
        object.__setattr__(self,'input_evidence_ids',check.evidence_ids)
        object.__setattr__(self,'warnings',check.warnings)


@dataclass(frozen=True)
class ReportingContext:
    current: Optional[FinancialPeriod]
    prior: Optional[FinancialPeriod]
    source_ids: Tuple[str,...]
    latest_source_ids: Tuple[str,...]
    statement_division: Optional[str] = None

    def __post_init__(self):
        for p in (self.current,self.prior):
            if p is not None and not isinstance(p,FinancialPeriod): raise ValueError('Invalid context')
        for name in ('source_ids','latest_source_ids'): object.__setattr__(self,name,ids(getattr(self,name)))
        if not set(self.latest_source_ids)<=set(self.source_ids): raise ValueError('Unknown latest source')
        if self.statement_division not in (None,'CFS','OFS'): raise ValueError('Invalid division')


@dataclass(frozen=True)
class CompanyResearchSnapshot:
    identity: SnapshotIdentity
    reporting_context: ReportingContext
    income_statement: Tuple[BaseMetric,...]
    growth: Tuple[DerivedMetric,...]
    profitability: Tuple[DerivedMetric,...]
    balance_sheet: Tuple[BaseMetric,...]
    cash_flow: Tuple[BaseMetric,...]
    per_share: Tuple[BaseMetric,...]
    evidence_references: Tuple[EvidenceItem,...]
    sources: Tuple[ResearchSource,...]
    warnings: Tuple[str,...]
    coverage: FrozenMetadata
    created_at: datetime
    underlying_pack_id: str
    schema_version: int = 1

    def __post_init__(self):
        instant(self.created_at);text(self.underlying_pack_id)
        if type(self.schema_version) is not int or self.schema_version!=1: raise ValueError('Unsupported snapshot schema')
        if not isinstance(self.identity,SnapshotIdentity) or not isinstance(self.reporting_context,ReportingContext): raise ValueError('Invalid snapshot context')
        for name,cls,key in [('income_statement',BaseMetric,'name'),('growth',DerivedMetric,'name'),
                ('profitability',DerivedMetric,'name'),('balance_sheet',BaseMetric,'name'),('cash_flow',BaseMetric,'name'),
                ('per_share',BaseMetric,'name'),('evidence_references',EvidenceItem,'evidence_id'),('sources',ResearchSource,'source_id')]:
            values=getattr(self,name)
            if not isinstance(values,(tuple,list)) or any(not isinstance(v,cls) for v in values): raise ValueError('Invalid snapshot members')
            if len({getattr(v,key) for v in values})!=len(values): raise ValueError('Duplicate snapshot members')
            object.__setattr__(self,name,tuple(sorted(values,key=lambda v:getattr(v,key))))
        if len(self.sources)>128: raise ValueError('Snapshot source bound exceeded')
        if len(self.evidence_references)>128: raise ValueError('Snapshot evidence bound exceeded; narrow the input pack')
        source_ids={s.source_id for s in self.sources};evidence_ids={e.evidence_id for e in self.evidence_references}
        if any(e.source_id not in source_ids or e.retrieved_at>self.created_at for e in self.evidence_references): raise ValueError('Invalid evidence provenance')
        if any(s.retrieved_at>self.created_at for s in self.sources): raise ValueError('Invalid source time')
        if not set(self.reporting_context.source_ids)<=source_ids: raise ValueError('Invalid reporting sources')
        used=set()
        for section in (self.income_statement,self.balance_sheet,self.cash_flow,self.per_share):
            for metric in section:
                for v in (metric.current,metric.prior): used.update(v.evidence_ids)
        for metric in (*self.growth,*self.profitability): used.update(metric.input_evidence_ids)
        if used!=evidence_ids: raise ValueError('Missing or unreferenced evidence')
        evidence_map={e.evidence_id:e for e in self.evidence_references}
        for section in (self.income_statement,self.balance_sheet,self.cash_flow,self.per_share):
            for metric in section:
                for v in (metric.current,metric.prior):
                    if v.status==MetricStatus.AVAILABLE and any(evidence_map[ref].value!=v.value for ref in v.evidence_ids):
                        raise ValueError('Metric does not match cited official values')
        from .snapshot_policy import INCOME, BALANCE, CASH_FLOW, PER_SHARE
        expected={}
        for name,names in [('income_statement',INCOME),('balance_sheet',BALANCE),('cash_flow',CASH_FLOW),('per_share',PER_SHARE)]:
            section=getattr(self,name)
            if {m.name for m in section}!=set(names): raise ValueError('Invalid metric section')
            count=sum(m.current.status==MetricStatus.AVAILABLE for m in section)
            expected[name]=name.upper()+('_COMPLETE' if count==len(names) else '_PARTIAL' if count else '_UNAVAILABLE')
        if not isinstance(self.coverage,Mapping) or dict(self.coverage)!=expected: raise ValueError('Invalid coverage')
        # Validate serialized calculations against their cited base values as well.
        from .snapshot import derive
        base={m.name:m for section in (self.income_statement,self.balance_sheet,self.cash_flow,self.per_share) for m in section}
        expected_growth={derive(n+'_yoy',base[n].current,base[n].prior,Calculation.YOY) for n in (*INCOME,*PER_SHARE)}
        expected_margins={derive(n+'_margin',base[n+'_income'].current,base['revenue'].current,Calculation.MARGIN) for n in ('operating','net')}
        if set(self.growth)!=expected_growth or set(self.profitability)!=expected_margins: raise ValueError('Invalid derived calculations')
        strings(self,'warnings');object.__setattr__(self,'coverage',freeze(self.coverage))

    def metric(self,name):
        return next(m for section in (self.income_statement,self.balance_sheet,self.cash_flow,self.per_share,self.growth,self.profitability)
                    for m in section if m.name==name)
