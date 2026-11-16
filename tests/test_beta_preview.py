"""Offline Club preview contract/security tests. No sockets or databases required."""
from dataclasses import replace
from html.parser import HTMLParser
from http.client import parse_headers
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from beta_preview import app
from beta_preview.safety import audit
from magi.analysis.models import dumps, loads
from magi.research.balancing.models import InstrumentIdentity, TargetIdentity


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {}, clear=True)
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.reports = app.load_reports()

    def test_real_saved_artifacts_validate_and_roundtrip(self):
        self.assertEqual(set(self.reports), set(app.CATALOG))
        for result, _ in self.reports.values():
            self.assertEqual(loads(dumps(result)), result)

    def test_offline_fixture_generation_is_reproducible(self):
        from beta_preview.synthetic import generate
        written = {}
        def capture(path, value, **kwargs):
            written[path.stem] = loads(value)
        with patch.object(Path, 'write_text', capture):
            generate()
        self.assertEqual(written, {k: v[0] for k, v in self.reports.items()})

    def test_all_user_flow_pages(self):
        for path in ('/', '/reports', '/tickers', '/reports/demo-hold', '/reports/demo-partial'):
            with self.subTest(path=path):
                status, body = app.page(path, self.reports)
                self.assertEqual(status, 200)
                self.assertIn('SYNTHETIC', body)
                self.assertIn('Authentication is not implemented', body)

    def test_public_brand_and_local_search(self):
        for path in ('/', '/tickers', '/reports', '/reports/demo-partial'):
            body = app.page(path, self.reports)[1]
            self.assertIn('MOIRAI', body)
            self.assertIn('Powered by MAGI', body)
            self.assertNotIn('MAGI Club Research', body)
        self.assertIn('demo-partial', app.page('/reports?ticker=%20demo%20', self.reports)[1])
        body = app.page('/', self.reports)[1]
        self.assertIn('method="get"', body)
        self.assertIn('action="/reports"', body)
        self.assertIn('Reserved location for official FIC logo', body)

    def test_financial_table_has_accessible_aligned_columns(self):
        body = app.detail('demo-hold', self.reports['demo-hold'])
        self.assertIn('<caption>Selected financial evidence', body)
        self.assertIn('scope="col" class="number"', body)
        self.assertIn('tabindex="0"', body)

    def test_local_fonts_are_served_with_restrictive_policy(self):
        for name in ('Geist.ttf', 'GeistMono.ttf', 'PretendardVariable.woff2'):
            handler = object.__new__(app.Handler)
            handler.path = '/assets/fonts/' + name
            handler.headers = parse_headers(BytesIO(b'Host: 127.0.0.1:8765\r\n\r\n'))
            handler.server = SimpleNamespace(server_port=8765)
            handler.reply = lambda status, content, mime: self.assertTrue(status == 200 and len(content) > 10000 and mime.startswith('font/'))
            handler.do_GET()
        response = self.response()
        for directive in ("font-src 'self'", "form-action 'self'", "base-uri 'none'", "default-src 'none'", "frame-ancestors 'none'"):
            self.assertIn(directive, response)
        self.assertIn('404 Not Found', self.response('/assets/fonts/../app.py'))

    def test_partial_agents_do_not_vote(self):
        result, _ = self.reports['demo-partial']
        self.assertEqual(result.vote.final_action.value, 'INSUFFICIENT_PARTICIPATION')
        self.assertEqual(sum(a.vote_eligible for a in result.agent_results), 0)
        body = app.detail('demo-partial', self.reports['demo-partial'])
        self.assertEqual(body.count('<div class="outcome">NO VOTE</div>'), 2)
        self.assertIn('<div class="outcome">ABSTAIN</div>', body)

    def test_consensus_is_exact_saved_result(self):
        for key, saved in self.reports.items():
            result, _ = saved
            body = app.detail(key, saved)
            self.assertIn(f'<h2>{app.outcome_label(result.vote.final_action.value)}</h2>', body)
            self.assertIn(f'BUY {result.vote.buy_votes} · HOLD {result.vote.hold_votes} · SELL {result.vote.sell_votes}', body)

    def test_evidence_and_source_identity_preserved(self):
        saved = self.reports['demo-hold']
        body = app.detail('demo-hold', saved)
        for group in saved[0].selection.common_core:
            for ref in (*group.units, *group.citations):
                self.assertIn(ref.object_id, body)
        self.assertIn('https://example.invalid/fictional-annual-report', body)
        self.assertIn('120000000 USD', body)
        self.assertIn('2025-01-01 → 2025-12-31', body)

    def test_missing_data_and_unverified_claims_explicit(self):
        body = app.detail('demo-hold', self.reports['demo-hold'])
        for phrase in ('No trusted live quote', 'not independently verified', 'not verified facts', 'NOT_SUPPLIED'):
            self.assertIn(phrase, body)

    def test_empty_and_invalid_routes(self):
        self.assertIn('No prepared reports yet', app.page('/reports?ticker=MISSING', self.reports)[1])
        for path in ('/reports/unknown', '/reports/../.env', '/.env', '/fixtures/demo-hold.json', '/execute', '/reports/%2e%2e/.env'):
            self.assertEqual(app.page(path, self.reports)[0], 404)

    def test_malformed_artifact_is_withheld(self):
        with patch.object(Path, 'read_text', return_value='{broken json'):
            reports = app.load_reports()
        self.assertTrue(all(value is None for value in reports.values()))
        status, body = app.page('/reports/demo-hold', reports)
        self.assertEqual(status, 503)
        self.assertNotIn('broken json', body)

    def test_forged_artifact_identity_rejected(self):
        data = json.loads(dumps(self.reports['demo-hold'][0]))
        data['artifact_id'] = 'forged'
        with self.assertRaises(ValueError):
            loads(json.dumps(data))

    def test_non_demo_report_withheld(self):
        result = self.reports['demo-hold'][0]
        other = SimpleNamespace(request=SimpleNamespace(target=TargetIdentity(InstrumentIdentity('US', 'NVDA'))),
                                agent_results=result.agent_results)
        with patch.object(app, 'loads', return_value=other):
            self.assertTrue(all(v is None for v in app.load_reports().values()))

    def test_oversized_or_symlink_fixture_withheld(self):
        with patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=1_000_001)), patch.object(Path, 'is_symlink', return_value=False):
            self.assertTrue(all(v is None for v in app.load_reports().values()))
        with patch.object(Path, 'is_symlink', return_value=True):
            self.assertTrue(all(v is None for v in app.load_reports().values()))

    def test_untrusted_agent_and_markdown_are_inert(self):
        result, _ = self.reports['demo-hold']
        attack = '<script>alert(1)</script><img src=x onerror=alert(1)>[click](javascript:alert(1))'
        first = replace(result.agent_results[0], reasoning=attack, key_risks=(attack,), evidence_gaps=(attack,))
        result = replace(result, agent_results=(first, *result.agent_results[1:]), artifact_id='')
        body = app.detail('demo-hold', (result, attack))
        self.assertIn('&lt;script&gt;', body)
        self.assertFalse(any(tag in {'script', 'iframe'} or (tag == 'img' and _.get('src') != '/assets/fate-circle.svg') for tag, _ in Tags(body).tags))
        self.assertFalse(any(k.startswith('on') for _, attrs in Tags(body).tags for k in attrs))

    def test_safe_source_links(self):
        for url in ('javascript:alert(1)', 'data:text/html,bad', '//evil.invalid', 'http://example.invalid',
                    'https://name:password@example.invalid', 'https://example.invalid:99', 'https://example.invalid\\evil',
                    'https://example.invalid\n', 'https://[bad'):
            with self.subTest(url=url):
                self.assertNotIn('<a ', app.source_link(url))
        self.assertNotIn('<a ', app.source_link('https://example.invalid/" onclick="test'))
        self.assertIn('noopener', app.source_link('https://example.invalid/a?quote=%22'))

    def test_untrusted_source_text_is_inert(self):
        original_resolve = app.resolve
        attack = '<img src=x onerror=alert(1)>'
        def hostile(universe, ref):
            obj = original_resolve(universe, ref)
            if ref.object_kind == 'EvidenceItem':
                return replace(obj, statement=attack, evidence_id='')
            if ref.object_kind == 'ResearchSource':
                return replace(obj, title=attack)
            return obj
        with patch.object(app, 'resolve', side_effect=hostile):
            body = app.detail('demo-hold', self.reports['demo-hold'])
        self.assertIn('&lt;img', body)
        self.assertFalse(any(tag == 'img' and _.get('src') != '/assets/fate-circle.svg' for tag, _ in Tags(body).tags))

    def response(self, path='/', host='127.0.0.1:8765', method='GET'):
        handler = object.__new__(app.Handler)
        handler.path = path
        handler.headers = parse_headers(BytesIO(f'Host: {host}\r\n\r\n'.encode()))
        handler.server = SimpleNamespace(server_port=8765, reports=self.reports)
        handler.request_version = 'HTTP/1.1'
        handler.requestline = method + ' ' + path + ' HTTP/1.1'
        handler.command = method
        handler.wfile = BytesIO()
        getattr(handler, 'do_' + method)()
        return handler.wfile.getvalue().decode()

    def test_http_headers_and_host_boundary(self):
        response = self.response()
        self.assertIn('200 OK', response)
        self.assertIn("default-src 'none'", response)
        self.assertIn("frame-ancestors 'none'", response)
        self.assertIn('Cache-Control: no-store', response)
        for host in ('evil.invalid', '127.0.0.1:8765@evil.invalid', '127.0.0.1:8765\r\nHost: localhost:8765'):
            self.assertIn('403 Forbidden', self.response(host=host))

    def test_no_execution_endpoint(self):
        self.assertIn('405 Method Not Allowed', self.response('/execute', method='POST'))
        self.assertIn('disabled', app.page('/', self.reports)[1])

    def test_company_route_uses_validated_evidence(self):
        status, body = app.page('/companies/DEMO', self.reports)
        self.assertEqual(status, 200)
        for phrase in ('120000000 USD', 'Exchange not supplied', 'Market data unavailable', 'fictional-annual-report'):
            self.assertIn(phrase, body)
        self.assertEqual(app.page('/companies/NVDA', self.reports)[0], 404)
        self.assertEqual(app.page('/companies/DEMO', {'demo-hold': None})[0], 503)

    def test_search_preserves_query_and_escapes_input(self):
        self.assertIn('demo-hold', app.page('/reports?ticker=Example%20Compute', self.reports)[1])
        body = app.page('/reports?ticker=%22%3E%3Cscript%3E', self.reports)[1]
        self.assertNotIn('<script>', body)
        self.assertIn('Reset search', body)
        self.assertIn('&quot;&gt;&lt;script&gt;', body)
        self.assertIn('demo-hold', app.page('/reports?ticker=', self.reports)[1])

    def test_feedback_specimens_do_not_claim_active_execution(self):
        status, body = app.page('/preview/states', self.reports)
        self.assertEqual(status, 200)
        self.assertIn('Static UI specimens', body)
        self.assertIn('No research or model request is running', body)

    def test_logo_and_font_allowlist(self):
        response = self.response('/assets/fate-circle.svg')
        self.assertIn('200 OK', response)
        self.assertIn('image/svg+xml', response)
        self.assertIn("img-src 'self'", response)
        for path in ('/assets/../app.py', '/assets/fate-circle.svg?x=1', '/assets/fonts/Cinzel-OFL.txt'):
            self.assertIn('404 Not Found', self.response(path))
        body = app.page('/', self.reports)[1]
        images = [attrs for tag, attrs in Tags(body).tags if tag == 'img']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['src'], '/assets/fate-circle.svg')
        self.assertEqual(images[0]['width'], '400')

    def test_runtime_guard_rejects_side_effects_without_attempting_them(self):
        for event, args in [('socket.connect', ()), ('socket.getaddrinfo', ()), ('socket.gethostbyaddr', ()),
                            ('sqlite3.connect', ()), ('subprocess.Popen', ()),
                            ('socket.bind', (None, ('0.0.0.0', 8765))),
                            ('import', ('magi.broker.toss',)), ('import', ('magi.portfolio',)),
                            ('open', ('/fake/.env',)), ('open', ('/fake/magi.db',)),
                            ('open', ('/fake/.aws/credentials',)), ('open', ('/fake/MAGI-private/report.md',))]:
            with self.subTest(event=event, args=args), self.assertRaises(PermissionError):
                audit(event, args)
        audit('socket.bind', (None, ('127.0.0.1', 8765)))


if __name__ == '__main__':
    unittest.main()
