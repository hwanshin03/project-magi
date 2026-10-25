"""Pure next-opening policy over explicit synthetic calendars; no operating defaults."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from .execution_contracts import (CalendarCoverage, CalendarSession, SessionStatus, SyntheticCalendar,
                                  _calendar_identity, _exchange, _instant, _invalid, _reference)


class PolicyCode(str, Enum):
    CALENDAR_UNAVAILABLE = 'CALENDAR_UNAVAILABLE'
    CALENDAR_MISMATCH = 'CALENDAR_MISMATCH'
    NO_ELIGIBLE_SESSION = 'NO_ELIGIBLE_SESSION'
    INVALID_TRANSITION = 'INVALID_TRANSITION'
    TIMING_VIOLATION = 'TIMING_VIOLATION'
    INTENT_CHANGED = 'INTENT_CHANGED'
    SETTLEMENT_REQUIRED = 'SETTLEMENT_REQUIRED'
    DEADLINE_REQUIRED = 'DEADLINE_REQUIRED'
    EVIDENCE_UNRESOLVED = 'EVIDENCE_UNRESOLVED'


class PolicyError(ValueError):
    def __init__(self, code):
        self.code = PolicyCode(code)
        super().__init__(self.code.value)


@dataclass(frozen=True)
class SessionPolicy:
    """Explicit simulation inputs. Version is a label, not approval or a registry.

    None for price_wait_after_close disables deadline-based expiry. No five-minute
    cutoff or two-hour evidence deadline is selected implicitly.
    """
    version: str
    freeze_before_open: timedelta
    price_wait_after_close: timedelta | None

    def __post_init__(self):
        _reference(self.version)
        if (type(self.freeze_before_open) is not timedelta
                or not timedelta(0) < self.freeze_before_open < timedelta(days=1)):
            _invalid()
        if self.price_wait_after_close is not None and (
                type(self.price_wait_after_close) is not timedelta or self.price_wait_after_close <= timedelta(0)):
            _invalid()


class SessionEligibility(str, Enum):
    ELIGIBLE = 'ELIGIBLE'
    INELIGIBLE = 'INELIGIBLE'
    UNKNOWN = 'UNKNOWN'
    ELAPSED = 'ELAPSED'


def _cutoff(session, policy):
    try:
        return session.regular_open - policy.freeze_before_open
    except OverflowError:
        _invalid()


def classify_session(session, *, exchange, accepted_at, policy):
    """ELAPSED means this session's admission window has reached its cutoff."""
    if type(session) is not CalendarSession or type(policy) is not SessionPolicy:
        _invalid()
    _exchange(exchange)
    accepted_at = _instant(accepted_at)
    if session.exchange != exchange:
        raise PolicyError(PolicyCode.CALENDAR_MISMATCH)
    if session.coverage is CalendarCoverage.UNKNOWN:
        return SessionEligibility.UNKNOWN
    if session.status is not SessionStatus.REGULAR:
        return SessionEligibility.INELIGIBLE
    if accepted_at >= _cutoff(session, policy):
        return SessionEligibility.ELAPSED
    return SessionEligibility.ELIGIBLE


@dataclass(frozen=True)
class SessionSelection:
    """Unverified simulation snapshot, not proof of first-session choice or authority."""
    session: CalendarSession
    policy: SessionPolicy
    accepted_at: datetime

    def __post_init__(self):
        if type(self.session) is not CalendarSession:
            _invalid()
        object.__setattr__(self, 'accepted_at', _instant(self.accepted_at))
        if classify_session(self.session, exchange=self.session.exchange, accepted_at=self.accepted_at,
                            policy=self.policy) is not SessionEligibility.ELIGIBLE:
            raise PolicyError(PolicyCode.NO_ELIGIBLE_SESSION)
        # Validate representability now, including optional deadline overflow.
        self.deadline_at

    @property
    def cutoff_at(self):
        return _cutoff(self.session, self.policy)

    @property
    def deadline_at(self):
        if self.policy.price_wait_after_close is None:
            return None
        try:
            return self.session.regular_close + self.policy.price_wait_after_close
        except OverflowError:
            _invalid()


def select_session(calendar, *, exchange, accepted_at, policy, through_date):
    """First eligible local date within an explicit inclusive search horizon.

    Every date from local acceptance day through the chosen date needs coverage,
    including weekends. Unknown/missing coverage stops immediately. Tuple order
    never decides the result. Only the existing in-memory synthetic adapter runs.
    """
    if type(calendar) is not SyntheticCalendar or type(policy) is not SessionPolicy or type(through_date) is not date:
        _invalid()
    _exchange(exchange)
    if exchange != calendar.exchange:
        raise PolicyError(PolicyCode.CALENDAR_MISMATCH)
    accepted_at = _instant(accepted_at)
    zone = _calendar_identity(calendar.exchange, calendar.trading_timezone, calendar.source)
    try:
        first = accepted_at.astimezone(zone).date()
    except OverflowError:
        _invalid()
    if through_date < first:
        raise PolicyError(PolicyCode.NO_ELIGIBLE_SESSION)
    # At most one iteration per supplied day plus the first missing day.
    for ordinal in range(first.toordinal(), through_date.toordinal() + 1):
        session = calendar.get_session(exchange, date.fromordinal(ordinal))
        eligibility = classify_session(session, exchange=exchange, accepted_at=accepted_at, policy=policy)
        if eligibility is SessionEligibility.UNKNOWN:
            raise PolicyError(PolicyCode.CALENDAR_UNAVAILABLE)
        if eligibility is SessionEligibility.ELIGIBLE:
            return SessionSelection(session, policy, accepted_at)
    raise PolicyError(PolicyCode.NO_ELIGIBLE_SESSION)
