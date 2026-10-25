"""Offline price/calendar claims. Validation is never source trust or fill authority."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import re
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ErrorCode, VirtualError, identifier, number, usd


def _invalid():
    raise VirtualError(ErrorCode.INVALID_INPUT)


def _reference(value):
    """Bounded opaque source identifiers, never URLs, paths or free-form payloads."""
    if (type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value)
            or '..' in value):
        _invalid()


def _exchange(value):
    # MIC shape only; no claim of registry membership or listing authority.
    if type(value) is not str or not re.fullmatch(r'[A-Z0-9]{4}', value):
        _invalid()


def _instant(value):
    """Exact, unambiguous instant; reject imaginary and ambiguous IANA wall times."""
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        _invalid()
    try:
        utc = value.astimezone(timezone.utc)
        if isinstance(value.tzinfo, ZoneInfo):
            wall = value.replace(tzinfo=None)
            if (utc.astimezone(value.tzinfo).replace(tzinfo=None) != wall
                    or value.replace(fold=0).utcoffset() != value.replace(fold=1).utcoffset()):
                _invalid()
        return utc
    except (OverflowError, ValueError):
        _invalid()


@dataclass(frozen=True)
class SourceReference:
    """Unverified normalized provenance claims; no credential/endpoint fields."""
    provider: str
    dataset: str
    record_id: str
    provenance_id: str
    revision_id: str | None = None
    supersedes_revision_id: str | None = None

    def __post_init__(self):
        for value in (self.provider, self.dataset, self.record_id):
            _reference(value)
        identifier(self.provenance_id)
        for value in (self.revision_id, self.supersedes_revision_id):
            if value is not None:
                _reference(value)
        if self.supersedes_revision_id is not None and (
                self.revision_id is None or self.revision_id == self.supersedes_revision_id):
            _invalid()


class PriceClassification(str, Enum):
    OFFICIAL_OPEN = 'OFFICIAL_OPEN'
    INDICATIVE_OPEN = 'INDICATIVE_OPEN'
    INTRADAY_QUOTE = 'INTRADAY_QUOTE'
    HISTORICAL_ADJUSTED = 'HISTORICAL_ADJUSTED'
    CLOSE = 'CLOSE'
    VALUATION_ESTIMATE = 'VALUATION_ESTIMATE'
    BENCHMARK = 'BENCHMARK'


class TimestampPrecision(str, Enum):
    SECOND = 'SECOND'
    MILLISECOND = 'MILLISECOND'
    MICROSECOND = 'MICROSECOND'
    SESSION_DATE = 'SESSION_DATE'
    UNKNOWN = 'UNKNOWN'


class AdjustmentStatus(str, Enum):
    UNADJUSTED = 'UNADJUSTED'
    ADJUSTED = 'ADJUSTED'
    UNKNOWN = 'UNKNOWN'


class OfficialStatus(str, Enum):
    OFFICIAL = 'OFFICIAL'
    NONOFFICIAL = 'NONOFFICIAL'
    UNKNOWN = 'UNKNOWN'


class FinalityStatus(str, Enum):
    FINAL = 'FINAL'
    PRELIMINARY = 'PRELIMINARY'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class PriceObservation:
    """A potential price record, not a quote conversion, trusted price or fill.

    observed_at is optional PROVIDER observation time. retrieved_at is collector
    retrieval completion (called observed_at in the Phase 8.3a policy prose).
    Missing event time stays None; session-only precision cannot invent an instant.
    """
    source: SourceReference
    provider_instrument_id: str
    exchange: str
    currency: str
    session_id: str
    price: Decimal
    classification: PriceClassification
    event_at: datetime | None
    precision: TimestampPrecision
    observed_at: datetime | None
    retrieved_at: datetime
    adjustment: AdjustmentStatus
    official_status: OfficialStatus
    finality: FinalityStatus
    instrument_id: str | None = None
    corporate_action_ref: str | None = None

    def __post_init__(self):
        if type(self.source) is not SourceReference:
            _invalid()
        _reference(self.provider_instrument_id)
        _reference(self.session_id)
        _exchange(self.exchange)
        usd(self.currency)
        number(self.price, maximum=Decimal('1000000'), positive=True)
        if self.instrument_id is not None:
            identifier(self.instrument_id)
        if self.corporate_action_ref is not None:
            _reference(self.corporate_action_ref)
        for value, enum in ((self.classification, PriceClassification), (self.precision, TimestampPrecision),
                            (self.adjustment, AdjustmentStatus), (self.official_status, OfficialStatus),
                            (self.finality, FinalityStatus)):
            if type(value) is not enum:
                _invalid()
        if (self.classification is PriceClassification.OFFICIAL_OPEN
                and self.official_status is not OfficialStatus.OFFICIAL):
            _invalid()
        if (self.classification is PriceClassification.INDICATIVE_OPEN
                and self.official_status is OfficialStatus.OFFICIAL):
            _invalid()
        if (self.classification is PriceClassification.HISTORICAL_ADJUSTED
                and self.adjustment is not AdjustmentStatus.ADJUSTED):
            _invalid()
        coarse = self.precision in (TimestampPrecision.SESSION_DATE, TimestampPrecision.UNKNOWN)
        if coarse != (self.event_at is None):
            _invalid()
        for name in ('event_at', 'observed_at', 'retrieved_at'):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _instant(value))
            elif name == 'retrieved_at':
                _invalid()
        if self.event_at is not None:
            divisor = {TimestampPrecision.SECOND: 1000000, TimestampPrecision.MILLISECOND: 1000,
                       TimestampPrecision.MICROSECOND: 1}[self.precision]
            if self.event_at.microsecond % divisor or self.event_at > self.retrieved_at:
                _invalid()
        if self.observed_at is not None and (self.observed_at > self.retrieved_at or (
                self.event_at is not None and self.observed_at < self.event_at)):
            _invalid()


def validate_observation_time(observation, *, validated_at, as_of):
    """Check supplied chronology only, without certifying historical availability.

    No wall-clock lookup, persisted validation attestation, order or authorization.
    A caller-supplied as_of cannot prove what an actual system knew at that time.
    """
    if type(observation) is not PriceObservation:
        _invalid()
    if not observation.retrieved_at <= _instant(validated_at) <= _instant(as_of):
        _invalid()


class CalendarCoverage(str, Enum):
    KNOWN = 'KNOWN'
    UNKNOWN = 'UNKNOWN'


class SessionStatus(str, Enum):
    REGULAR = 'REGULAR'
    CLOSED = 'CLOSED'
    EXCEPTIONAL_CLOSURE = 'EXCEPTIONAL_CLOSURE'
    DELAYED_OPEN = 'DELAYED_OPEN'
    HALTED = 'HALTED'
    SUSPENDED = 'SUSPENDED'
    UNKNOWN = 'UNKNOWN'


def _calendar_identity(exchange, trading_timezone, source):
    _exchange(exchange)
    if (type(source) is not SourceReference or source.revision_id is None
            or type(trading_timezone) is not str):
        _invalid()
    try:
        return ZoneInfo(trading_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        _invalid()


@dataclass(frozen=True)
class CalendarSession:
    """One claimed session/day. Exchange-wide status is not instrument tradability."""
    exchange: str
    session_date: date
    trading_timezone: str
    source: SourceReference
    coverage: CalendarCoverage
    status: SessionStatus
    session_id: str | None = None
    regular_open: datetime | None = None
    regular_close: datetime | None = None
    early_close: bool | None = None
    delayed_open_at: datetime | None = None

    def __post_init__(self):
        zone = _calendar_identity(self.exchange, self.trading_timezone, self.source)
        if (type(self.session_date) is not date or type(self.coverage) is not CalendarCoverage
                or type(self.status) is not SessionStatus):
            _invalid()
        if self.session_id is not None:
            _reference(self.session_id)
        if self.coverage is CalendarCoverage.UNKNOWN:
            if self.status is not SessionStatus.UNKNOWN:
                _invalid()
        elif self.status is SessionStatus.UNKNOWN:
            _invalid()
        no_hours = self.status in (SessionStatus.CLOSED, SessionStatus.EXCEPTIONAL_CLOSURE,
                                   SessionStatus.UNKNOWN)
        if no_hours:
            if any(v is not None for v in (self.regular_open, self.regular_close,
                                           self.early_close, self.delayed_open_at)):
                _invalid()
            return
        if self.session_id is None or type(self.early_close) is not bool:
            _invalid()
        for name in ('regular_open', 'regular_close', 'delayed_open_at'):
            value = getattr(self, name)
            if value is None and name == 'delayed_open_at':
                continue
            utc = _instant(value)
            # Explicit IANA exchange-local input or UTC; reject mislabeled zones.
            if value.tzinfo != timezone.utc and value.tzinfo != zone:
                _invalid()
            local = utc.astimezone(zone)
            if local.date() != self.session_date:
                _invalid()
            _instant(local)  # Reject ambiguous exchange wall times, even from UTC.
            object.__setattr__(self, name, utc)
        if self.regular_open >= self.regular_close:
            _invalid()
        if self.status is SessionStatus.DELAYED_OPEN:
            if self.delayed_open_at is not None and not (
                    self.regular_open < self.delayed_open_at < self.regular_close):
                _invalid()
        elif self.delayed_open_at is not None:
            _invalid()


class ExchangeCalendar(Protocol):
    def get_session(self, exchange: str, session_date: date) -> CalendarSession:
        """Return explicit coverage; never infer openness from weekday or absence."""
        ...


@dataclass(frozen=True)
class SyntheticCalendar:
    """Immutable fixture snapshot only. No real schedules, downloads or selection."""
    exchange: str
    trading_timezone: str
    source: SourceReference
    sessions: tuple[CalendarSession, ...]

    def __post_init__(self):
        _calendar_identity(self.exchange, self.trading_timezone, self.source)
        if type(self.sessions) is not tuple:
            _invalid()
        dates, identities = set(), set()
        for session in self.sessions:
            if (type(session) is not CalendarSession or session.exchange != self.exchange
                    or session.trading_timezone != self.trading_timezone or session.source != self.source
                    or session.session_date in dates or (
                        session.session_id is not None and session.session_id in identities)):
                _invalid()
            dates.add(session.session_date)
            identities.add(session.session_id)

    def get_session(self, exchange, session_date):
        _exchange(exchange)
        if exchange != self.exchange or type(session_date) is not date:
            _invalid()
        for session in self.sessions:
            if session.session_date == session_date:
                return session
        return CalendarSession(self.exchange, session_date, self.trading_timezone, self.source,
                               CalendarCoverage.UNKNOWN, SessionStatus.UNKNOWN)


def validate_price_session(observation, session):
    """Check identity and event containment, NOT official-open eligibility or trust.

    Even a matching FINAL/OFFICIAL observation and REGULAR session grants no rights.
    Delayed/halted records can be represented; this does not make them executable.
    """
    if type(observation) is not PriceObservation or type(session) is not CalendarSession:
        _invalid()
    if (session.coverage is not CalendarCoverage.KNOWN or session.regular_open is None
            or observation.exchange != session.exchange or observation.session_id != session.session_id
            or observation.event_at is None
            or not session.regular_open <= observation.event_at <= session.regular_close):
        _invalid()
