"""Strict v1 persistence of simulation claims; decoding grants no authority."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
import json

from .execution_contracts import (CalendarCoverage, CalendarSession, SessionStatus,
                                  SourceReference, _instant)
from .models import Side, identifier
from .order_lifecycle import (ExpirationEvidence, ExpirationReason, OrderIntent,
                              OrderSnapshot, OrderState, SealStatus, validate_transition)
from .session_policy import SessionPolicy, SessionSelection
from .storage import _decimal_text


class PersistenceCode(str, Enum):
    INVALID_RECORD = 'INVALID_RECORD'
    MISSING_ORDER = 'MISSING_ORDER'


class PersistenceError(ValueError):
    def __init__(self, code):
        self.code = PersistenceCode(code)
        super().__init__(self.code.value)


def _invalid():
    raise PersistenceError(PersistenceCode.INVALID_RECORD)


def _fields(value, names):
    if type(value) is not dict or set(value) != set(names.split()):
        _invalid()
    return value


def _text(value):
    if type(value) is not str:
        _invalid()
    return value


def _integer(value):
    if type(value) is not int or value < 1:
        _invalid()
    return value


def _time(value):
    return None if value is None else _instant(datetime.fromisoformat(_text(value)))


def _stamp(value):
    return None if value is None else _instant(value).isoformat(timespec='microseconds')


def _duration(value):
    return None if value is None else timedelta(microseconds=_integer(value))


def _micros(value):
    return None if value is None else (value.days * 86400 + value.seconds) * 1000000 + value.microseconds


def _source_data(value):
    return dict(provider=value.provider, dataset=value.dataset, record_id=value.record_id,
                provenance_id=value.provenance_id, revision_id=value.revision_id,
                supersedes_revision_id=value.supersedes_revision_id)


def _session_data(value):
    return dict(exchange=value.exchange, session_date=value.session_date.isoformat(),
                trading_timezone=value.trading_timezone, source=_source_data(value.source),
                coverage=value.coverage.value, status=value.status.value, session_id=value.session_id,
                regular_open=_stamp(value.regular_open), regular_close=_stamp(value.regular_close),
                early_close=value.early_close, delayed_open_at=_stamp(value.delayed_open_at))


def _session(value):
    v = _fields(value, 'exchange session_date trading_timezone source coverage status session_id '
                      'regular_open regular_close early_close delayed_open_at')
    source = _fields(v['source'], 'provider dataset record_id provenance_id revision_id supersedes_revision_id')
    return CalendarSession(v['exchange'], date.fromisoformat(_text(v['session_date'])),
                           v['trading_timezone'], SourceReference(**source),
                           CalendarCoverage(v['coverage']), SessionStatus(v['status']), v['session_id'],
                           _time(v['regular_open']), _time(v['regular_close']), v['early_close'],
                           _time(v['delayed_open_at']))


def _snapshot_data(value):
    i, s, e = value.intent, value.selection, value.expiration
    return dict(version=1, intent=dict(order_id=i.order_id, portfolio_id=i.portfolio_id,
        instrument_id=i.instrument_id, exchange=i.exchange, side=i.side.value,
        quantity=_decimal_text(i.quantity), submitted_at=_stamp(i.submitted_at),
        idempotency_key=i.idempotency_key), selection=dict(session=_session_data(s.session),
        policy=dict(version=s.policy.version, freeze_before_open_us=_micros(s.policy.freeze_before_open),
                    price_wait_after_close_us=_micros(s.policy.price_wait_after_close)),
        accepted_at=_stamp(s.accepted_at)), state=value.state.value, changed_at=_stamp(value.changed_at),
        expiration=None if e is None else dict(reason=e.reason.value, seal_status=e.seal_status.value,
            revised_session=None if e.revised_session is None else _session_data(e.revised_session)))


def _snapshot(value):
    v = _fields(value, 'version intent selection state changed_at expiration')
    if type(v['version']) is not int or v['version'] != 1:
        _invalid()
    i = _fields(v['intent'], 'order_id portfolio_id instrument_id exchange side quantity submitted_at idempotency_key')
    s = _fields(v['selection'], 'session policy accepted_at')
    p = _fields(s['policy'], 'version freeze_before_open_us price_wait_after_close_us')
    intent = OrderIntent(i['order_id'], i['portfolio_id'], i['instrument_id'], i['exchange'],
                         Side(i['side']), Decimal(_text(i['quantity'])), _time(i['submitted_at']),
                         i['idempotency_key'])
    policy = SessionPolicy(p['version'], _duration(p['freeze_before_open_us']), _duration(p['price_wait_after_close_us']))
    selection = SessionSelection(_session(s['session']), policy, _time(s['accepted_at']))
    expiration = None
    if v['expiration'] is not None:
        e = _fields(v['expiration'], 'reason seal_status revised_session')
        expiration = ExpirationEvidence(ExpirationReason(e['reason']), SealStatus(e['seal_status']),
            None if e['revised_session'] is None else _session(e['revised_session']))
    return OrderSnapshot(intent, selection, OrderState(v['state']), _time(v['changed_at']), expiration)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid()
        value[key] = item
    return value


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _load(text):
    if type(text) is not str or len(text) > 32768:
        _invalid()
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: _invalid())


def encode_snapshot(value):
    try:
        if type(value) is not OrderSnapshot:
            _invalid()
        data = _snapshot_data(value)
        if _snapshot(data) != value:
            _invalid()
        return _dump(data)
    except (ValueError, TypeError, ArithmeticError, AttributeError, RecursionError):
        raise PersistenceError(PersistenceCode.INVALID_RECORD) from None


def decode_snapshot(text):
    try:
        value = _snapshot(_load(text))
        if encode_snapshot(value) != text:
            _invalid()
        return value
    except (ValueError, TypeError, ArithmeticError, KeyError, RecursionError):
        raise PersistenceError(PersistenceCode.INVALID_RECORD) from None


@dataclass(frozen=True)
class OrderEvent:
    """Stored simulation event. recorded_at is supplied explicitly, never clock-filled."""
    event_id: str
    command_id: str
    revision: int
    prior_state: OrderState | None
    snapshot: OrderSnapshot
    recorded_at: datetime

    def __post_init__(self):
        identifier(self.event_id)
        identifier(self.command_id)
        _integer(self.revision)
        if self.prior_state is not None and type(self.prior_state) is not OrderState:
            _invalid()
        encode_snapshot(self.snapshot)
        object.__setattr__(self, 'recorded_at', _instant(self.recorded_at))
        if self.recorded_at < self.snapshot.changed_at:
            _invalid()
        if self.revision == 1:
            if self.prior_state is not None or self.snapshot.state is not OrderState.ACCEPTED:
                _invalid()
        elif self.prior_state is None:
            _invalid()


def encode_event(value):
    try:
        if type(value) is not OrderEvent:
            _invalid()
        # Revalidate even an object modified outside the frozen-dataclass contract.
        OrderEvent(value.event_id, value.command_id, value.revision, value.prior_state,
                   value.snapshot, value.recorded_at)
        return _dump(dict(version=1, event_id=value.event_id, command_id=value.command_id,
            revision=value.revision, prior_state=None if value.prior_state is None else value.prior_state.value,
            snapshot=_snapshot_data(value.snapshot), recorded_at=_stamp(value.recorded_at)))
    except (ValueError, TypeError, ArithmeticError, AttributeError, RecursionError):
        raise PersistenceError(PersistenceCode.INVALID_RECORD) from None


def decode_event(text):
    try:
        v = _fields(_load(text), 'version event_id command_id revision prior_state snapshot recorded_at')
        if type(v['version']) is not int or v['version'] != 1:
            _invalid()
        result = OrderEvent(v['event_id'], v['command_id'], v['revision'],
            None if v['prior_state'] is None else OrderState(v['prior_state']),
            _snapshot(v['snapshot']), _time(v['recorded_at']))
        if encode_event(result) != text:
            _invalid()
        return result
    except (ValueError, TypeError, ArithmeticError, KeyError, RecursionError):
        raise PersistenceError(PersistenceCode.INVALID_RECORD) from None


def reconstruct_order(initial, events):
    """Replay complete ordered history using the existing pure lifecycle validator."""
    try:
        initial = decode_snapshot(encode_snapshot(initial))
        if initial.state is not OrderState.ACCEPTED or type(events) is not tuple or not events:
            _invalid()
        current, recorded, identities = initial, None, set()
        for revision, event in enumerate(events, 1):
            event = decode_event(encode_event(event))
            if (event.revision != revision or event.event_id in identities
                    or (recorded is not None and event.recorded_at < recorded)):
                _invalid()
            if revision == 1:
                if event.snapshot != initial:
                    _invalid()
            else:
                if event.prior_state is not current.state:
                    _invalid()
                validate_transition(current, event.snapshot)
            identities.add(event.event_id)
            current, recorded = event.snapshot, event.recorded_at
        return current
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        raise PersistenceError(PersistenceCode.INVALID_RECORD) from None
