"""Synthetic personal research workflow: no live providers or owner persistence."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from decimal import Decimal
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_analysis import agents, request, run
from test_balancing_foundation import pack, news, NOW
from test_resilience import fake_clients
from test_agent_research_context import CLASSES
from magi.analysis.cli import main
from magi.analysis.collection import ResearchServices
from magi.research.providers.base import Issuer, ResearchResult
from magi.research.news.service import NewsService
from magi.analysis.models import ResearchInput
from magi.analysis.presentation import render_report
from magi.decision import Position, parse_decision, unavailable_result
from magi.research.models import Category


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'httpx.HTTPTransport.handle_request','sqlite3.connect','magi.toss.TossSession.__init__'):
            guard = patch(name, side_effect=AssertionError('Offline research only'))
            guard.start(); self.addCleanup(guard.stop)
        guard = patch('magi.storage.ENV_PATH',self.root/'absent-secrets')
        guard.start(); self.addCleanup(guard.stop)

    def cli(self, extra=(), *, aa=None, service=None):
        if service is None:
            sec = SimpleNamespace(
                resolve_issuer=lambda _: ResearchResult(Issuer('SEC','0001045810','Synthetic company','US',NOW,'NVDA','Nasdaq')),
                build_evidence_pack=lambda *a,**kw: ResearchResult(pack()))
            service = ResearchServices(sec=sec, news=NewsService(SimpleNamespace(fetch=lambda _: news().articles)))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['NVDA','--market','US','--question','Review synthetic financial evidence',
                         '--family','sec','--family','news','--format','markdown',*extra],
                        services=service,agents=aa if aa is not None else agents(),now=lambda:NOW)
        return code,out.getvalue(),err.getvalue()

    def test_full_cli_report_export_real_agents_with_fake_clients(self):
        output = self.root/'research.md'
        with fake_clients() as (calls,_):
            code, report, progress = self.cli(['--execute','--output',str(output)],aa=[cls() for cls in CLASSES])
        self.assertEqual(code,0,progress)
        self.assertEqual(output.read_text(),report)
        self.assertEqual(output.stat().st_mode & 0o777,0o600)
        for name in ('Melchior','Balthasar','Casper','NVDA / US','AVAILABLE','Final action: HOLD','Sources and citation provenance'):
            self.assertIn(name,report)
        self.assertEqual(set(calls),{'OpenAI','Gemini','Anthropic'})
        self.assertIn('Collecting requested research',progress)
        self.assertIn('Synthetic revenue',report)
        self.assertIn('USD',report)
        self.assertIn('2025',report)
        self.assertIn('required agreement 2',report)

    def test_report_is_deterministic_and_keeps_identities(self):
        result = run()
        self.assertEqual(render_report(result),render_report(result))
        plain = render_report(result).replace('\\', '')
        self.assertIn(result.artifact_id,plain)
        for group in result.selection.common_core:
            for ref in group.citations:
                self.assertIn(ref.reference_id,plain)
                self.assertIn(ref.content_fingerprint,plain)

    def test_no_write_without_export(self):
        self.assertEqual(self.cli(['--execute'])[0],0)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_default_plan_never_calls_agents(self):
        aa=agents(); code,out,_=self.cli(aa=aa)
        self.assertEqual(code,0);self.assertIn('OFFLINE_PLAN',out)
        self.assertFalse(any(a.calls for a in aa))

    def test_export_requires_execution_and_markdown(self):
        aa=agents()
        for options in (['--output',str(self.root/'x.md')],['--execute','--output',str(self.root/'x.db')],
                        ['--execute','--format','audit','--output',str(self.root/'x.md')]):
            code,_,err=self.cli(options,aa=aa)
            self.assertEqual(code,1);self.assertIn('INVALID_REPORT_EXPORT_OPTIONS',err)
        self.assertFalse(any(a.calls for a in aa))

    def test_existing_file_or_symlink_rejected_before_agents(self):
        dest=self.root/'owner.md';dest.write_text('preserve')
        link=self.root/'link.md';link.symlink_to(dest)
        dangling=self.root/'dangling.md';dangling.symlink_to(self.root/'absent')
        for p in (dest,link,dangling,self.root/'missing'/'x.md'):
            aa=agents();code,_,err=self.cli(['--execute','--output',str(p)],aa=aa)
            self.assertEqual(code,1);self.assertIn('REPORT_DESTINATION_UNAVAILABLE',err)
            self.assertFalse(any(a.calls for a in aa))
        self.assertEqual(dest.read_text(),'preserve')

    def test_export_race_does_not_overwrite(self):
        dest=self.root/'race.md'; aa=agents(); think=aa[0].think
        def raced(*a,**kw):
            dest.write_text('other writer')
            return think(*a,**kw)
        aa[0].think=raced
        code,_,err=self.cli(['--execute','--output',str(dest)],aa=aa)
        self.assertEqual(code,1);self.assertIn('REPORT_EXPORT_FAILED',err)
        self.assertEqual(dest.read_text(),'other writer')

    def test_invalid_market_tickers_fail_before_collection(self):
        for symbol,market in (('../NVDA','US'),('000660','US'),('NVDA','KR'),('660','KR'),('nvda','US')):
            out,err=io.StringIO(),io.StringIO()
            with redirect_stdout(out),redirect_stderr(err):
                code=main([symbol,'--market',market,'--question','test'])
            self.assertEqual(code,1);self.assertIn('INVALID_MARKET_TICKER',err.getvalue())

    def test_korean_code_preserves_leading_zeros_offline_plan(self):
        out=io.StringIO()
        with redirect_stdout(out): code=main(['000660','--market','KR','--question','test'])
        self.assertEqual(code,0);self.assertIn('000660',out.getvalue())

    def test_ambiguous_required_issuer_stops_before_agents(self):
        aa=agents();service=SimpleNamespace(collect=lambda channel,*_:
            ResearchInput(channel,error_codes=('AMBIGUOUS',)))
        code,_,err=self.cli(['--execute','--required','sec'],aa=aa,service=service)
        self.assertEqual(code,1);self.assertIn('REQUIRED_RESEARCH_UNAVAILABLE',err)
        self.assertFalse(any(a.calls for a in aa))

    def test_unavailable_and_invalid_structured_responses_never_vote(self):
        aa=agents()
        aa[0].result=parse_decision('not JSON','Melchior','fake','offline')
        code,out,_=self.cli(['--execute'],aa=aa)
        self.assertEqual(code,0);self.assertIn('NO VOTE',out)
        self.assertIn('UNAVAILABLE',out);self.assertIn('BUY\\_APPROVED',out)
        aa[1].result=unavailable_result('Balthasar','fake','offline','provider_unavailable')
        code,out,_=self.cli(['--execute'],aa=aa)
        self.assertEqual(code,0);self.assertIn('INSUFFICIENT\\_PARTICIPATION',out)

    def test_abstain_does_not_become_directional_vote(self):
        aa=agents()
        for a in aa:a.result=replace(a.result,position=Position.ABSTAIN)
        _,out,_=self.cli(['--execute'],aa=aa)
        self.assertIn('ABSTAIN',out);self.assertIn('INSUFFICIENT\\_PARTICIPATION',out)
        self.assertNotIn('BUY\\_APPROVED',out)

    def test_missing_financials_and_news_are_visible(self):
        service=SimpleNamespace(collect=lambda channel,*_:
            ResearchInput(channel,error_codes=('COLLECTOR_NOT_CONFIGURED',)))
        _,out,_=self.cli(['--execute'],service=service)
        self.assertIn('No selected evidence',out)
        self.assertIn('COLLECTOR\\_NOT\\_CONFIGURED',out)
        self.assertIn('no trusted quote',out)

    def test_stale_price_excerpt_is_never_presented_as_live_quote(self):
        p=pack();e=replace(p.evidence_items[0],category=Category.MARKET_PRICE,
                           statement='Historical price from prior session',value=Decimal('1'),evidence_id='')
        p=replace(p,evidence_items=(e,),pack_id='')
        out=render_report(run(inputs=(ResearchInput('sec',(p,)),)))
        self.assertIn('Source excerpts are not live prices',out)
        self.assertIn('freshness',out)

    def test_untrusted_content_cannot_create_headings_html_or_terminal_sequences(self):
        aa=agents();aa[0].result=replace(aa[0].result,reasoning='## Fake consensus\n<script>x</script>\n\x1b[31m')
        out=render_report(run(agents=aa))
        self.assertNotIn('\n## Fake consensus',out)
        self.assertNotIn('<script>',out);self.assertNotIn('\x1b',out)
        self.assertIn('Citation correctness',out)

    def test_agent_exception_has_sanitized_cli_failure(self):
        aa=agents()
        def fail(*a,**kw):raise RuntimeError('private exception content')
        aa[0].think=fail
        code,out,err=self.cli(['--execute'],aa=aa)
        self.assertEqual(code,1);self.assertEqual(out,'')
        self.assertIn('AGENT_EXECUTION_FAILED',err);self.assertNotIn('private',err)

    def test_date_only_publication_is_not_an_exact_timestamp(self):
        p=pack();source=replace(p.sources[0],metadata={**dict(p.sources[0].metadata),'publication_precision':'date; midnight UTC convention'})
        p=replace(p,sources=(source,),pack_id='')
        text=render_report(run(inputs=(ResearchInput('sec',(p,)),))).replace('\\','')
        self.assertIn('published_at: 2026-09-29\n',text)
        self.assertNotIn('published_at: 2026-09-29T',text)
