"""Validated agent decisions; no voting or consensus policy lives here."""

import json
import math
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Optional, Tuple

from magi.provider import ProviderUnavailable


class Position(str, Enum):
    STRONG_BUY = 'STRONG_BUY'
    BUY = 'BUY'
    HOLD = 'HOLD'
    SELL = 'SELL'
    STRONG_SELL = 'STRONG_SELL'
    ABSTAIN = 'ABSTAIN'


class Availability(str, Enum):
    AVAILABLE = 'AVAILABLE'
    STALE = 'STALE'
    UNAVAILABLE = 'UNAVAILABLE'


@dataclass(frozen=True)
class AgentResult:
    agent: str
    provider: str
    model: str
    position: Optional[Position]
    confidence: float
    reasoning: str
    key_risks: Tuple[str, ...]
    evidence_gaps: Tuple[str, ...]
    changed_position: Optional[bool] = None
    availability: Availability = Availability.AVAILABLE
    error: Optional[str] = None
    attempts: int = 0
    http_status: Optional[int] = None

    def __post_init__(self):
        for value in (self.agent, self.provider, self.model):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Agent identity must be nonempty text')
        if not isinstance(self.availability, Availability):
            raise ValueError('Invalid availability')
        if (isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
                or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError('Confidence must be a finite number from 0 to 1')
        object.__setattr__(self, 'confidence', float(self.confidence))
        if not isinstance(self.reasoning, str):
            raise ValueError('Reasoning must be text')
        for field in ('key_risks', 'evidence_gaps'):
            values = getattr(self, field)
            if not isinstance(values, (list, tuple)) or any(
                    not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f'{field} must contain nonempty strings')
            object.__setattr__(self, field, tuple(values))
        if self.changed_position is not None and type(self.changed_position) is not bool:
            raise ValueError('changed_position must be bool or None')
        if self.availability == Availability.UNAVAILABLE:
            if self.position is not None or self.confidence != 0:
                raise ValueError('Unavailable results cannot carry a decision')
        elif not isinstance(self.position, Position) or not self.reasoning.strip():
            raise ValueError('Usable results need a valid position and reasoning')
        if self.availability != Availability.AVAILABLE and self.changed_position is not None:
            raise ValueError('Failed requests cannot establish a position change')

    @property
    def has_answer(self):
        return self.availability != Availability.UNAVAILABLE

    @property
    def vote_eligible(self):
        # Fresh non-abstaining decisions are eligible; confidence never changes eligibility.
        return self.availability == Availability.AVAILABLE and self.position != Position.ABSTAIN

    def __str__(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)


DECISION_INSTRUCTIONS = '''
Return ONLY one JSON object, without Markdown fences, with these fields:
{
  "position": "STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL|ABSTAIN",
  "confidence": 0.0,
  "reasoning": "Your full human-readable analysis and rationale",
  "key_risks": ["risk"],
  "evidence_gaps": ["missing evidence"]
}
Choose exactly one listed position. Use ABSTAIN when information is insufficient
for a responsible investment decision, or when no investment position applies.
Confidence must be a finite JSON number from 0.0 to 1.0, not a percentage.
Preserve detailed reasoning; do not reduce your analysis to a vote.
Risks and evidence gaps must be arrays of strings (empty arrays are allowed).
Identity, availability, and changed_position are supplied by the application.
'''


def unavailable_result(agent, provider, model, error, attempts=0, http_status=None):
    return AgentResult(
        agent=agent, provider=provider, model=model, position=None, confidence=0.0,
        reasoning='', key_risks=(), evidence_gaps=('No validated response is available.',),
        availability=Availability.UNAVAILABLE, error=error, attempts=attempts,
        http_status=http_status,
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse_decision(text, agent, provider, model):
    """Validate before returning; malformed output never becomes an abstention/vote."""
    try:
        if not isinstance(text, str):
            raise ValueError('Missing response text')
        data = json.loads(text, object_pairs_hook=_unique_object)
        if not isinstance(data, dict):
            raise ValueError('Expected JSON object')
        return AgentResult(
            agent=agent, provider=provider, model=model,
            position=Position(data['position']), confidence=data['confidence'],
            reasoning=data['reasoning'], key_risks=data['key_risks'],
            evidence_gaps=data['evidence_gaps'],
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        # Do not expose raw provider output or exception contents in diagnostics.
        return unavailable_result(agent, provider, model, 'invalid_response')


def provider_failure(result, agent, provider, model):
    """Adapt the transport helper's failure into the same agent decision contract."""
    if isinstance(result, ProviderUnavailable):
        return unavailable_result(agent, provider, model, 'provider_unavailable:' + result.reason,
                                  result.attempts, result.status_code)
    return None


def reconcile_result(result, previous):
    """Retain validated decisions on failure; compare fresh positions in code."""
    if result.availability != Availability.AVAILABLE:
        if previous.has_answer:
            return replace(previous, availability=Availability.STALE, changed_position=None,
                           error=result.error, attempts=result.attempts, http_status=result.http_status)
        return result
    changed = result.position != previous.position if previous.has_answer else None
    return replace(result, changed_position=changed)
