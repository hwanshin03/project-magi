"""Bounded bilingual facts, not generated commentary. Evidence stays untrusted."""
from decimal import Decimal, localcontext
from .security import safe_text
from .context import ResearchContext, POLICY
from .snapshot_models import CompanyResearchSnapshot, MetricStatus

LABELS={
 'en':{'title':'Company financial snapshot','current':'Current','prior':'Prior comparable period',
       'revenue':'Revenue','operating_income':'Operating income','net_income':'Net income',
       'eps_basic':'Basic EPS','eps_diluted':'Diluted EPS','operating_margin':'Operating margin','net_margin':'Net margin',
       'assets':'Total assets','liabilities':'Total liabilities','equity':'Equity','cash':'Cash and cash equivalents','debt':'Debt',
       'operating_cash_flow':'Operating cash flow','investing_cash_flow':'Investing cash flow','financing_cash_flow':'Financing cash flow',
       'capex':'PP&E capital expenditures','warnings':'Warnings','sources':'Sources','evidence':'Evidence',
       'coverage':'Coverage','period':'Reporting period','filing':'Latest filing/report','unknown':'Not supplied'},
 'ko':{'title':'기업 재무 스냅샷','current':'당기','prior':'비교 가능한 전기',
       'revenue':'매출액','operating_income':'영업이익','net_income':'순이익',
       'eps_basic':'기본주당이익','eps_diluted':'희석주당이익','operating_margin':'영업이익률','net_margin':'순이익률',
       'assets':'총자산','liabilities':'총부채','equity':'자본','cash':'현금 및 현금성자산','debt':'차입금',
       'operating_cash_flow':'영업활동 현금흐름','investing_cash_flow':'투자현금흐름','financing_cash_flow':'재무현금흐름',
       'capex':'유형자산 취득 지출','warnings':'경고','sources':'출처','evidence':'근거',
       'coverage':'데이터 범위','period':'보고기간','filing':'최신 공시/보고서','unknown':'미제공'},
}
STATUS={'en':{'AVAILABLE':'Available','UNAVAILABLE':'Unavailable','CONFLICTED':'Conflicted','NOT_MEANINGFUL':'Not meaningful'},
        'ko':{'AVAILABLE':'확인됨','UNAVAILABLE':'자료 없음','CONFLICTED':'공식 자료 상충','NOT_MEANINGFUL':'비교 의미 없음'}}


def period_label(p):
    if p is None: return 'UNKNOWN'
    dates=(str(p.start) if p.start else '?')+'..'+(str(p.end) if p.end else '?')
    return ' '.join(str(v) for v in (p.kind.value,p.business_year,p.report_code,dates) if v is not None)


def render_snapshot(snapshot,*,language='ko',max_characters=32000):
    if not isinstance(snapshot,CompanyResearchSnapshot) or language not in LABELS: raise ValueError('Invalid presentation')
    if type(max_characters) is not int or max_characters<1: raise ValueError('Invalid character bound')
    labels=LABELS[language];ident=snapshot.identity
    aliases={e.evidence_id:'E'+str(i+1) for i,e in enumerate(snapshot.evidence_references)}
    src={s.source_id:'S'+str(i+1) for i,s in enumerate(snapshot.sources)}
    def refs(values): return ','.join(aliases[v] for v in values) or '-'
    def value(v):
        result=str(v.value)+' '+v.unit if v.status==MetricStatus.AVAILABLE else STATUS[language][v.status.value]
        return result+' ['+refs(v.evidence_ids)+']'
    lines=[labels['title'],ident.company_name+' | '+ident.ticker+' | '+ident.market+' | '+ident.reporting_currency,
           ident.provider+':'+ident.provider_issuer_id,labels['period']+': '+period_label(snapshot.reporting_context.current),
           labels['filing']+': '+', '.join(src[s] for s in snapshot.reporting_context.latest_source_ids)]
    for section in (snapshot.income_statement,snapshot.per_share,snapshot.balance_sheet,snapshot.cash_flow):
        for m in section:
            lines.append(labels[m.name]+': '+value(m.current))
            if m.name not in ('assets','liabilities','equity','cash','debt'):
                lines.append('  '+labels['prior']+': '+value(m.prior)+' | '+period_label(m.prior.period))
    for m in (*snapshot.growth,*snapshot.profitability):
        name=labels[m.name[:-4]]+(' YoY' if language=='en' else ' 전년 대비') if m.name.endswith('_yoy') else labels[m.name]
        if language=='ko' and m.name=='revenue_yoy': name='매출 성장률'
        with localcontext() as ctx:
            ctx.prec=40
            result=format(m.value*Decimal(100),'.2f')+'%' if m.value is not None else STATUS[language][m.status.value]
        lines.append(name+': '+result+' ['+refs(m.input_evidence_ids)+']')
    lines.append(labels['coverage']+': '+', '.join(snapshot.coverage.values()))
    lines.append(labels['warnings']+': '+(', '.join(snapshot.warnings) or '-'))
    lines.append(labels['sources']+':')
    for s in snapshot.sources:
        lines.append(src[s.source_id]+' | '+s.title+' | '+str(s.external_id)+' | '+str(s.published_at)+' | '+(s.url or '-')+' | '+s.source_id)
    lines.append(labels['evidence']+':')
    for e in snapshot.evidence_references:
        concept=e.metadata.get('concept') or e.metadata.get('account_id') or '-'
        # Includes alternatives for conflicts; no uncited figure in this rendering.
        lines.append(aliases[e.evidence_id]+' | '+e.evidence_id+' | '+src[e.source_id]+' | '+concept+' | '+str(e.value)+' '+str(e.unit)
                     +' | '+str(e.period_start)+'..'+str(e.period_end)+' | '+str(e.metadata.get('amount_field','')))
    content='\n'.join(lines)
    safe_text(content)
    if len(content)>max_characters: raise ValueError('Snapshot rendering exceeds character bound')
    return content


def render_snapshot_context(instructions,question,snapshot,*,language='en',max_characters=40000):
    safe_text(instructions);safe_text(question)
    body='CURRENT USER QUESTION\n'+question+'\n\nUNTRUSTED RESEARCH DATA\n'+render_snapshot(snapshot,language=language,max_characters=max_characters)
    system=instructions+'\n\n'+POLICY
    if len(system)+len(body)>max_characters: raise ValueError('Snapshot context exceeds character bound')
    return ResearchContext(system,body)
