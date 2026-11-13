"""Synthetic adapter-to-report failures; never uses live responses or clients."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, replace, FrozenInstanceError
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import anthropic
import httpx
from google.genai import types
from magi.analysis.cli import main
from magi.analysis.collection import ResearchServices
from magi.analysis.models import AnalysisError, dumps, loads
from magi.analysis.presentation import render_report
from magi.balthasar import Balthasar
from magi.casper import Casper
from magi.decision import Availability, Position, parse_decision, reconcile_result, unavailable_result
from magi.provider import call_provider
from magi.research.providers.base import Issuer, ResearchResult
from magi.voting import FinalAction
from test_analysis import agents, request, run
from test_agent_research_context import CLASSES
from test_balancing_foundation import NOW, pack
from test_balthasar_response import DATA, message
from test_resilience import fake_clients, status_error

SECRET = 'FAKE_DIAGNOSTIC_SECRET_NOT_A_CREDENTIAL'


class ResearchFailureDiagnosticTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo',
                       'httpx.HTTPTransport.handle_request', 'httpx.AsyncHTTPTransport.handle_async_request',
                       'sqlite3.connect', 'magi.toss.TossSession.__init__'):
            guard = patch(target, side_effect=AssertionError('Offline synthetic research only'))
            guard.start(); self.addCleanup(guard.stop)
        sleeper = patch('magi.provider.time.sleep')
        self.sleep = sleeper.start(); self.addCleanup(sleeper.stop)

    def gemini(self, payload):
        with fake_clients() as (calls, _):
            calls['Gemini'].return_value = payload
            agent = Balthasar(); result = agent.think('Synthetic question')
            calls['Gemini'].assert_called_once()
        return result, agent.last_response_diagnostic

    def casper_error(self, error):
        with fake_clients() as (calls, _):
            calls['Anthropic'].side_effect = error
            agent = Casper(); result = agent.think('Synthetic question')
            calls['Anthropic'].assert_called_once()
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.vote_eligible)
        self.assertNotIn(SECRET, str(result)+repr(agent.last_response_diagnostic))
        self.sleep.assert_not_called()
        return result

    def test_gemini_failures_reach_agent_result(self):
        cases = [
            (types.GenerateContentResponse(), 'missing_candidate'),
            (message(''), 'empty_response'),
            (message('{'+SECRET), 'invalid_json'),
            (message(json.dumps({k:v for k,v in DATA.items() if k!='evidence_gaps'})), 'invalid_schema'),
            (message(json.dumps(dict(DATA, position='MAYBE'))), 'invalid_schema'),
            (message(json.dumps(dict(DATA, confidence='high'))), 'invalid_schema'),
            (types.GenerateContentResponse(candidates=[types.Candidate(finish_reason='STOP', content=types.Content())]), 'unsupported_content'),
            (message(finish='MAX_TOKENS'), 'incomplete_response'),
            (SimpleNamespace(text=SECRET), 'unsupported_candidate'),
        ]
        for payload, code in cases:
            with self.subTest(code=code):
                result, diagnostic = self.gemini(payload)
                self.assertEqual(result.diagnostic_code, code)
                self.assertEqual(result.diagnostic_code, diagnostic.failure_category)
                self.assertEqual(result.error, 'invalid_response')
                self.assertEqual(result.attempts, 1)
                self.assertEqual(result.availability, Availability.UNAVAILABLE)
                self.assertFalse(result.vote_eligible)
                self.assertNotIn(SECRET, str(result))

    def test_valid_abstain_is_not_failure(self):
        result, _ = self.gemini(message(json.dumps(dict(DATA, position='ABSTAIN'))))
        self.assertEqual(result.availability, Availability.AVAILABLE)
        self.assertEqual(result.position, Position.ABSTAIN)
        self.assertFalse(result.vote_eligible)
        self.assertIsNone(result.error); self.assertIsNone(result.diagnostic_code)
        self.assertEqual(result.attempts, 1)

    def test_source_reference_not_a_decision_schema_contract(self):
        # Citation truth is not validated by AgentResult: do not invent that check.
        result, _ = self.gemini(message(json.dumps(dict(DATA, reasoning='Unresolved synthetic [BQ-NOT-PRESENT]'))))
        self.assertEqual(result.availability, Availability.AVAILABLE)
        self.assertIsNone(result.diagnostic_code)
        with fake_clients() as (calls, _):
            calls['Gemini'].return_value = message(json.dumps(dict(DATA, source_references=['NOT-PRESENT'])))
            self.assertEqual(Balthasar().think('Synthetic').availability, Availability.AVAILABLE)

    def test_unknown_gemini_programming_failure_stays_fail_closed(self):
        with fake_clients() as (calls, _):
            calls['Gemini'].return_value = message()
            with patch('magi.balthasar_response.parse_decision', side_effect=RuntimeError(SECRET)):
                with self.assertRaises(AnalysisError) as caught: run(agents=[cls() for cls in CLASSES])
        self.assertEqual(caught.exception.code, 'AGENT_EXECUTION_FAILED')
        self.assertNotIn(SECRET, str(caught.exception))

    def test_casper_timeout_and_transport(self):
        req = httpx.Request('POST', 'https://invalid.test')
        for error, code in (
            (httpx.ReadTimeout(SECRET), 'provider_timeout'),
            (httpx.ConnectTimeout(SECRET), 'provider_timeout'),
            (anthropic.APITimeoutError(request=req), 'provider_timeout'),
            (httpx.ConnectError(SECRET), 'provider_transport_error'),
            (anthropic.APIConnectionError(message=SECRET, request=req), 'provider_transport_error'),
        ):
            with self.subTest(kind=type(error).__name__):
                result = self.casper_error(error)
                self.assertEqual(result.diagnostic_code, code)
                self.assertIsNone(result.http_status)
                self.assertEqual(result.error, 'provider_unavailable:transient_failure_exhausted')

    def test_casper_status_failure_classes(self):
        for status, code, retryable in (
            (429, 'provider_rate_limited', True), (503, 'provider_service_unavailable', True),
            (500, 'provider_server_error', True), (502, 'provider_server_error', True),
            (401, 'provider_authentication_failed', False), (403, 'provider_authentication_failed', False),
            (400, 'provider_error', False), (501, 'provider_server_error', False),
        ):
            with self.subTest(status=status):
                result = self.casper_error(status_error('Anthropic', status))
                self.assertEqual(result.diagnostic_code, code)
                self.assertEqual(result.http_status, status)
                reason = 'transient_failure_exhausted' if retryable else 'non_retryable_provider_error'
                self.assertEqual(result.error, 'provider_unavailable:'+reason)

    def test_unknown_api_error_no_metadata(self):
        error = anthropic.APIError(SECRET, request=httpx.Request('POST', 'https://invalid.test'), body=None)
        result = self.casper_error(error)
        self.assertEqual(result.diagnostic_code, 'provider_error')
        self.assertIsNone(result.http_status)

    def test_unknown_nonprovider_exception_not_fabricated(self):
        with fake_clients() as (calls, _):
            calls['Anthropic'].side_effect = RuntimeError(SECRET)
            with self.assertRaises(AnalysisError) as caught: run(agents=[cls() for cls in CLASSES])
            calls['Anthropic'].assert_called_once()
        self.assertEqual(caught.exception.code, 'AGENT_EXECUTION_FAILED')
        self.assertNotIn(SECRET, str(caught.exception))

    def test_status_metadata_is_numeric_only(self):
        for status in (SECRET, True, -1, 999):
            with self.subTest(status=status):
                error = anthropic.APIError(SECRET, request=httpx.Request('POST', 'https://invalid.test'), body=None)
                error.status_code = status
                result = self.casper_error(error)
                self.assertIsNone(result.http_status)
                self.assertEqual(result.diagnostic_code, 'provider_error')

    def test_shared_retry_defaults_unchanged(self):
        call = Mock(side_effect=httpx.ReadTimeout(SECRET))
        result = call_provider('Gemini', call)
        self.assertEqual((call.call_count, result.attempts), (3, 3))
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.5, 1.0])
        self.assertEqual(result.diagnostic_code, 'provider_timeout')

    def test_success_after_retry_observed_not_guessed(self):
        with fake_clients() as (calls, _):
            calls['Gemini'].side_effect = [httpx.ReadTimeout(SECRET), message()]
            result = Balthasar().think('Synthetic')
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.availability, Availability.AVAILABLE)
        self.assertIsNone(result.diagnostic_code)

    def test_casper_completed_and_invalid_response_classes(self):
        for text, stop, code in ((json.dumps(DATA), 'end_turn', None),
                                 (json.dumps(DATA), 'max_tokens', 'incomplete_response'),
                                 ('{'+SECRET, 'end_turn', 'json_decoding_failure'),
                                 ('{}', 'end_turn', 'schema_validation_failure')):
            with self.subTest(code=code), fake_clients() as (calls, _):
                calls['Anthropic'].return_value.content[0].text = text
                calls['Anthropic'].return_value.stop_reason = stop
                result = Casper().think('Synthetic')
                self.assertEqual(result.diagnostic_code, code)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(result.vote_eligible, code is None)
                self.assertNotIn(SECRET, str(result))

    def test_diagnostic_allowlist_and_immutability(self):
        result = unavailable_result('Casper', 'Anthropic', 'offline', 'invalid_response')
        for value in (SECRET, {}, 1, ['invalid_json']):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                replace(result, diagnostic_code=value)
        with self.assertRaises(FrozenInstanceError): result.diagnostic_code = 'invalid_json'

    def test_legacy_serialization_and_artifact_roundtrip(self):
        result = run()
        for agent in result.agent_results:
            legacy = asdict(agent); legacy.pop('diagnostic_code')
            self.assertEqual(str(agent), json.dumps(legacy, indent=2, ensure_ascii=False, allow_nan=False))
        self.assertNotIn('diagnostic_code', dumps(result))
        self.assertEqual(loads(dumps(result)), result)

    def test_diagnostics_roundtrip_and_consensus_unchanged(self):
        aa = agents()
        aa[0].result = replace(aa[0].result, position=Position.ABSTAIN)
        aa[1].result, _ = self.gemini(message('{bad}'))
        aa[2].result = self.casper_error(httpx.ReadTimeout(SECRET))
        result = run(agents=aa)
        self.assertEqual(loads(dumps(result)), result)
        self.assertEqual([a.diagnostic_code for a in loads(dumps(result)).agent_results],
                         [a.diagnostic_code for a in result.agent_results])
        old = replace(result, agent_results=tuple(replace(a, diagnostic_code=None) for a in result.agent_results), artifact_id='')
        self.assertEqual((result.vote.final_action, result.vote.buy_votes, result.vote.hold_votes, result.vote.sell_votes),
                         (FinalAction.INSUFFICIENT_PARTICIPATION, 0, 0, 0))
        self.assertEqual(result.vote.final_action, old.vote.final_action)
        self.assertEqual(result.vote.eligible_voters, old.vote.eligible_voters)
        report = render_report(result).replace('\\', '')
        for text in ('Decision: ABSTAIN', 'Decision: NO VOTE', 'Failure diagnostic: invalid_json',
                     'Failure diagnostic: provider_timeout', 'Observed provider attempts: 1',
                     'Observed HTTP status: not available'):
            self.assertIn(text, report)
        self.assertNotIn(SECRET, report)

    def test_diagnostics_do_not_change_legacy_decision_equality(self):
        result = unavailable_result('Casper', 'Anthropic', 'offline', 'invalid_response')
        diagnosed = replace(result, diagnostic_code='json_decoding_failure')
        self.assertEqual(result, diagnosed)
        self.assertNotEqual(str(result), str(diagnosed))

    def test_model_cannot_supply_application_diagnostics(self):
        result, _ = self.gemini(message(json.dumps(dict(DATA, diagnostic_code=SECRET, attempts=3, http_status=401))))
        self.assertIsNone(result.diagnostic_code)
        self.assertEqual(result.attempts, 1)
        self.assertIsNone(result.http_status)

    def test_missing_and_malformed_numeric_metadata_not_reported(self):
        aa = agents()
        with self.assertRaises(ValueError):
            unavailable_result('Balthasar', 'fake', 'offline', 'invalid_response',
                               attempts=SECRET, http_status=SECRET)
        aa[1].result = unavailable_result('Balthasar', 'fake', 'offline', 'invalid_response')
        report = render_report(run(agents=aa))
        self.assertIn('Failure diagnostic: not available', report)
        self.assertIn('Observed provider attempts: not available', report)
        self.assertIn('Observed HTTP status: not available', report)
        self.assertNotIn(SECRET, report)

    def test_reconciled_stale_result_keeps_failure_diagnostic(self):
        previous = parse_decision(json.dumps(DATA), 'Casper', 'Anthropic', 'offline')
        result = reconcile_result(self.casper_error(httpx.ReadTimeout(SECRET)), previous)
        self.assertEqual(result.availability, Availability.STALE)
        self.assertEqual(result.position, previous.position)
        self.assertEqual(result.diagnostic_code, 'provider_timeout')
        self.assertFalse(result.vote_eligible)

    def test_cli_failures_export_success_and_sanitized_execution_error(self):
        sec = SimpleNamespace(resolve_issuer=lambda _: ResearchResult(Issuer('SEC','0001045810','Synthetic','US',NOW,'NVDA','Nasdaq')),
                              build_evidence_pack=lambda *a,**kw: ResearchResult(pack()))
        for unexpected in (False, True):
            with self.subTest(unexpected=unexpected), fake_clients() as (calls, _):
                calls['OpenAI'].return_value.output_text = json.dumps(dict(DATA, position='ABSTAIN'))
                calls['Gemini'].return_value = message('{'+SECRET)
                calls['Anthropic'].side_effect = RuntimeError(SECRET) if unexpected else httpx.ReadTimeout(SECRET)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(['NVDA','--market','US','--question','Synthetic','--family','sec','--format','markdown','--execute'],
                                services=ResearchServices(sec=sec),agents=[cls() for cls in CLASSES],now=lambda:NOW)
                calls['Anthropic'].assert_called_once()
                self.assertEqual(code, 1 if unexpected else 0)
                self.assertNotIn(SECRET, out.getvalue()+err.getvalue())
                if unexpected:
                    self.assertEqual(out.getvalue(), '')
                    self.assertIn('AGENT_EXECUTION_FAILED', err.getvalue())
                else:
                    self.assertIn('provider\\_timeout', out.getvalue())
                    self.assertIn('INSUFFICIENT\\_PARTICIPATION', out.getvalue())

    def test_required_evidence_failure_precedes_agents(self):
        aa = agents()
        with self.assertRaises(AnalysisError): run(request(requested=('sec',), required=('sec',)), inputs=(), agents=aa)
        self.assertFalse(any(a.calls for a in aa))


    def test_attempt_contract_at_both_helpers_and_result(self):
        from magi.balthasar_response import response_result as b
        from magi.casper_response import response_result as c
        from test_casper_response import response
        for bad in (True, False, -1, 4, 10**100, 1.0, '1', SECRET, [], {}, None):
            for fn in (lambda v: b(message(), v), lambda v: c(response(), v),
                       lambda v: replace(agents()[0].result, attempts=v)):
                with self.subTest(kind=type(bad).__name__), self.assertRaises(ValueError): fn(bad)
        for count in (0, 1, 2, 3):
            for fn, payload in ((b, message()), (c, response())):
                r, diagnostic = fn(payload, count)
                self.assertEqual((r.attempts, diagnostic.attempts), (count, count))

    def test_mutable_attempts_rejected_before_response_inspection(self):
        from magi.balthasar_response import response_result as b
        from magi.casper_response import response_result as c
        class Unreadable:
            def __getattr__(self, name): raise AssertionError('Must validate count first')
        for fn in (b, c):
            for bad in ([], {}):
                with self.assertRaises(ValueError): fn(Unreadable(), bad)

    def test_http_status_result_contract(self):
        for bad in (True, False, '503', SECRET, [], {}, 503.0, 99, 600, -1, 10**100):
            with self.subTest(kind=type(bad).__name__), self.assertRaises(ValueError):
                replace(agents()[0].result, http_status=bad)
        for value in (None, 100, 401, 403, 429, 500, 503, 599):
            self.assertEqual(replace(agents()[0].result, http_status=value).http_status, value)

    def test_invalid_numeric_metadata_rejected_on_research_load(self):
        baseline = json.loads(dumps(run()))
        for field, bad in (('attempts', SECRET), ('attempts', []), ('attempts', True),
                           ('http_status', SECRET), ('http_status', {}), ('http_status', 600)):
            with self.subTest(field=field):
                raw = json.loads(json.dumps(baseline)); raw['agent_results'][0][field] = bad
                with self.assertRaises(ValueError): loads(json.dumps(raw))

    def test_valid_numeric_json_markdown_roundtrip(self):
        for count, status in ((0, None), (1, 401), (2, 429), (3, 503)):
            aa = agents()
            aa[2].result = replace(unavailable_result('Casper', 'Anthropic', 'offline',
                'provider_unavailable:non_retryable_provider_error', count, status), diagnostic_code='provider_error')
            result = run(agents=aa); restored = loads(dumps(result))
            self.assertEqual((restored.agent_results[2].attempts, restored.agent_results[2].http_status), (count, status))
            text = render_report(restored)
            self.assertIn('Observed provider attempts: '+(str(count) if count else 'not available'), text)
            self.assertIn('Observed HTTP status: '+(str(status) if status is not None else 'not available'), text)

    def test_gemini_sdk_malformed_status_normalized_before_classification(self):
        from google.genai.errors import APIError
        marker = 'FAKE token='+SECRET+' Authorization: FAKE /fake/private/key raw_prompt fake_account'
        for bad in ([], {}, None, True, False, '503', 503.0, 99, 600):
            with self.subTest(kind=type(bad).__name__), fake_clients() as (calls, _):
                calls['Gemini'].side_effect = APIError(0, {'error': {'code': bad, 'message': marker}})
                result = Balthasar().think('Synthetic')
                calls['Gemini'].assert_called_once()
                self.assertEqual(result.diagnostic_code, 'provider_error')
                self.assertIsNone(result.http_status); self.assertFalse(result.vote_eligible)
                self.assertEqual(result.attempts, 1)
                self.assertNotIn(SECRET, str(result))
        self.sleep.assert_not_called()

    def test_anthropic_malformed_status_normalized(self):
        for bad in ([], {}, None, True, '503', 503.0):
            error = anthropic.APIError(SECRET, request=httpx.Request('POST', 'https://invalid.test'), body=None)
            error.status_code = bad
            with self.subTest(kind=type(bad).__name__):
                result = self.casper_error(error)
                self.assertEqual(result.diagnostic_code, 'provider_error')
                self.assertIsNone(result.http_status)

    def test_gemini_valid_status_retry_contract(self):
        for status, code, count in ((401,'provider_authentication_failed',1),
                (403,'provider_authentication_failed',1),(429,'provider_rate_limited',3),
                (500,'provider_server_error',3),(503,'provider_service_unavailable',3)):
            self.sleep.reset_mock()
            with self.subTest(status=status), fake_clients() as (calls, _):
                calls['Gemini'].side_effect = status_error('Gemini', status)
                result = Balthasar().think('Synthetic')
                self.assertEqual((calls['Gemini'].call_count,result.attempts,result.http_status,result.diagnostic_code),
                                 (count,count,status,code))
                self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.5,1.0] if count==3 else [])

    def test_documented_nested_serialization_addition(self):
        from magi.voting import VotingEngine
        # Committed standalone schema: explicit keys, independent of current asdict fields.
        legacy_keys = {'agent','provider','model','position','confidence','reasoning','key_risks',
                       'evidence_gaps','changed_position','availability','error','attempts','http_status'}
        aa = agents(); vote = VotingEngine().vote({a.name:a.result for a in aa})
        nested = json.loads(str(vote))['agent_results']
        for a, row in zip(aa, nested):
            standalone = json.loads(str(a.result))
            self.assertEqual(set(standalone), legacy_keys)
            self.assertEqual(set(row), legacy_keys | {'diagnostic_code'})
            self.assertIsNone(row.pop('diagnostic_code'))
            self.assertEqual(row, standalone)
        aa[1].result = replace(aa[1].result, diagnostic_code='invalid_json')
        self.assertEqual(json.loads(str(VotingEngine().vote({a.name:a.result for a in aa})))['agent_results'][1]['diagnostic_code'], 'invalid_json')

    def test_all_five_state_vote_combinations_ignore_diagnostics(self):
        from itertools import product
        from magi.voting import VotingEngine
        for positions in product(('BUY','HOLD','SELL','ABSTAIN',None), repeat=3):
            with self.subTest(positions=positions):
                base = {}; diagnosed = {}
                for name, position in zip(VotingEngine.AGENTS, positions):
                    r = (parse_decision(json.dumps(dict(DATA, position=position)),name,'fake','offline')
                         if position else unavailable_result(name,'fake','offline','invalid_response'))
                    base[name] = r; diagnosed[name] = replace(r,diagnostic_code='invalid_schema' if position is None else None)
                self.assertEqual(VotingEngine().vote(base), VotingEngine().vote(diagnosed))


if __name__ == '__main__': unittest.main()
