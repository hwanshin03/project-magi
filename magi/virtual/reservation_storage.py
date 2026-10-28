"""Exact v3 synthetic reservation storage. No admission, release or financial writer."""
from pathlib import Path
import re
import sqlite3

from .accounting import reconstruct
from .models import ZERO, ErrorCode, Side, VirtualError, arithmetic, identifier
from .order_lifecycle import OrderState
from .order_storage import _Database, _history, _validate_contents
from .reservations import (AvailableResource, ReservationCode, ReservationOutcome, ReservationState,
                           Resource, decode_reservation, encode_reservation)
from .storage import VirtualLedger, _decimal_text


def _extension():
    statements, objects, pending = [], {}, ''
    for line in Path(__file__).with_name('reservation_schema.sql').read_text().splitlines(True):
        if line.lstrip().startswith('--'):
            continue
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip().rstrip(';')
            statements.append(statement)
            match = re.match(r'CREATE (?:TABLE|UNIQUE INDEX|TRIGGER) (\w+)', statement)
            if match:
                objects[match[1]] = statement
            pending = ''
    if pending.strip():
        raise VirtualError(ErrorCode.INVALID_LEDGER)
    return statements, objects


class _ReservationDatabase(_Database):
    def __init__(self, path, *, versions=(3,), timeout=5.0):
        super().__init__(path, versions=versions, timeout=timeout)
        self._manifests[3] = self._manifests[2] | _extension()[1]


def _invalid():
    raise VirtualError(ErrorCode.INVALID_LEDGER)


def _association(connection, request, *, evolved_orders=False):
    # v4 alone reconciles later transitions/releases. The create still binds to
    # the original ACCEPTED event; default v3 requires it to remain the only event.
    current, events = _history(connection, request.portfolio_id, request.order_id)
    first = events[0]
    command = connection.execute('SELECT * FROM virtual_order_commands WHERE operation_id=?',
                                  (request.command_id,)).fetchone()
    if ((not evolved_orders and (len(events) != 1 or current.state is not OrderState.ACCEPTED))
            or current.intent.instrument_id != request.instrument_id
            or first.event_id != request.creation_event_id or first.command_id != request.command_id
            or command is None or command['actor_id'] != request.actor_id
            or command['idempotency_key'] != request.idempotency_key
            or (current.intent.side is Side.BUY) != (request.resource is Resource.CASH)
            or (request.resource is Resource.SHARES and request.amount != current.intent.quantity)):
        _invalid()


def _reservation_row(request):
    return (request.reservation_id, request.operation_id, request.portfolio_id, request.order_id,
            request.instrument_id, request.command_id, request.creation_event_id,
            request.resource.value, _decimal_text(request.amount), request.currency,
            request.status.value, encode_reservation(request))


def _outcome(request, cash, shares):
    if request.resource is Resource.CASH:
        return ReservationCode.RESERVED if request.amount <= cash else ReservationCode.INSUFFICIENT_CASH
    return (ReservationCode.RESERVED if request.amount <= shares[request.instrument_id]
            else ReservationCode.INSUFFICIENT_SHARES)


def _replay(connection, *, evolved_orders=False):
    """One DB snapshot; full old history plus immutable operation/effect reconciliation.

    v3 has no ledger writer or state-operation service. Replaying recorded denials
    therefore uses the same posted ledger, never hypothetical pending proceeds.
    The private v4 caller permits evolved order histories, but this result still
    represents the original v3 creates/denials BEFORE any v4 release effects.
    """
    _validate_contents(connection, 2)
    instruments = tuple(row[0] for row in connection.execute(
        'SELECT instrument_id FROM virtual_instruments ORDER BY instrument_id'))
    states, outcomes, expected_reservations = {}, {}, {}
    with arithmetic():
        for portfolio in connection.execute('SELECT portfolio_id FROM virtual_portfolios ORDER BY portfolio_id'):
            p = portfolio[0]
            posted = reconstruct(p, VirtualLedger._events(connection, p))
            positions = {position.instrument_id: position.quantity for position in posted.positions}
            quantities = {i: positions.get(i, ZERO) for i in instruments}
            cash, shares, active, orders = posted.cash, quantities.copy(), [], set()
            for sequence, row in enumerate(connection.execute(
                    'SELECT * FROM virtual_reservation_operations WHERE portfolio_id=? ORDER BY sequence', (p,)), 1):
                request = decode_reservation(row['request_json'])
                _association(connection, request, evolved_orders=evolved_orders)
                code = _outcome(request, cash, shares)
                result_id = request.reservation_id if code is ReservationCode.RESERVED else None
                expected = (request.operation_id, request.portfolio_id, request.actor_id,
                            request.idempotency_key, sequence, request.order_id, request.command_id,
                            request.creation_event_id, code.value, result_id, encode_reservation(request))
                if tuple(row) != expected or request.portfolio_id != p or request.order_id in orders:
                    _invalid()
                if code is ReservationCode.RESERVED:
                    if request.reservation_id in expected_reservations:
                        _invalid()
                    expected_reservations[request.reservation_id] = _reservation_row(request)
                    active.append(request)
                    orders.add(request.order_id)
                    if request.resource is Resource.CASH:
                        cash -= request.amount
                    else:
                        shares[request.instrument_id] -= request.amount
                outcomes[request.operation_id] = ReservationOutcome(request, code)
            states[p] = ReservationState(p, AvailableResource(posted.cash, posted.cash-cash, cash),
                tuple((i, AvailableResource(quantities[i], quantities[i]-shares[i], shares[i])) for i in instruments),
                tuple(sorted(active, key=lambda r: r.reservation_id)))
    actual = {row['reservation_id']: tuple(row) for row in connection.execute('SELECT * FROM virtual_order_reservations')}
    if actual != expected_reservations:
        _invalid()
    return states, outcomes


def migrate_reservation_schema(path, *, confirmed=False, timeout=5.0):
    """Explicit exact v2 -> v3 only. Valid v3 repeats verify without mutation."""
    if confirmed is not True:
        raise VirtualError(ErrorCode.CONFIRMATION_REQUIRED)
    database = _ReservationDatabase(path, versions=(2, 3), timeout=timeout)
    with database._connection(write=True) as connection:
        if connection.execute('PRAGMA user_version').fetchone()[0] == 2:
            _validate_contents(connection, 2)
            for statement in _extension()[0]:
                connection.execute(statement)
            database._verify_schema(connection)
        _replay(connection)
    return 3


def _reserve_synthetic(path, request, *, timeout=5.0):
    """Internal offline operation for an already persisted, validated fixture order.

    Not authentication or public admission. No fixture seeding, caller balance,
    lifecycle mutation, financial event, automatic retry or clock authority.
    """
    payload = encode_reservation(request)
    database = _ReservationDatabase(path, timeout=timeout)
    with database._connection(write=True) as connection:
        states, outcomes = _replay(connection)
        for outcome in outcomes.values():
            previous = outcome.request
            if (previous.operation_id == request.operation_id or
                    (previous.portfolio_id, previous.actor_id, previous.idempotency_key) ==
                    (request.portfolio_id, request.actor_id, request.idempotency_key)):
                if encode_reservation(previous) != payload:
                    raise VirtualError(ErrorCode.IDEMPOTENCY_CONFLICT)
                return outcome
        _association(connection, request)
        state = states[request.portfolio_id]
        if any(r.reservation_id == request.reservation_id or r.order_id == request.order_id
               for s in states.values() for r in s.reservations):
            raise VirtualError(ErrorCode.IDENTITY_CONFLICT)
        code = _outcome(request, state.cash.available, {i: value.available for i, value in state.shares})
        sequence = 1 + sum(o.request.portfolio_id == request.portfolio_id for o in outcomes.values())
        result_id = request.reservation_id if code is ReservationCode.RESERVED else None
        connection.execute('INSERT INTO virtual_reservation_operations VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (request.operation_id, request.portfolio_id, request.actor_id, request.idempotency_key,
             sequence, request.order_id, request.command_id, request.creation_event_id, code.value, result_id, payload))
        if code is ReservationCode.RESERVED:
            connection.execute('INSERT INTO virtual_order_reservations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                _reservation_row(request))
        database._verify_schema(connection)
        return _replay(connection)[1][request.operation_id]


class ReservationStore:
    """Scoped read-only v3 view. UUID scope is not ownership authorization."""
    def __init__(self, path, *, timeout=5.0):
        self._database = _ReservationDatabase(path, timeout=timeout)
        with self._database._connection() as connection:
            _replay(connection)

    def state(self, portfolio_id):
        identifier(portfolio_id)
        with self._database._connection() as connection:
            VirtualLedger._portfolio(connection, portfolio_id)
            return _replay(connection)[0][portfolio_id]

    def reservation(self, portfolio_id, order_id):
        identifier(portfolio_id)
        identifier(order_id)
        with self._database._connection() as connection:
            _history(connection, portfolio_id, order_id)
            state = _replay(connection)[0][portfolio_id]
            return next((r for r in state.reservations if r.order_id == order_id), None)

    def outcome(self, portfolio_id, actor_id, idempotency_key):
        for value in (portfolio_id, actor_id, idempotency_key):
            identifier(value)
        with self._database._connection() as connection:
            VirtualLedger._portfolio(connection, portfolio_id)
            outcomes = _replay(connection)[1]
            return next((o for o in outcomes.values() if
                (o.request.portfolio_id, o.request.actor_id, o.request.idempotency_key) ==
                (portfolio_id, actor_id, idempotency_key)), None)
