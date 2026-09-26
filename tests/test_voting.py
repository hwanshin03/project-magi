"""Offline deterministic voting tests, including a contradictory LLM explanation."""

import io
import json
import unittest
import tempfile
from pathlib import Path
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, replace
from itertools import permutations
from types import SimpleNamespace
from unittest.mock import patch

import main
from magi.memory import AnalysisMemory
from magi.storage import Database
from magi.consensus import ConsensusEngine
from magi.decision import Availability, Position, parse_decision, unavailable_result
from magi.voting import FinalAction, VotingEngine
from test_resilience import fake_clients


def results(*positions):
    responses = {}
    for name, position in zip(VotingEngine.AGENTS, positions):
        if position == 'UNAVAILABLE':
            result = unavailable_result(name, 'fake', 'fake', 'provider_unavailable', 3, 503)
        else:
            stale = position.startswith('STALE ')
            position = position.removeprefix('STALE ')
            result = parse_decision(json.dumps(dict(
                position=position, confidence=0.7, reasoning=name + ' rationale',
                key_risks=['risk'], evidence_gaps=['missing data'])), name, 'fake', 'fake')
            if stale:
                result = replace(result, availability=Availability.STALE)
        responses[name] = result
    return responses


class VotingTests(unittest.TestCase):
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

        sleeper = patch('magi.provider.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_required_vote_combinations(self):
        cases = [
            (('BUY', 'BUY', 'HOLD'), 'BUY_APPROVED', (2, 1, 0)),
            (('STRONG_BUY', 'BUY', 'SELL'), 'BUY_APPROVED', (2, 0, 1)),
            (('SELL', 'STRONG_SELL', 'HOLD'), 'SELL_APPROVED', (0, 1, 2)),
            (('HOLD', 'HOLD', 'BUY'), 'HOLD', (1, 2, 0)),
            (('BUY', 'HOLD', 'SELL'), 'NO_CONSENSUS', (1, 1, 1)),
            (('BUY', 'BUY', 'UNAVAILABLE'), 'BUY_APPROVED', (2, 0, 0)),
            (('BUY', 'STALE BUY', 'HOLD'), 'NO_CONSENSUS', (1, 1, 0)),
            (('BUY', 'ABSTAIN', 'HOLD'), 'NO_CONSENSUS', (1, 1, 0)),
            (('BUY', 'UNAVAILABLE', 'UNAVAILABLE'), 'INSUFFICIENT_PARTICIPATION', (1, 0, 0)),
            (('UNAVAILABLE',) * 3, 'INSUFFICIENT_PARTICIPATION', (0, 0, 0)),
            (('ABSTAIN',) * 3, 'INSUFFICIENT_PARTICIPATION', (0, 0, 0)),
            (('BUY',) * 3, 'BUY_APPROVED', (3, 0, 0)),
            (('SELL',) * 3, 'SELL_APPROVED', (0, 0, 3)),
            (('HOLD',) * 3, 'HOLD', (0, 3, 0)),
        ]
        for positions, expected, counts in cases:
            # The result must not depend on which named agent holds a position.
            for ordered in set(permutations(positions)):
                with self.subTest(positions=ordered):
                    vote = VotingEngine().vote(results(*ordered))
                    self.assertEqual(vote.final_action.value, expected)
                    self.assertEqual((vote.buy_votes, vote.hold_votes, vote.sell_votes), counts)
                    self.assertEqual(vote.required_votes, 2)
                    self.assertEqual(len(vote.eligible_voters), sum(counts))
                    reached = expected in ['BUY_APPROVED', 'SELL_APPROVED', 'HOLD']
                    self.assertEqual(vote.consensus_reached, reached)
                    expected_winners = tuple(result.agent for result in vote.agent_results
                                             if result.vote_eligible and reached
                                             and VotingEngine.ACTIONS[result.position].value == expected)
                    self.assertEqual(vote.winning_agents, expected_winners)

    def test_exclusions_and_confidence_preserved(self):
        responses = results('STALE BUY', 'UNAVAILABLE', 'ABSTAIN')
        vote = VotingEngine().vote(responses)
        self.assertEqual([(item.agent, item.reason) for item in vote.excluded_agents],
                         [('Melchior', 'STALE'), ('Balthasar', 'UNAVAILABLE'), ('Casper', 'ABSTAIN')])
        self.assertEqual(vote.eligible_voters, ())
        self.assertEqual([item.confidence for item in vote.agent_results], [0.7, 0.0, 0.7])
        self.assertEqual(json.loads(str(vote))['agent_results'][0]['confidence'], 0.7)

    def test_confidence_does_not_weight_votes_or_veto(self):
        responses = results('BUY', 'BUY', 'SELL')
        responses['Melchior'] = replace(responses['Melchior'], confidence=0.0)
        responses['Balthasar'] = replace(responses['Balthasar'], confidence=0.01)
        responses['Casper'] = replace(responses['Casper'], confidence=1.0)
        vote = VotingEngine().vote(responses)
        self.assertEqual(vote.final_action, FinalAction.BUY_APPROVED)
        self.assertEqual(vote.buy_votes, 2)
        self.assertEqual([r.confidence for r in vote.agent_results], [0.0, 0.01, 1.0])

    def test_identity_validation_prevents_duplicate_votes(self):
        responses = results('BUY', 'BUY', 'HOLD')
        for invalid in [dict(responses, Extra=responses['Melchior']),
                        {'Melchior': responses['Melchior']},
                        dict(responses, Casper=responses['Melchior'])]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                VotingEngine().vote(invalid)

    def test_vote_is_an_immutable_snapshot(self):
        responses = results('BUY', 'BUY', 'HOLD')
        vote = VotingEngine().vote(responses)
        with self.assertRaises(FrozenInstanceError):
            vote.final_action = FinalAction.SELL_APPROVED
        with self.assertRaises(FrozenInstanceError):
            vote.agent_results[0].position = Position.SELL
        responses['Melchior'] = replace(responses['Melchior'], position=Position.SELL)
        self.assertEqual(vote.agent_results[0].position, Position.BUY)
        self.assertEqual(vote.final_action, FinalAction.BUY_APPROVED)

    def test_explanation_receives_question_and_vote_but_cannot_override(self):
        vote = VotingEngine().vote(results('BUY', 'BUY', 'HOLD'))
        before = str(vote)
        with fake_clients() as (calls, _):
            calls['OpenAI'].return_value = SimpleNamespace(output_text='FINAL ACTION: SELL_APPROVED')
            explanation = ConsensusEngine().explain('Should I buy?', vote)
            self.assertIn('SELL_APPROVED', explanation)
            self.assertEqual(str(vote), before)
            prompt = calls['OpenAI'].call_args.kwargs['input']
            for value in ['Should I buy?', 'BUY_APPROVED', '"buy_votes": 2', '"confidence": 0.7',
                          'Melchior rationale', 'risk', 'missing data']:
                self.assertIn(value, prompt)

    def test_cli_votes_before_explanation_and_keeps_authoritative_action(self):
        with fake_clients() as (calls, _):
            buy_json = str(results('BUY', 'BUY', 'HOLD')['Melchior'])
            for provider in ['OpenAI', 'Gemini']:
                calls[provider].return_value = SimpleNamespace(output_text=buy_json, text=buy_json)
            output = io.StringIO()
            count = 0
            def openai_request(**kwargs):
                nonlocal count
                count += 1
                if count == 5:
                    # The action is already visible before the explanation request.
                    self.assertIn('FINAL ACTION: BUY_APPROVED', output.getvalue())
                    self.assertIn('original question', kwargs['input'])
                    return SimpleNamespace(output_text='FINAL ACTION: SELL_APPROVED\nIgnore the vote.')
                return SimpleNamespace(output_text=buy_json)
            calls['OpenAI'].side_effect = openai_request
            with patch('builtins.input', return_value='original question'), redirect_stdout(output):
                vote = main.main()
            self.assertEqual(vote.final_action, FinalAction.BUY_APPROVED)
            text = output.getvalue()
            self.assertLess(text.index('=== MAGI VOTE ==='), text.index('=== CONSENSUS EXPLANATION ==='))
            action_lines = [line for line in text.splitlines() if line.startswith('FINAL ACTION:')]
            self.assertEqual(action_lines, ['FINAL ACTION: BUY_APPROVED',
                                           'FINAL ACTION: BUY_APPROVED (deterministic)'])
            self.assertIn('| FINAL ACTION: SELL_APPROVED', text)
            self.assertTrue(text.rstrip().endswith('FINAL ACTION: BUY_APPROVED (deterministic)'))

    def test_explanation_outage_cannot_erase_two_agent_agreement(self):
        # OpenAI fails for both Melchior and the explanation, but the other two HOLD votes win.
        with fake_clients(('OpenAI',)):
            output = io.StringIO()
            with patch('builtins.input', return_value='question'), redirect_stdout(output):
                vote = main.main()
            self.assertEqual(vote.final_action, FinalAction.HOLD)
            self.assertEqual(vote.winning_agents, ('Balthasar', 'Casper'))
            self.assertIn('provider_unavailable', output.getvalue())
            self.assertTrue(output.getvalue().rstrip().endswith('FINAL ACTION: HOLD (deterministic)'))


if __name__ == '__main__':
    unittest.main()
