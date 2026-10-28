"""Strict synthetic command envelopes. These values grant no execution authority."""
from dataclasses import dataclass, replace
from datetime import datetime

from .execution_contracts import _instant
from .models import ErrorCode, VirtualError, identifier
from .order_codec import (OrderEvent, encode_event, decode_event, _dump, _fields,
                          _load, _stamp, _time)


@dataclass(frozen=True)
class SyntheticContext:
    """Offline fixture identity and receipt claim, not authentication or a trusted clock."""
    fixture_id: str
    received_at: datetime

    def __post_init__(self):
        identifier(self.fixture_id)
        object.__setattr__(self, 'received_at', _instant(self.received_at))


@dataclass(frozen=True)
class SyntheticTransition:
    """One proposed edge. Prior state/revision are event.prior_state/revision - 1.

    Scope, instrument and requested state/evidence are explicit in event.snapshot.
    A forged snapshot cannot replace the persisted intent or bypass the pure graph.
    """
    actor_id: str
    idempotency_key: str
    reservation_id: str
    context: SyntheticContext
    event: OrderEvent

    def __post_init__(self):
        for value in (self.actor_id, self.idempotency_key, self.reservation_id):
            identifier(value)
        if type(self.context) is not SyntheticContext or replace(self.context) != self.context:
            raise VirtualError(ErrorCode.INVALID_INPUT)
        encode_event(self.event)
        if (self.event.revision <= 1 or
                not self.event.snapshot.selection.accepted_at <= self.context.received_at
                <= self.event.snapshot.changed_at <= self.event.recorded_at):
            raise VirtualError(ErrorCode.INVALID_INPUT)


def encode_transition(value):
    try:
        if type(value) is not SyntheticTransition or replace(value) != value:
            raise ValueError()
        return _dump(dict(version=1, actor_id=value.actor_id, idempotency_key=value.idempotency_key,
            reservation_id=value.reservation_id, context=dict(fixture_id=value.context.fixture_id,
            received_at=_stamp(value.context.received_at)), event=_load(encode_event(value.event))))
    except (ValueError, TypeError, ArithmeticError, AttributeError, RecursionError):
        raise VirtualError(ErrorCode.INVALID_INPUT) from None


def decode_transition(text):
    try:
        data = _fields(_load(text), 'version actor_id idempotency_key reservation_id context event')
        if type(data['version']) is not int or data['version'] != 1:
            raise ValueError()
        context = _fields(data['context'], 'fixture_id received_at')
        value = SyntheticTransition(data['actor_id'], data['idempotency_key'], data['reservation_id'],
            SyntheticContext(context['fixture_id'], _time(context['received_at'])),
            decode_event(_dump(data['event'])))
        if encode_transition(value) != text:
            raise ValueError()
        return value
    except (ValueError, TypeError, ArithmeticError, KeyError, RecursionError):
        raise VirtualError(ErrorCode.INVALID_LEDGER) from None


@dataclass(frozen=True)
class TransitionOutcome:
    command: SyntheticTransition
    released: bool
