"""Immutable simulated order transitions. No authentication, reservations or settlement."""
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .models import Side, identifier, number
from .execution_contracts import (CalendarCoverage, CalendarSession, SessionStatus,
                                  _exchange, _instant, _invalid)
from .session_policy import PolicyCode, PolicyError, SessionSelection, select_session


@dataclass(frozen=True)
class OrderIntent:
    """Caller-supplied synthetic references, not verified portfolio/listing ownership."""
    order_id: str
    portfolio_id: str
    instrument_id: str
    exchange: str
    side: Side
    quantity: Decimal
    submitted_at: datetime
    idempotency_key: str

    def __post_init__(self):
        for value in (self.order_id, self.portfolio_id, self.instrument_id, self.idempotency_key):
            identifier(value)
        _exchange(self.exchange)
        if type(self.side) is not Side:
            _invalid()
        number(self.quantity, maximum=Decimal('1000000'), positive=True)
        if self.quantity != self.quantity.to_integral_value():
            _invalid()
        object.__setattr__(self, 'submitted_at', _instant(self.submitted_at))


class OrderState(str, Enum):
    ACCEPTED = 'ACCEPTED'
    FROZEN = 'FROZEN'
    AWAITING_PRICE = 'AWAITING_PRICE'
    CANCELLED = 'CANCELLED'
    EXPIRED = 'EXPIRED'
    FILLED = 'FILLED'  # Reserved vocabulary only; construction and transitions reject it.


class ExpirationReason(str, Enum):
    PRICE_DEADLINE = 'PRICE_DEADLINE'
    SESSION_INVALIDATED = 'SESSION_INVALIDATED'


class SealStatus(str, Enum):
    """Explicit synthetic claims; never derived from a provider's FINAL flag."""
    NO_SEAL = 'NO_SEAL'
    SEALED = 'SEALED'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class ExpirationEvidence:
    """Simulation evidence, not a real attestation or permission to release funds."""
    reason: ExpirationReason
    seal_status: SealStatus
    revised_session: CalendarSession | None = None

    def __post_init__(self):
        if type(self.reason) is not ExpirationReason or type(self.seal_status) is not SealStatus:
            _invalid()
        if self.reason is ExpirationReason.PRICE_DEADLINE:
            if self.revised_session is not None:
                _invalid()
        elif type(self.revised_session) is not CalendarSession:
            _invalid()


def _validate_expiration(selection, at, evidence):
    if type(evidence) is not ExpirationEvidence:
        _invalid()
    # A qualified seal must survive worker delay. Unknown is never absence of a seal.
    if evidence.seal_status is not SealStatus.NO_SEAL:
        raise PolicyError(PolicyCode.EVIDENCE_UNRESOLVED)
    if evidence.reason is ExpirationReason.PRICE_DEADLINE:
        if selection.deadline_at is None:
            raise PolicyError(PolicyCode.DEADLINE_REQUIRED)
        if at < selection.deadline_at:
            raise PolicyError(PolicyCode.TIMING_VIOLATION)
        return
    revised, original = evidence.revised_session, selection.session
    if (revised.exchange != original.exchange or revised.session_date != original.session_date
            or revised.trading_timezone != original.trading_timezone
            or (revised.source.provider, revised.source.dataset) != (original.source.provider, original.source.dataset)
            or revised.source.revision_id == original.source.revision_id
            or (revised.session_id is not None and revised.session_id != original.session_id)):
        raise PolicyError(PolicyCode.CALENDAR_MISMATCH)
    if revised.coverage is CalendarCoverage.UNKNOWN:
        raise PolicyError(PolicyCode.CALENDAR_UNAVAILABLE)
    if revised.status is SessionStatus.REGULAR:
        raise PolicyError(PolicyCode.EVIDENCE_UNRESOLVED)
    # Day-level status lacks an effective-time history. Do not reinterpret a later
    # halt as a halt at opening or expire a potentially sealed post-opening cohort.
    if at > original.regular_open:
        raise PolicyError(PolicyCode.EVIDENCE_UNRESOLVED)


@dataclass(frozen=True)
class OrderSnapshot:
    """Pure lifecycle snapshot; ACCEPTED does not attest actual server acceptance."""
    intent: OrderIntent
    selection: SessionSelection
    state: OrderState
    changed_at: datetime
    expiration: ExpirationEvidence | None = None

    def __post_init__(self):
        if (type(self.intent) is not OrderIntent or type(self.selection) is not SessionSelection
                or type(self.state) is not OrderState):
            _invalid()
        if self.state is OrderState.FILLED:
            raise PolicyError(PolicyCode.SETTLEMENT_REQUIRED)
        at = _instant(self.changed_at)
        object.__setattr__(self, 'changed_at', at)
        if self.intent.exchange != self.selection.session.exchange:
            raise PolicyError(PolicyCode.CALENDAR_MISMATCH)
        if not self.intent.submitted_at <= self.selection.accepted_at <= at:
            raise PolicyError(PolicyCode.TIMING_VIOLATION)
        if self.state is OrderState.EXPIRED:
            _validate_expiration(self.selection, at, self.expiration)
        elif self.expiration is not None:
            _invalid()
        if self.state is OrderState.ACCEPTED and at != self.selection.accepted_at:
            raise PolicyError(PolicyCode.TIMING_VIOLATION)
        if self.state is OrderState.FROZEN and at < self.selection.cutoff_at:
            raise PolicyError(PolicyCode.TIMING_VIOLATION)
        if self.state is OrderState.AWAITING_PRICE and at < self.selection.session.regular_open:
            raise PolicyError(PolicyCode.TIMING_VIOLATION)
        if self.state is OrderState.CANCELLED and at >= self.selection.cutoff_at:
            raise PolicyError(PolicyCode.TIMING_VIOLATION)


def prepare_order(intent, *, calendar, policy, accepted_at, through_date):
    """Simulate acceptance after deterministic selection; no durable or trusted effect."""
    if type(intent) is not OrderIntent:
        _invalid()
    accepted_at = _instant(accepted_at)
    if accepted_at < intent.submitted_at:
        raise PolicyError(PolicyCode.TIMING_VIOLATION)
    selection = select_session(calendar, exchange=intent.exchange, accepted_at=accepted_at,
                               policy=policy, through_date=through_date)
    return OrderSnapshot(intent, selection, OrderState.ACCEPTED, accepted_at)


def validate_transition(before, after):
    """Validate a proposed edge and unchanged intent/session/policy. No global history."""
    if type(before) is not OrderSnapshot or type(after) is not OrderSnapshot:
        _invalid()
    if before.intent != after.intent or before.selection != after.selection:
        raise PolicyError(PolicyCode.INTENT_CHANGED)
    if after.changed_at < before.changed_at:
        raise PolicyError(PolicyCode.TIMING_VIOLATION)
    permitted = {
        OrderState.ACCEPTED: (OrderState.FROZEN, OrderState.CANCELLED, OrderState.EXPIRED),
        OrderState.FROZEN: (OrderState.AWAITING_PRICE, OrderState.EXPIRED),
        OrderState.AWAITING_PRICE: (OrderState.EXPIRED,),
    }
    if after.state not in permitted.get(before.state, ()):
        raise PolicyError(PolicyCode.INVALID_TRANSITION)


def transition_order(order, state, *, at, expiration=None):
    """Return a new snapshot, preserving all accepted intent and selection fields."""
    if type(order) is not OrderSnapshot:
        _invalid()
    result = replace(order, state=state, changed_at=at, expiration=expiration)
    validate_transition(order, result)
    return result
