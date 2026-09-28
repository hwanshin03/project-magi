"""Pure deterministic selection from official evidence. No provider calls or storage."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from .models import EvidencePack, Authority, SourceType
from .snapshot_models import (MetricStatus as Status, PeriodKind as Kind, Calculation,
    FinancialPeriod as Period, SnapshotIdentity, MetricValue, BaseMetric, DerivedMetric,
    ReportingContext, CompanyResearchSnapshot)
from .snapshot_policy import SEC, DART, INCOME, BALANCE, CASH_FLOW, PER_SHARE

REPORTS={'annual':(Kind.ANNUAL,'11011'),'quarter':(Kind.QUARTER,None),
         'q1':(Kind.QUARTER,'11013'),'half':(Kind.HALF_YEAR,'11012'),'q3':(Kind.NINE_MONTHS,'11014')}


def missing(unit,period,warning):
    return MetricValue(None,unit,period,warnings=(warning,))


def derive(name,numerator,denominator,calculation):
    """Fractions, not percent-scaled values. Fixed precision independent of callers."""
    refs=tuple(sorted(set(numerator.evidence_ids+denominator.evidence_ids)))
    warnings=set(numerator.warnings+denominator.warnings)
    status=Status.AVAILABLE;value=None
    if Status.CONFLICTED in (numerator.status,denominator.status): status=Status.CONFLICTED
    elif any(v.status!=Status.AVAILABLE for v in (numerator,denominator)): status=Status.UNAVAILABLE
    elif numerator.unit!=denominator.unit:
        status=Status.UNAVAILABLE;warnings.add('INCOMPARABLE_UNITS')
    elif calculation==Calculation.MARGIN and numerator.period!=denominator.period:
        status=Status.UNAVAILABLE;warnings.add('INCOMPARABLE_PERIOD')
    elif calculation==Calculation.YOY and not comparable(numerator.period,denominator.period):
        status=Status.UNAVAILABLE;warnings.add('INCOMPARABLE_PERIOD')
    elif denominator.value<=0:
        status=Status.NOT_MEANINGFUL
        warnings.add('ZERO_DENOMINATOR' if denominator.value==0 else 'NEGATIVE_PRIOR_VALUE' if calculation==Calculation.YOY else 'NONPOSITIVE_REVENUE')
    else:
        with localcontext() as ctx:
            ctx.prec=34;ctx.rounding=ROUND_HALF_EVEN
            value=((numerator.value-denominator.value)/abs(denominator.value)
                   if calculation==Calculation.YOY else numerator.value/denominator.value)
    return DerivedMetric(name,value,'ratio',numerator.period,calculation,refs,status,tuple(warnings))


def comparable(current,prior):
    if current is None or prior is None or current.kind!=prior.kind: return False
    if current.business_year is not None or prior.business_year is not None:
        if not (current.business_year is not None and prior.business_year==current.business_year-1
                and current.report_code==prior.report_code): return False
        if not any((current.start,current.end,prior.start,prior.end)): return True
        # If explicit dates exist, they must support the report-label comparison.
    return bool(current.start and prior.start and current.end and prior.end
                and 350<=(current.end-prior.end).days<=380
                and 350<=(current.start-prior.start).days<=380
                and abs((current.end-current.start).days-(prior.end-prior.start).days)<=10)


def _sec_period(e,s,kind):
    form=e.metadata.get('form',s.document_type)
    fp=e.metadata.get('fp')
    fy=e.metadata.get('fy')
    if fy is not None and (type(fy) is not int or not 1900<=fy<=9999): return None
    if s.document_type!=form: return None
    if kind==Kind.ANNUAL:
        if form not in ('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A') or fp not in (None,'FY'): return None
        low,high=330,380
    elif kind==Kind.QUARTER:
        if form not in ('10-Q','10-Q/A','10-K','10-K/A') or fp not in (None,'Q1','Q2','Q3','FY'): return None
        low,high=70,105
    else: return None
    if not e.period_start or not e.period_end or not low<=(e.period_end-e.period_start).days<=high: return None
    return Period(kind,e.period_start,e.period_end)


def _dart_field(name,kind,prior):
    if name in BALANCE: return None if prior else 'thstrm_amount'
    if kind==Kind.ANNUAL: return 'frmtrm_amount' if prior else 'thstrm_amount'
    if name in CASH_FLOW:
        # Interim cash-flow current amounts are cumulative. Prior labels alone do
        # not establish a comparable interval; no interim cash-flow comparison.
        return None if prior else 'thstrm_amount'
    if kind==Kind.QUARTER: return 'frmtrm_q_amount' if prior else 'thstrm_amount'
    return 'frmtrm_add_amount' if prior else 'thstrm_add_amount'


def build_snapshot(pack,*,report='annual',year=None,currency=None,division='CFS'):
    if not isinstance(pack,EvidencePack) or report not in REPORTS or division not in ('CFS','OFS'):
        raise ValueError('Invalid snapshot request')
    kind,report_code=REPORTS[report]
    if year is not None and (type(year) is not int or not 1900<=year<=pack.created_at.year): raise ValueError('Invalid business year')
    sources={s.source_id:s for s in pack.sources}
    official=[s for s in pack.sources if s.authority==Authority.PRIMARY and s.source_type in (SourceType.SEC_FILING,SourceType.DART_FILING)]
    scopes={(s.provider,s.metadata.get('issuer_id'),s.market,s.ticker) for s in official}
    if len(scopes)!=1: raise ValueError('Snapshot requires one official issuer scope')
    provider,issuer_id,market,ticker=next(iter(scopes))
    if provider not in ('SEC','DART') or not issuer_id or not issuer_id.startswith(provider+':') or ticker!=pack.ticker or market!=pack.market:
        raise ValueError('Issuer does not match pack')
    if provider=='SEC' and report not in ('annual','quarter'): raise ValueError('SEC supports annual or quarter snapshots')
    if provider=='DART' and report=='quarter': raise ValueError('Use explicit DART q1, half, or q3 semantics')
    if any(s.company_name!=pack.subject or s.metadata.get('cik' if provider=='SEC' else 'corp_code')!=issuer_id.split(':',1)[1]
           for s in official): raise ValueError('Inconsistent official issuer metadata')
    policy=SEC if provider=='SEC' else DART
    official_ids={s.source_id for s in official}
    entries=[]
    for e in pack.evidence_items:
        if e.source_id not in official_ids: continue
        if e.ticker!=ticker or e.subject!=pack.subject: raise ValueError('Evidence issuer mismatch')
        if e.value is not None and (not isinstance(e.value,Decimal) or not e.value.is_finite()): continue
        s=sources[e.source_id]
        if provider=='SEC' and e.metadata.get('taxonomy')!='us-gaap': continue
        if provider=='DART' and e.metadata.get('statement_division')!=division: continue
        concept=e.metadata.get('concept' if provider=='SEC' else 'account_id')
        for name,concepts in policy.items():
            if concept not in concepts: continue
            if provider=='DART':
                expected=('BS',) if name in BALANCE else ('CF',) if name in CASH_FLOW else ('IS','CIS')
                if e.metadata.get('sj_div') not in expected: continue
                if e.metadata.get('account_detail') not in (None,'','-','연결재무제표','연결재무제표 [member]','별도재무제표'): continue
            entries.append((name,concepts.index(concept),e,s))
    currencies={e.unit.split('/')[0] for name,_,e,_ in entries if e.unit and len(e.unit.split('/')[0])==3 and e.unit.split('/')[0].isupper()}
    if currency is None:
        if len(currencies)!=1: raise ValueError('Specify one unambiguous reporting currency')
        currency=next(iter(currencies))
    identity=SnapshotIdentity(pack.subject,ticker,market,currency,provider,issuer_id.split(':',1)[1])
    entries=[r for r in entries if r[2].unit==(currency+'/shares' if provider=='SEC' and r[0] in PER_SHARE else currency)]
    warnings=set();current=None;prior=None
    if provider=='SEC':
        periods={_sec_period(e,s,kind) for name,_,e,s in entries if name not in BALANCE}
        periods={p for p in periods if p is not None and p.end<=pack.created_at.date()}
        if periods:
            end=max(p.end for p in periods);latest={p for p in periods if p.end==end}
            if len(latest)!=1: raise ValueError('Ambiguous official reporting intervals; narrow the evidence pack')
            current=next(iter(latest))
            candidates={p for p in periods if comparable(current,p)}
            if len(candidates)==1: prior=next(iter(candidates))
            elif candidates: warnings.add('INCOMPARABLE_PERIOD')
        else:
            # An instant-only pack can still support a balance sheet. Do not
            # invent a duration start or infer income/cash-flow observations.
            report_dates=[]
            forms=('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A') if kind==Kind.ANNUAL else ('10-Q','10-Q/A')
            for s in official:
                if s.document_type in forms and s.metadata.get('report_date'):
                    end=date.fromisoformat(s.metadata['report_date'])
                    if end<=pack.created_at.date(): report_dates.append(end)
            if report_dates: current=Period(kind,end=max(report_dates))
    else:
        years={e.metadata.get('business_year') for _,_,e,_ in entries if e.metadata.get('report_code')==report_code}
        years={y for y in years if type(y) is int and y<=pack.created_at.year and (year is None or y==year)}
        if years:
            selected_year=max(years)
            current=Period(kind,business_year=selected_year,report_code=report_code)
            prior=Period(kind,business_year=selected_year-1,report_code=report_code)
    if current is None: warnings.add('INCOMPARABLE_PERIOD')
    if prior is None: warnings.add('MISSING_PRIOR_PERIOD')

    def select_value(name,is_prior):
        target=prior if is_prior else current
        unit=currency+'/shares' if name in PER_SHARE else currency
        if target is None: return missing(unit,None,'MISSING_PRIOR_PERIOD' if is_prior else 'INCOMPARABLE_PERIOD')
        candidates=[]
        for metric,rank,e,s in entries:
            if metric!=name: continue
            if provider=='SEC':
                if name in BALANCE:
                    if is_prior: continue
                    if e.period_start is not None or e.period_end!=target.end: continue
                    if e.metadata.get('form',s.document_type) not in (('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A') if kind==Kind.ANNUAL else ('10-Q','10-Q/A','10-K','10-K/A')): continue
                elif _sec_period(e,s,kind)!=target: continue
            else:
                if e.metadata.get('business_year')!=current.business_year or e.metadata.get('report_code')!=report_code: continue
                field=_dart_field(name,kind,is_prior)
                if field is None or e.metadata.get('amount_field')!=field: continue
                if e.period_end and e.period_end>pack.created_at.date(): continue
            # Missing publication dates cannot outrank dated official filings.
            published=s.published_at or datetime.min.replace(tzinfo=timezone.utc)
            candidates.append((published,-rank,e,s))
        if not candidates: return missing(unit,target,'MISSING_PRIOR_PERIOD' if is_prior else 'MISSING_'+name.upper())
        best=max((r[0],r[1]) for r in candidates)
        winners=[r for r in candidates if r[:2]==best]
        # Distinct explicit intervals must never be merged on DART's year label.
        contexts={(e.period_start,e.period_end) for _,_,e,_ in winners if e.period_start or e.period_end}
        vals={r[2].value for r in winners};notes=set()
        refs=tuple(sorted({r[2].evidence_id for r in winners}))
        if vals=={None}: return MetricValue(None,unit,target,refs,Status.UNAVAILABLE,('MISSING_PRIOR_PERIOD' if is_prior else 'MISSING_'+name.upper(),))
        if len(vals)>1 or len(contexts)>1:
            return MetricValue(None,unit,target,refs,Status.CONFLICTED,('CONFLICTING_OFFICIAL_FACTS',))
        if any(s.metadata.get('amendment') or s.metadata.get('correction_marker') for _,_,_,s in winners): notes.add('RESTATED_VALUE')
        if any(r[2].value not in vals and r[0]<best[0] for r in candidates): notes.add('RESTATED_VALUE')
        if provider=='DART':
            if contexts:
                start,end=next(iter(contexts))
                target=Period(target.kind,start,end,target.business_year,target.report_code)
            else: notes.add('PERIOD_DATES_NOT_SUPPLIED')
        if name in BALANCE and target.start is not None:
            target=Period(target.kind,end=target.end,business_year=target.business_year,report_code=target.report_code)
        return MetricValue(next(iter(vals)),unit,target,refs,Status.AVAILABLE,tuple(notes))

    metrics={name:BaseMetric(name,select_value(name,False),select_value(name,True)) for name in policy}
    growth=tuple(derive(name+'_yoy',metrics[name].current,metrics[name].prior,Calculation.YOY) for name in (*INCOME,*PER_SHARE))
    profitability=tuple(derive(name+'_margin',metrics[name+'_income'].current,metrics['revenue'].current,Calculation.MARGIN) for name in ('operating','net'))
    refs={ref for m in metrics.values() for v in (m.current,m.prior) for ref in v.evidence_ids}
    evidence=tuple(e for e in pack.evidence_items if e.evidence_id in refs)
    used_sources={e.source_id for e in evidence}
    # Retain official context even when no metric can be selected.
    context_sources=used_sources or {s.source_id for s in official if s.published_at==max((x.published_at for x in official if x.published_at),default=None)}
    selected_sources=tuple(s for s in official if s.source_id in context_sources)
    latest_date=max((s.published_at for s in selected_sources if s.published_at),default=None)
    latest_ids=tuple(s.source_id for s in selected_sources if s.published_at==latest_date)
    for m in metrics.values():
        warnings.update(m.current.warnings)
        if m.name in (*INCOME,*PER_SHARE): warnings.update(m.prior.warnings)
    for m in (*growth,*profitability): warnings.update(m.warnings)
    if latest_date and pack.created_at-latest_date>timedelta(days=pack.stale_after_days): warnings.add('STALE_FILING')
    if current and current.end and (pack.created_at.date()-current.end).days>(550 if kind==Kind.ANNUAL else 200): warnings.add('STALE_FILING')
    if any(s.published_at is None for s in selected_sources): warnings.add('MISSING_PUBLICATION_DATE')
    coverage={}
    for name,keys in [('income_statement',INCOME),('balance_sheet',BALANCE),('cash_flow',CASH_FLOW),('per_share',PER_SHARE)]:
        n=sum(metrics[k].current.status==Status.AVAILABLE for k in keys)
        coverage[name]=name.upper()+('_COMPLETE' if n==len(keys) else '_PARTIAL' if n else '_UNAVAILABLE')
    return CompanyResearchSnapshot(identity,ReportingContext(current,prior,tuple(context_sources),latest_ids,division if provider=='DART' else None),
        tuple(metrics[n] for n in INCOME),growth,profitability,tuple(metrics[n] for n in BALANCE),
        tuple(metrics[n] for n in CASH_FLOW),tuple(metrics[n] for n in PER_SHARE),evidence,selected_sources,
        tuple(warnings),coverage,pack.created_at,pack.pack_id)
