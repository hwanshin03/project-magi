"""Internal v4 synthetic lifecycle transactions; no admission or settlement writer."""
from dataclasses import replace
from pathlib import Path
import re
import sqlite3

from .atomic_transitions import TransitionOutcome, decode_transition, encode_transition
from .models import ErrorCode, VirtualError, arithmetic, identifier
from .order_codec import encode_event, _stamp
from .order_lifecycle import OrderState, transition_order
from .order_storage import _history
from .reservation_storage import _ReservationDatabase, _replay as _reservation_replay
from .reservations import AvailableResource, Resource
from .storage import VirtualLedger, _decimal_text


_TERMINAL = (OrderState.CANCELLED, OrderState.EXPIRED)


def _extension():
    statements, objects, pending = [], {}, ''
    for line in Path(__file__).with_name('transition_schema.sql').read_text().splitlines(True):
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
        _invalid()
    return statements, objects


class _TransitionDatabase(_ReservationDatabase):
    def __init__(self, path, *, versions=(4,), timeout=5.0):
        super().__init__(path, versions=versions, timeout=timeout)
        self._manifests[4] = self._manifests[3] | _extension()[1]


def _invalid():
    raise VirtualError(ErrorCode.INVALID_LEDGER)


def _validate_edge(previous, command, reservation):
    event = command.event
    before = previous.snapshot
    if event.revision != previous.revision + 1 or event.prior_state is not before.state:
        raise VirtualError(ErrorCode.IDENTITY_CONFLICT)
    if (reservation is None or reservation.reservation_id != command.reservation_id
            or (reservation.portfolio_id, reservation.order_id, reservation.instrument_id) !=
               (before.intent.portfolio_id, before.intent.order_id, before.intent.instrument_id)
            or command.context.received_at < previous.recorded_at):
        _invalid()
    # Pure lifecycle remains authoritative, including evidence, cutoff and FILLED denial.
    expected = transition_order(before, event.snapshot.state, at=event.snapshot.changed_at,
                                expiration=event.snapshot.expiration)
    if expected != event.snapshot:
        _invalid()
    return event.snapshot.state in _TERMINAL


def _outcome_row(command, released):
    e, i = command.event, command.event.snapshot.intent
    return (e.command_id, i.portfolio_id, i.order_id, command.actor_id, command.idempotency_key,
            e.event_id, command.reservation_id, int(released),
            command.reservation_id if released else None, encode_transition(command))


def _release_row(command, reservation):
    return (reservation.reservation_id, command.event.command_id, command.event.event_id,
            reservation.portfolio_id, reservation.order_id, reservation.resource.value,
            _decimal_text(reservation.amount))


def _replay(connection):
    # All v3 creates/denials precede v4. Replay their ORIGINAL availability first;
    # release never retrospectively turns a recorded v3 denial into a success.
    states, reservation_outcomes = _reservation_replay(connection, evolved_orders=True)
    rows = {r['command_id']: r for r in connection.execute('SELECT * FROM virtual_transition_outcomes')}
    baselines = {(r['portfolio_id'], r['order_id']): r['revision'] for r in connection.execute(
        'SELECT * FROM virtual_transition_baselines')}
    orders = tuple(tuple(r) for r in connection.execute(
        'SELECT portfolio_id, order_id FROM virtual_orders ORDER BY portfolio_id, order_id'))
    if set(orders) != set(baselines):
        _invalid()
    reservations = {r.order_id: r for s in states.values() for r in s.reservations}
    outcomes, releases = {}, {}
    for p, order_id in orders:
        _, events = _history(connection, p, order_id)
        baseline = baselines[p, order_id]
        reservation = reservations.get(order_id)
        if (type(baseline) is not int or not 1 <= baseline <= len(events)
                or (reservation is not None and baseline != 1)):
            _invalid()
        for previous, event in zip(events[baseline-1:], events[baseline:]):
            row = rows.get(event.command_id)
            if row is None or event.command_id in outcomes:
                _invalid()
            command = decode_transition(row['request_json'])
            released = _validate_edge(previous, command, reservation)
            if event != command.event or tuple(row) != _outcome_row(command, released):
                _invalid()
            original = connection.execute('SELECT * FROM virtual_order_commands WHERE operation_id=?',
                                           (event.command_id,)).fetchone()
            if tuple(original) != (event.command_id, p, order_id, command.actor_id, 'TRANSITION',
                                   command.idempotency_key, event.revision):
                _invalid()
            outcomes[event.command_id] = TransitionOutcome(command, released)
            if released:
                if reservation.reservation_id in releases:
                    _invalid()
                releases[reservation.reservation_id] = _release_row(command, reservation)
                reservation = None
    if set(rows) != set(outcomes) or releases != {r['reservation_id']: tuple(r) for r in
            connection.execute('SELECT * FROM virtual_reservation_releases')}:
        _invalid()
    with arithmetic():
        for p, state in states.items():
            cash = state.cash.available
            shares = dict(state.shares)
            active = []
            for r in state.reservations:
                if r.reservation_id not in releases:
                    active.append(r)
                elif r.resource is Resource.CASH:
                    cash += r.amount
                else:
                    old = shares[r.instrument_id]
                    shares[r.instrument_id] = AvailableResource(old.posted, old.reserved-r.amount,
                                                               old.available+r.amount)
            states[p] = replace(state, cash=AvailableResource(state.cash.posted, state.cash.posted-cash, cash),
                                shares=tuple(sorted(shares.items())), reservations=tuple(active))
    return states, outcomes, reservation_outcomes


def migrate_transition_schema(path, *, confirmed=False, timeout=5.0):
    """Exact v3 -> v4, explicit and atomic. Never creates a file or rewrites history."""
    if confirmed is not True:
        raise VirtualError(ErrorCode.CONFIRMATION_REQUIRED)
    database = _TransitionDatabase(path, versions=(3, 4), timeout=timeout)
    with database._connection(write=True) as connection:
        if connection.execute('PRAGMA user_version').fetchone()[0] == 3:
            _reservation_replay(connection)
            for statement in _extension()[0]:
                connection.execute(statement)
            # Retain valid unreserved legacy fixture histories without pretending
            # their old commands were funded Package C operations.
            connection.execute('INSERT INTO virtual_transition_baselines '
                'SELECT portfolio_id, order_id, MAX(revision) FROM virtual_order_events '
                'GROUP BY portfolio_id, order_id')
            database._verify_schema(connection)
        _replay(connection)
    return 4


def _transition_synthetic(path, command, *, timeout=5.0):
    """One fixture edge + optional full release + durable outcome, one outer commit.

    No public admission, clock authority, standalone release, financial API or retry.
    """
    payload = encode_transition(command)
    database = _TransitionDatabase(path, timeout=timeout)
    e, i = command.event, command.event.snapshot.intent
    with database._connection(write=True) as connection:
        states, outcomes, _ = _replay(connection)
        for outcome in outcomes.values():
            old = outcome.command
            if (old.event.command_id == e.command_id or
                    (old.event.snapshot.intent.portfolio_id, old.actor_id, old.idempotency_key) ==
                    (i.portfolio_id, command.actor_id, command.idempotency_key)):
                if encode_transition(old) != payload:
                    raise VirtualError(ErrorCode.IDEMPOTENCY_CONFLICT)
                return outcome
        _, events = _history(connection, i.portfolio_id, i.order_id)
        reservation = next((r for r in states[i.portfolio_id].reservations
                            if r.order_id == i.order_id), None)
        released = _validate_edge(events[-1], command, reservation)
        connection.execute('INSERT INTO virtual_order_commands VALUES (?,?,?,?,?,?,?)',
            (e.command_id, i.portfolio_id, i.order_id, command.actor_id, 'TRANSITION',
             command.idempotency_key, e.revision))
        connection.execute('INSERT INTO virtual_order_events VALUES (?,?,?,?,?,?,?,?,?,?)',
            (e.event_id, i.portfolio_id, i.order_id, e.revision, e.command_id, e.prior_state.value,
             e.snapshot.state.value, _stamp(e.snapshot.changed_at), _stamp(e.recorded_at), encode_event(e)))
        if released:
            connection.execute('INSERT INTO virtual_reservation_releases VALUES (?,?,?,?,?,?,?)',
                                _release_row(command, reservation))
        connection.execute('INSERT INTO virtual_transition_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)',
                            _outcome_row(command, released))
        database._verify_schema(connection)
        return _replay(connection)[1][e.command_id]


class TransitionStore:
    """Scoped read-only v4 replay. Scope identifiers are not user authorization."""
    def __init__(self, path, *, timeout=5.0):
        self._database = _TransitionDatabase(path, timeout=timeout)
        with self._database._connection() as connection:
            _replay(connection)

    def state(self, portfolio_id):
        identifier(portfolio_id)
        with self._database._connection() as connection:
            VirtualLedger._portfolio(connection, portfolio_id)
            return _replay(connection)[0][portfolio_id]

    def history(self, portfolio_id, order_id):
        identifier(portfolio_id)
        identifier(order_id)
        with self._database._connection() as connection:
            _replay(connection)
            return _history(connection, portfolio_id, order_id)[1]

    def outcome(self, portfolio_id, actor_id, idempotency_key):
        for value in (portfolio_id, actor_id, idempotency_key):
            identifier(value)
        with self._database._connection() as connection:
            VirtualLedger._portfolio(connection, portfolio_id)
            return next((o for o in _replay(connection)[1].values() if
                (o.command.event.snapshot.intent.portfolio_id, o.command.actor_id, o.command.idempotency_key)
                == (portfolio_id, actor_id, idempotency_key)), None)

    def reservation_outcome(self, portfolio_id, actor_id, idempotency_key):
        for value in (portfolio_id, actor_id, idempotency_key):
            identifier(value)
        with self._database._connection() as connection:
            VirtualLedger._portfolio(connection, portfolio_id)
            return next((o for o in _replay(connection)[2].values() if
                (o.request.portfolio_id, o.request.actor_id, o.request.idempotency_key)
                == (portfolio_id, actor_id, idempotency_key)), None)
