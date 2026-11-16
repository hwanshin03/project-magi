"""Reproducible offline fixture authoring. Never imports or constructs live agents."""
from beta_preview.safety import install


def generate():
    from dataclasses import replace
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from pathlib import Path
    from types import SimpleNamespace
    from magi.analysis.models import AnalysisRequest, ResearchInput, dumps
    from magi.analysis.orchestrator import analyze
    from magi.decision import AgentResult, Position, unavailable_result
    from magi.research.models import ResearchSource, EvidenceItem, EvidencePack, SourceType, Authority, Category
    from magi.research.balancing.models import TargetIdentity, InstrumentIdentity

    now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    target = TargetIdentity(InstrumentIdentity('US', 'DEMO'))
    source = ResearchSource(SourceType.SEC_FILING, Authority.PRIMARY,
        'Synthetic fixture', 'Example Compute — fictional annual filing', 'Synthetic fixture', now,
        published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        url='https://example.invalid/fictional-annual-report', document_type='10-K',
        ticker='DEMO', company_name='Example Compute (fictional)', market='US')
    evidence = EvidenceItem(source.source_id, 'Example Compute (fictional)', Category.FINANCIAL,
        'Synthetic annual revenue; this invented example is not real company data.', now,
        ticker='DEMO', value=Decimal('120000000'), unit='USD',
        period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
    pack = EvidencePack('Example Compute (fictional)', now, (source,), (evidence,), ticker='DEMO', market='US')
    req = AnalysisRequest(target, 'Synthetic teaching example: assess evidence and limitations.', now,
                          requested=('sec',), year=2025, currency='USD')
    agents = []
    for name in ('Melchior', 'Balthasar', 'Casper'):
        result = AgentResult(name, 'synthetic', 'fixture-only', Position.HOLD, 0.5,
            'Synthetic interpretation: one historical revenue observation is insufficient to establish valuation.',
            ('Customer concentration is unverified in this fictional dataset.',),
            ('No current price, cash-flow statement, balance sheet or independent news coverage.',))
        agents.append(SimpleNamespace(name=name, think=lambda *a, _result=result, **kw: _result))
    complete = analyze(req, inputs=(ResearchInput('sec', (pack,)),), agents=agents)
    failed = list(complete.agent_results)
    failed[0] = replace(failed[0], position=Position.ABSTAIN)
    for i in (1, 2):
        failed[i] = unavailable_result(failed[i].agent, 'synthetic', 'fixture-only', 'invalid_response')
    partial = replace(complete, agent_results=tuple(failed), artifact_id='')
    root = Path(__file__).parent / 'fixtures'
    for name, result in [('demo-hold', complete), ('demo-partial', partial)]:
        (root / (name + '.json')).write_text(dumps(result) + '\n', encoding='utf-8')


if __name__ == '__main__':
    install()
    generate()
