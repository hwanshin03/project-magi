"""Deterministic, unweighted two-of-three voting. No trade execution."""

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping, Tuple

from magi.decision import AgentResult, Availability, Position


class FinalAction(str, Enum):
    BUY_APPROVED = 'BUY_APPROVED'
    SELL_APPROVED = 'SELL_APPROVED'
    HOLD = 'HOLD'
    NO_CONSENSUS = 'NO_CONSENSUS'
    INSUFFICIENT_PARTICIPATION = 'INSUFFICIENT_PARTICIPATION'


@dataclass(frozen=True)
class ExcludedAgent:
    agent: str
    reason: str


@dataclass(frozen=True)
class VotingResult:
    final_action: FinalAction
    eligible_voters: Tuple[str, ...]
    excluded_agents: Tuple[ExcludedAgent, ...]
    buy_votes: int
    hold_votes: int
    sell_votes: int
    required_votes: int
    winning_agents: Tuple[str, ...]
    consensus_reached: bool
    # Immutable snapshots preserve every confidence, including excluded agents,
    # as well as the reasoning needed for explanation. Confidence is not a weight.
    agent_results: Tuple[AgentResult, ...]

    def __str__(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)


class VotingEngine:
    AGENTS = ('Melchior', 'Balthasar', 'Casper')
    REQUIRED_VOTES = 2
    ACTIONS = {
        Position.STRONG_BUY: FinalAction.BUY_APPROVED,
        Position.BUY: FinalAction.BUY_APPROVED,
        Position.HOLD: FinalAction.HOLD,
        Position.SELL: FinalAction.SELL_APPROVED,
        Position.STRONG_SELL: FinalAction.SELL_APPROVED,
    }

    def vote(self, responses: Mapping[str, AgentResult]) -> VotingResult:
        # Reject malformed application input rather than accidentally counting an
        # agent twice or expanding this fixed two-of-three electorate.
        if set(responses) != set(self.AGENTS):
            raise ValueError('Voting requires results for Melchior, Balthasar, and Casper')
        results = tuple(responses[name] for name in self.AGENTS)
        eligible = []
        excluded = []
        groups = {action: [] for action in (
            FinalAction.BUY_APPROVED, FinalAction.HOLD, FinalAction.SELL_APPROVED)}
        for name, result in zip(self.AGENTS, results):
            if not isinstance(result, AgentResult) or result.agent != name:
                raise ValueError('Each voting result must match its unique agent identity')
            if not result.vote_eligible:
                reason = (result.availability.value if result.availability != Availability.AVAILABLE
                          else Position.ABSTAIN.value)
                excluded.append(ExcludedAgent(name, reason))
                continue
            eligible.append(name)
            groups[self.ACTIONS[result.position]].append(name)

        action = FinalAction.NO_CONSENSUS
        winners = ()
        if len(eligible) < self.REQUIRED_VOTES:
            action = FinalAction.INSUFFICIENT_PARTICIPATION
        else:
            for category, names in groups.items():
                if len(names) >= self.REQUIRED_VOTES:
                    action, winners = category, tuple(names)
                    break
        return VotingResult(
            final_action=action, eligible_voters=tuple(eligible), excluded_agents=tuple(excluded),
            buy_votes=len(groups[FinalAction.BUY_APPROVED]), hold_votes=len(groups[FinalAction.HOLD]),
            sell_votes=len(groups[FinalAction.SELL_APPROVED]), required_votes=self.REQUIRED_VOTES,
            winning_agents=winners, consensus_reached=bool(winners), agent_results=results,
        )
