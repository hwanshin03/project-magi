"""Deterministic contract and debate tests. No provider requests are made."""

import io
import json
import unittest
import tempfile
from pathlib import Path
from contextlib import redirect_stdout
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from magi.memory import AnalysisMemory
from magi.storage import Database
from magi.debate import DebateEngine
from magi.decision import (AgentResult, Availability, Position, parse_decision,
                           reconcile_result, unavailable_result)
from test_resilience import fake_clients


def response(position='BUY', **overrides):
    data = dict(position=position, confidence=0.8, reasoning='Full investment reasoning.',
                key_risks=['Downside scenario'], evidence_gaps=['Missing earnings report'])
    data.update(overrides)
    return json.dumps(data)


def decision(position='BUY', **overrides):
    return parse_decision(response(position, **overrides), 'Melchior', 'OpenAI', 'gpt-5.5')


class DecisionTests(unittest.TestCase):
    def setUp(self):
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

    def test_valid_structured_response(self):
        result = decision()
        self.assertIsInstance(result, AgentResult)
        self.assertEqual(result.position, Position.BUY)
        self.assertEqual(result.confidence, 0.8)
        self.assertEqual(result.availability, Availability.AVAILABLE)
        self.assertTrue(result.vote_eligible)
        self.assertIsNone(result.changed_position)
        self.assertEqual(result.key_risks, ('Downside scenario',))
        self.assertEqual(result.evidence_gaps, ('Missing earnings report',))
        self.assertEqual(json.loads(str(result))['agent'], 'Melchior')

    def test_all_controlled_positions_and_abstain(self):
        for position in Position:
            result = decision(position.value)
            self.assertEqual(result.position, position)
            self.assertTrue(result.has_answer)
            self.assertEqual(result.vote_eligible, position != Position.ABSTAIN)

    def test_invalid_position(self):
        result = decision('MAYBE')
        self.assertEqual(result.error, 'invalid_response')
        self.assertEqual(result.availability, Availability.UNAVAILABLE)
        self.assertIsNone(result.position)

    def test_confidence_below_zero(self):
        self.assertEqual(decision(confidence=-0.1).error, 'invalid_response')

    def test_confidence_above_one(self):
        self.assertEqual(decision(confidence=1.1).error, 'invalid_response')

    def test_confidence_types_and_boundaries(self):
        for invalid in [True, False, '0.8', None, float('nan'), float('inf'), float('-inf')]:
            with self.subTest(invalid=invalid):
                self.assertEqual(decision(confidence=invalid).error, 'invalid_response')
        for valid in [0, 1, 0.5]:
            self.assertEqual(decision(confidence=valid).confidence, valid)
        with self.assertRaises(ValueError):
            replace(decision(), confidence=2)

    def test_malformed_missing_and_wrong_typed_fields(self):
        for text in ['', 'not json', '{"position":', '[]', '{}', None,
                     response()[:-1] + ', "confidence": 0.9}',
                     response(reasoning=''), response(reasoning=1),
                     response(key_risks='risk'), response(evidence_gaps=[42])]:
            with self.subTest(text=text):
                result = parse_decision(text, 'Melchior', 'OpenAI', 'gpt-5.5')
                self.assertEqual(result.error, 'invalid_response')
                self.assertFalse(result.vote_eligible)

    def test_model_cannot_override_application_metadata(self):
        result = decision(agent='Impostor', provider='fake', model='fake',
                          availability='STALE', changed_position=True)
        self.assertEqual(result.agent, 'Melchior')
        self.assertEqual(result.provider, 'OpenAI')
        self.assertEqual(result.model, 'gpt-5.5')
        self.assertEqual(result.availability, Availability.AVAILABLE)
        self.assertIsNone(result.changed_position)

    def test_unavailable_result(self):
        result = unavailable_result('Melchior', 'OpenAI', 'gpt-5.5', 'provider_unavailable', 3, 503)
        self.assertEqual(result.availability, Availability.UNAVAILABLE)
        self.assertIsNone(result.position)
        self.assertEqual(result.confidence, 0.0)
        self.assertFalse(result.vote_eligible)
        self.assertFalse(result.has_answer)
        with self.assertRaises(ValueError):
            replace(result, position=Position.BUY)

    def test_stale_result_preserves_details_through_repeated_failures(self):
        prior = decision()
        failure = decision('invalid')
        stale = reconcile_result(failure, prior)
        stale = reconcile_result(failure, stale)
        self.assertEqual(stale.availability, Availability.STALE)
        for field in ['position', 'confidence', 'reasoning', 'key_risks', 'evidence_gaps']:
            self.assertEqual(getattr(stale, field), getattr(prior, field))
        self.assertFalse(stale.vote_eligible)
        self.assertIsNone(stale.changed_position)
        self.assertEqual(prior.availability, Availability.AVAILABLE)

    def test_position_changes_are_computed_in_debate(self):
        for revised, expected in [('SELL', True), ('BUY', False), ('ABSTAIN', True)]:
            with self.subTest(revised=revised):
                agent = SimpleNamespace(name='Melchior', think=Mock(return_value=decision(
                    revised, changed_position=not expected)))
                result = DebateEngine().run([agent], 'original investment question',
                                             {'Melchior': decision()}, rounds=1)[0]['responses']['Melchior']
                self.assertIs(result.changed_position, expected)
                prompt = agent.think.call_args.args[0]
                for value in ['original investment question', 'BUY', 'Full investment reasoning.',
                              'Downside scenario', 'Missing earnings report', 'may change your position']:
                    self.assertIn(value, prompt)

    def test_recovery_compares_last_valid_position_or_unknown(self):
        failure = decision('invalid')
        stale = reconcile_result(failure, decision('BUY'))
        self.assertTrue(reconcile_result(decision('SELL'), stale).changed_position)
        self.assertIsNone(reconcile_result(decision('BUY'), failure).changed_position)

    def test_round_uses_same_snapshot_and_includes_peer_details(self):
        prior = decision()
        peer = replace(decision('SELL'), agent='Casper')
        first = SimpleNamespace(name='Melchior', think=Mock(return_value=decision('HOLD')))
        second = SimpleNamespace(name='Casper', think=Mock(return_value=peer))
        DebateEngine().run([first, second], 'question', {'Melchior': prior, 'Casper': peer}, rounds=1)
        for agent in [first, second]:
            prompt = agent.think.call_args.args[0]
            self.assertIn(str(prior), prompt)
            self.assertIn(str(peer), prompt)
            self.assertNotIn('"position": "HOLD"', prompt)

    def test_invalid_output_full_application_and_no_parsing_retries(self):
        with fake_clients() as (calls, _), patch('magi.provider.time.sleep') as sleep:
            calls['Gemini'].return_value = SimpleNamespace(text='malformed JSON')
            output = io.StringIO()
            with patch('builtins.input', return_value='original question'), redirect_stdout(output):
                main.main()
            self.assertIn('CONSENSUS EXPLANATION', output.getvalue())
            self.assertIn('invalid_response', output.getvalue())
            self.assertIn('Anthropic answer', output.getvalue())
            self.assertEqual(calls['Gemini'].call_count, 4)
            sleep.assert_not_called()

    def test_all_agents_parse_contract_and_keep_metadata(self):
        from magi.melchior import Melchior
        from magi.balthasar import Balthasar
        from magi.casper import Casper
        with fake_clients() as (calls, _):
            for cls in [Melchior, Balthasar, Casper]:
                agent = cls()
                result = agent.think('question')
                self.assertEqual(result.agent, agent.name)
                self.assertEqual(result.provider, agent.provider)
                self.assertEqual(result.model, agent.model)
                self.assertEqual(result.availability, Availability.AVAILABLE)
                self.assertIn('Return ONLY one JSON object', str(calls[agent.provider].call_args))

    def test_one_and_two_provider_failures_share_decision_contract(self):
        from magi.melchior import Melchior
        from magi.balthasar import Balthasar
        from magi.casper import Casper
        for failed in [('Gemini',), ('Gemini', 'Anthropic')]:
            with self.subTest(failed=failed), fake_clients(failed), patch('magi.provider.time.sleep'):
                for cls in [Melchior, Balthasar, Casper]:
                    agent = cls()
                    result = agent.think('question')
                    self.assertIsInstance(result, AgentResult)
                    self.assertEqual(result.agent, agent.name)
                    self.assertEqual(result.model, agent.model)
                    if agent.provider in failed:
                        self.assertEqual(result.availability, Availability.UNAVAILABLE)
                        self.assertIsNone(result.position)
                        self.assertFalse(result.vote_eligible)
                        self.assertEqual(result.attempts, 3)
                        self.assertEqual(result.http_status, 503)
                    else:
                        self.assertEqual(result.availability, Availability.AVAILABLE)
                        self.assertTrue(result.vote_eligible)


if __name__ == '__main__':
    unittest.main()
