"""Pure report-aware SEC projection. The original catalog is retained verbatim."""
from dataclasses import dataclass, field
from datetime import date, datetime
from .models import EvidencePack, SourceType, Authority, instant
from .snapshot import _sec_period, comparable, REPORTS
from .snapshot_policy import SEC, SEC_CONCEPTS, BALANCE
from .balancing.models import fingerprint, label, strings
from .balancing.inputs import validate_container
from .validation import operation


@dataclass(frozen=True)
class SECProjectionPolicy:
    as_of: datetime
    scope: str
    report: str = 'annual'
    year: int | None = None
    concepts: tuple = SEC_CONCEPTS
    published_since: datetime | None = None
    currency: str | None = None
    version: str = 'sec-comparable-periods-v1'

    @operation
    def __post_init__(self):
        instant(self.as_of); label(self.scope, 4000)
        if self.version != 'sec-comparable-periods-v1' or self.report not in ('annual','quarter'):
            raise ValueError('Unsupported SEC projection policy')
        if self.year is not None and (type(self.year) is not int or not 1900 <= self.year <= self.as_of.year):
            raise ValueError('Invalid SEC projection year')
        object.__setattr__(self, 'concepts', strings(self.concepts))
        if not self.concepts or not set(self.concepts) <= set(SEC_CONCEPTS):
            raise ValueError('Unsupported SEC projection concepts')
        if self.published_since is not None:
            instant(self.published_since)
            if self.published_since > self.as_of: raise ValueError('Invalid SEC projection horizon')
        if self.currency is not None:
            import re
            if not isinstance(self.currency, str) or not re.fullmatch('[A-Z]{3}', self.currency):
                raise ValueError('Invalid SEC projection currency')


def _publication(source, boundary):
    value = source.published_at
    if value is None: return False
    if source.metadata.get('publication_precision') in ('DATE', 'date; midnight UTC convention'):
        # A date on the cutoff day cannot establish intraday availability.
        return value.date() < boundary.date()
    return value <= boundary


def _project(catalog, policy):
    sources = {s.source_id:s for s in catalog.sources}
    if catalog.market != 'US' or catalog.claims or catalog.relations:
        raise ValueError('Expected normalized SEC financial catalog without analytical claims')
    if any(s.source_type != SourceType.SEC_FILING or s.authority != Authority.PRIMARY
           or s.provider != 'SEC' for s in catalog.sources):
        raise ValueError('SEC projection requires official SEC sources')
    scopes = {(s.metadata.get('issuer_id'),s.market,s.ticker) for s in catalog.sources}
    if len(scopes) > 1: raise ValueError('SEC projection requires one issuer')
    for s in catalog.sources:
        issuer = s.metadata.get('issuer_id')
        if (not isinstance(issuer,str) or not issuer.startswith('SEC:')
                or s.metadata.get('cik') != issuer[4:] or s.market != catalog.market
                or s.ticker != catalog.ticker or s.company_name != catalog.subject):
            raise ValueError('SEC catalog issuer mismatch')
    if any(e.subject != catalog.subject or e.ticker != catalog.ticker for e in catalog.evidence_items):
        raise ValueError('SEC observation issuer mismatch')
    kind = REPORTS[policy.report][0]
    balance = {c for key in BALANCE for c in SEC[key]}
    eligible = {}; reasons = {}; periods = {}
    for e in catalog.evidence_items:
        s = sources[e.source_id]; concept = e.metadata.get('concept')
        reason = None
        if (catalog.created_at > policy.as_of or e.retrieved_at > policy.as_of
                or s.retrieved_at > policy.as_of): reason = 'AFTER_AS_OF'
        elif not _publication(s, policy.as_of): reason = 'PUBLICATION_UNAVAILABLE_AT_AS_OF'
        elif e.period_end is None or e.period_end > policy.as_of.date(): reason = 'PERIOD_UNAVAILABLE_AT_AS_OF'
        elif e.metadata.get('taxonomy') != 'us-gaap' or concept not in policy.concepts: reason = 'OUTSIDE_CONCEPT_SCOPE'
        elif policy.currency and e.unit not in (policy.currency, policy.currency+'/shares'): reason = 'OUTSIDE_CURRENCY_SCOPE'
        if reason is None:
            period = _sec_period(e,s,kind)
            if concept in balance and e.period_start is None:
                forms = ('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A') if policy.report == 'annual' else ('10-Q','10-Q/A','10-K','10-K/A')
                if e.metadata.get('form',s.document_type) not in forms or e.metadata.get('form',s.document_type) != s.document_type:
                    reason = 'REPORT_KIND_MISMATCH'
            elif period is None: reason = 'REPORT_KIND_MISMATCH'
            else: periods[e.evidence_id] = period
        if reason: reasons[e.evidence_id] = reason
        else: eligible[e.evidence_id] = e
    all_periods = set(periods.values())
    anchors = {p for p in all_periods if policy.year is None or p.end.year == policy.year}
    if anchors:
        latest = max(p.end for p in anchors)
        # Preserve ambiguous intervals; Phase 7C will report ambiguity, not guess.
        anchors = {p for p in anchors if p.end == latest}
    if policy.published_since:
        anchors |= {p for key,p in periods.items() if sources[eligible[key].source_id].published_at >= policy.published_since
                    and (policy.year is None or p.end.year <= policy.year)}
    selected_periods = anchors | {p for p in all_periods if any(comparable(a,p) for a in anchors)}
    balance_ends = {p.end for p in anchors}
    if not anchors:
        # Same instant-only fallback as Phase 7C: official report dates only.
        forms = ('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A') if policy.report == 'annual' else ('10-Q','10-Q/A')
        ends = {date.fromisoformat(s.metadata['report_date']) for s in sources.values()
                if s.document_type in forms and s.metadata.get('report_date') and _publication(s,policy.as_of)
                and s.retrieved_at <= policy.as_of}
        ends = {d for d in ends if d <= policy.as_of.date() and (policy.year is None or d.year == policy.year)}
        if ends: balance_ends = {max(ends)}
    selected = []
    for key,e in eligible.items():
        keep = periods.get(key) in selected_periods or (
            e.metadata.get('concept') in balance and e.period_start is None and e.period_end in balance_ends)
        reasons[key] = 'SELECTED_REQUIRED_PERIOD' if keep else 'OUTSIDE_REQUIRED_PERIODS'
        if keep: selected.append(e)
    used = {e.source_id for e in selected}
    projected = EvidencePack(catalog.subject,catalog.created_at,tuple(s for s in catalog.sources if s.source_id in used),
        tuple(selected),ticker=catalog.ticker,market=catalog.market,stale_after_days=catalog.stale_after_days)
    return projected, tuple(sorted(reasons.items()))


@dataclass(frozen=True)
class SECAnalyticalProjection:
    catalog: EvidencePack
    policy: SECProjectionPolicy
    catalog_fingerprint: str = field(init=False)
    analytical_pack: EvidencePack = field(init=False)
    audit: tuple = field(init=False)
    projection_id: str = field(init=False)

    @operation
    def __post_init__(self):
        if type(self.catalog) is not EvidencePack or type(self.policy) is not SECProjectionPolicy:
            raise ValueError('Invalid SEC projection inputs')
        validate_container(self.catalog); validate_container(self.policy)
        projected, audit = _project(self.catalog,self.policy)
        catalog_id = fingerprint(self.catalog)
        object.__setattr__(self,'catalog_fingerprint',catalog_id)
        object.__setattr__(self,'analytical_pack',projected)
        object.__setattr__(self,'audit',audit)
        object.__setattr__(self,'projection_id','SP_'+fingerprint((catalog_id,self.policy,
            tuple((e.evidence_id,e.source_id,fingerprint(e)) for e in projected.evidence_items),audit))[3:])
