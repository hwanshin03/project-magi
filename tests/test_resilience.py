"""Offline tests. Run only this module; other test_*.py files are live smoke scripts."""

import io
import json
import unittest
import tempfile
from pathlib import Path
from contextlib import ExitStack, contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
import httpx
import openai
from google.genai import types
from google.genai.errors import APIError as GeminiAPIError

import main
from magi.memory import AnalysisMemory
from magi.storage import Database
from magi.balthasar import Balthasar
from magi.casper import Casper
from magi.consensus import ConsensusEngine
from magi.debate import DebateEngine
from magi.melchior import Melchior
from magi.provider import ProviderUnavailable, call_provider
from magi.voting import VotingEngine
from magi.decision import Availability, parse_decision, unavailable_result, reconcile_result


def status_error(provider, status):
    if provider == 'Gemini':
        return GeminiAPIError(status, {'error': {'message': 'secret must not appear'}})
    sdk = openai if provider == 'OpenAI' else anthropic
    response = httpx.Response(status, request=httpx.Request('POST', 'https://invalid.test'))
    return sdk.APIStatusError('secret must not appear', response=response, body=None)


@contextmanager
def fake_clients(failed=()):
    calls = {}
    constructors = {}
    with ExitStack() as stack:
        for provider, target, attribute in [
            ('OpenAI', 'magi.melchior.OpenAI', 'responses'),
            ('Gemini', 'magi.balthasar.genai.Client', 'models'),
            ('Anthropic', 'magi.casper.Anthropic', 'messages'),
        ]:
            decision = json.dumps(dict(position='HOLD', confidence=0.7,
                                       reasoning=provider + ' answer', key_risks=['downside'],
                                       evidence_gaps=['latest data']))
            payload = SimpleNamespace(output_text=decision, text=decision,
                                      content=[SimpleNamespace(text=decision)])
            request = Mock(return_value=payload)
            if provider in failed:
                request.side_effect = status_error(provider, 503)
            endpoint = SimpleNamespace(create=request, generate_content=request)
            client = SimpleNamespace(**{attribute: endpoint})
            constructors[provider] = stack.enter_context(patch(target, return_value=client))
            if provider == 'OpenAI':
                stack.enter_context(patch('magi.consensus.OpenAI', return_value=client))
            calls[provider] = request
        yield calls, constructors


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        # Tripwires: an accidental real network request must fail this test suite.
        for target in ['socket.socket.connect', 'httpx.Client.send']:
            guard = patch(target, side_effect=AssertionError('Live network access forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        memory = AnalysisMemory(Database(Path(directory.name) / 'magi.db'))
        factory = patch('main.AnalysisMemory', return_value=memory)
        factory.start()
        self.addCleanup(factory.stop)

        sleeper = patch('magi.provider.time.sleep')
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_transient_statuses_retry_then_succeed(self):
        for provider in ['OpenAI', 'Gemini', 'Anthropic']:
            for status in [429, 500, 502, 503, 504]:
                with self.subTest(provider=provider, status=status):
                    self.sleep.reset_mock()
                    request = Mock(side_effect=[status_error(provider, status),
                                                status_error(provider, status), 'ok'])
                    self.assertEqual(call_provider(provider, request), 'ok')
                    self.assertEqual(request.call_count, 3)
                    self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.5, 1.0])

    def test_nontransient_statuses_do_not_retry(self):
        for provider in ['OpenAI', 'Gemini', 'Anthropic']:
            for status in [400, 401, 403, 404, 422, 501]:
                with self.subTest(provider=provider, status=status):
                    self.sleep.reset_mock()
                    request = Mock(side_effect=status_error(provider, status))
                    result = call_provider(provider, request)
                    self.assertIsInstance(result, ProviderUnavailable)
                    self.assertEqual(result.attempts, 1)
                    self.assertEqual(result.status_code, status)
                    self.assertEqual(request.call_count, 1)
                    self.sleep.assert_not_called()
                    self.assertNotIn('secret', str(result))

    def test_network_and_timeout_errors_exhaust_safely(self):
        request = httpx.Request('POST', 'https://invalid.test')
        errors = [openai.APIConnectionError(request=request),
                  openai.APITimeoutError(request=request),
                  anthropic.APIConnectionError(request=request),
                  anthropic.APITimeoutError(request=request),
                  httpx.ConnectError('offline'), httpx.ReadTimeout('offline'),
                  httpx.RemoteProtocolError('offline'), TimeoutError(), ConnectionError()]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                operation = Mock(side_effect=error)
                result = call_provider('fake', operation)
                self.assertEqual(result.status, 'provider_unavailable')
                self.assertEqual(result.attempts, 3)
                self.assertEqual(operation.call_count, 3)

    def test_programming_errors_are_not_swallowed(self):
        with self.assertRaises(ValueError):
            call_provider('fake', Mock(side_effect=ValueError('bug')))

    def test_client_timeouts_and_sdk_retries(self):
        with fake_clients() as (_, constructors):
            Melchior()
            Balthasar()
            Casper()
            for provider in ['OpenAI', 'Anthropic']:
                kwargs = constructors[provider].call_args.kwargs
                self.assertEqual(kwargs['timeout'], 30)
                self.assertEqual(kwargs['max_retries'], 0)
            options = types.HttpOptions(**constructors['Gemini'].call_args.kwargs['http_options'])
            self.assertEqual(options.timeout, 30000)
            self.assertEqual(options.retry_options.attempts, 1)
            with patch('magi.consensus.OpenAI') as constructor:
                ConsensusEngine()
                self.assertEqual(constructor.call_args.kwargs['timeout'], 30)
                self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)

    def test_debate_preserves_answers_and_recovers(self):
        failure = unavailable_result('Balthasar', 'Gemini', 'fake', 'provider_unavailable', 3, 503)
        def answer(name, reasoning):
            return parse_decision(json.dumps(dict(position='HOLD', confidence=0.7,
                                  reasoning=reasoning, key_risks=[], evidence_gaps=[])),
                                  name, 'fake', 'fake')
        recovered = answer('Balthasar', 'recovered')
        healthy_answer = answer('Melchior', 'healthy')
        failing = SimpleNamespace(name='Balthasar', think=Mock(side_effect=[failure, failure, recovered]))
        healthy = SimpleNamespace(name='Melchior', think=Mock(return_value=healthy_answer))
        initial = {'Balthasar': answer('Balthasar', 'previous good answer'),
                   'Melchior': answer('Melchior', 'initial healthy')}
        rounds = DebateEngine().run([failing, healthy], 'question', initial, rounds=3)
        for index in [0, 1]:
            result = rounds[index]['responses']['Balthasar']
            self.assertEqual(result.reasoning, 'previous good answer')
            self.assertEqual(result.availability, Availability.STALE)
            self.assertFalse(result.vote_eligible)
            self.assertEqual(rounds[index]['responses']['Melchior'].reasoning, 'healthy')
        self.assertEqual(rounds[2]['responses']['Balthasar'].reasoning, 'recovered')
        self.assertEqual(rounds[2]['responses']['Balthasar'].availability, Availability.AVAILABLE)
        self.assertFalse(rounds[2]['responses']['Balthasar'].changed_position)
        self.assertEqual(initial['Balthasar'].reasoning, 'previous good answer')
        self.assertIn('STALE', failing.think.call_args_list[1].args[0])

    def test_full_application_provider_outages(self):
        for failed in [(), ('Gemini',), ('Anthropic',), ('OpenAI',),
                       ('Gemini', 'Anthropic'), ('OpenAI', 'Gemini'),
                       ('OpenAI', 'Anthropic'), ('OpenAI', 'Gemini', 'Anthropic')]:
            with self.subTest(failed=failed), fake_clients(failed) as (calls, _):
                output = io.StringIO()
                with patch('builtins.input', return_value='offline question'), redirect_stdout(output):
                    main.main()
                text = output.getvalue()
                self.assertIn('CONSENSUS EXPLANATION', text)
                self.assertIn('DEBATE ROUND 3', text)
                self.assertNotIn('secret must not appear', text)
                if failed:
                    self.assertIn('provider_unavailable', text)
                    self.assertTrue('http_status=503' in text or '"http_status": 503' in text)
                for provider in ['OpenAI', 'Gemini', 'Anthropic']:
                    if provider not in failed:
                        self.assertIn(provider + ' answer', text)
                    count = 12 if provider in failed else 4
                    if provider == 'OpenAI' and len(failed) < 3:
                        count += 3 if provider in failed else 1
                    self.assertEqual(calls[provider].call_count, count)
                if failed and 'OpenAI' not in failed:
                    self.assertIn('Provider availability limitation:', text)
                    consensus_prompt = calls['OpenAI'].call_args.kwargs['input']
                    self.assertIn('do not invent missing positions', consensus_prompt)

    def test_consensus_no_answers_and_retained_answers(self):
        with fake_clients() as (calls, _):
            engine = ConsensusEngine()
            responses = {name: unavailable_result(name, 'fake', 'fake', 'provider_unavailable')
                         for name in ['Melchior', 'Balthasar', 'Casper']}
            result = engine.explain('original question', VotingEngine().vote(responses))
            self.assertEqual(result.reason, 'no_agent_answers_available')
            calls['OpenAI'].assert_not_called()
            prior = parse_decision(json.dumps(dict(position='HOLD', confidence=0.5,
                                   reasoning='retained risk analysis', key_risks=[], evidence_gaps=[])),
                                   'Casper', 'Anthropic', 'fake')
            responses['Casper'] = reconcile_result(responses['Casper'], prior)
            self.assertIn('OpenAI answer', engine.explain('original question', VotingEngine().vote(responses)))
            prompt = calls['OpenAI'].call_args.kwargs['input']
            for fragment in ['original question', 'retained risk analysis', 'STALE', '"confidence": 0.5']:
                self.assertIn(fragment, prompt)

    def test_full_application_consensus_only_failure(self):
        with fake_clients() as (calls, _):
            success = calls['OpenAI'].return_value
            calls['OpenAI'].side_effect = [success] * 4 + [status_error('OpenAI', 503)] * 3
            output = io.StringIO()
            with patch('builtins.input', return_value='offline question'), redirect_stdout(output):
                main.main()
            final_output = output.getvalue().split('CONSENSUS EXPLANATION')[-1]
            self.assertIn('provider_unavailable', final_output)
            self.assertIn('attempts=3', final_output)
            for answer in ['OpenAI answer', 'Gemini answer', 'Anthropic answer']:
                self.assertIn(answer, output.getvalue())


if __name__ == '__main__':
    unittest.main()
