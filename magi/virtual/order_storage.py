"""Explicit virtual-only migration and scoped reads. No order or financial writer."""
from pathlib import Path
import re
import sqlite3

from .accounting import reconstruct
from .models import ErrorCode, Instrument, VirtualError, identifier
from .order_codec import (PersistenceCode, PersistenceError, decode_event, decode_snapshot,
                          reconstruct_order, _stamp)
from .order_lifecycle import OrderState
from .storage import APPLICATION_ID, VirtualLedger, _schema


def _extension():
    statements, pending, objects = [], '', {}
    for line in Path(__file__).with_name('order_schema.sql').read_text().splitlines(True):
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


class _Database:
    """Existing-file adapter reusing v1's private path/connection guards, not its writers."""
    _safe_path = VirtualLedger._safe_path
    _connection = VirtualLedger._connection

    def __init__(self, path, *, versions=(2,), timeout=5.0):
        if (not isinstance(path, (str, Path)) or not str(path)
                or type(timeout) not in (int, float) or not 0 < timeout <= 30):
            raise VirtualError(ErrorCode.UNSAFE_STORAGE)
        self.path, self.timeout, self._stat_identity = Path(path), timeout, None
        self._safe_path()
        try:
            info = self.path.stat()  # Never create a missing file, even during migration.
        except OSError:
            raise VirtualError(ErrorCode.UNSAFE_STORAGE) from None
        self._stat_identity = (info.st_dev, info.st_ino)
        _, original = _schema()
        _, extension = _extension()
        self._manifests = {1: original, 2: original | extension}
        self._versions = versions

    def _verify_schema(self, connection):
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        objects = dict(connection.execute('SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL'))
        if (connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                or version not in self._versions or objects != self._manifests[version]):
            raise VirtualError(ErrorCode.INVALID_LEDGER)
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise PersistenceError(PersistenceCode.INVALID_RECORD)


def _history(connection, portfolio_id, order_id):
    row = connection.execute('SELECT * FROM virtual_orders WHERE portfolio_id=? AND order_id=?',
                             (portfolio_id, order_id)).fetchone()
    if row is None:
        raise PersistenceError(PersistenceCode.MISSING_ORDER)
    try:
        initial = decode_snapshot(row['initial_json'])
        if (initial.state is not OrderState.ACCEPTED or initial.intent.order_id != order_id
                or initial.intent.portfolio_id != portfolio_id or initial.intent.instrument_id != row['instrument_id']):
            raise ValueError()
        instrument = connection.execute('SELECT * FROM virtual_instruments WHERE instrument_id=?',
                                         (row['instrument_id'],)).fetchone()
        Instrument(*instrument)
        commands = {}
        for command in connection.execute('SELECT * FROM virtual_order_commands WHERE portfolio_id=? AND order_id=?',
                                           (portfolio_id, order_id)):
            for field in ('operation_id', 'actor_id', 'idempotency_key'):
                identifier(command[field])
            commands[command['operation_id']] = command
        events, used = [], {}
        for record in connection.execute('SELECT * FROM virtual_order_events WHERE portfolio_id=? AND order_id=? ORDER BY revision',
                                         (portfolio_id, order_id)):
            event = decode_event(record['event_json'])
            expected = (event.event_id, portfolio_id, order_id, event.revision, event.command_id,
                        None if event.prior_state is None else event.prior_state.value,
                        event.snapshot.state.value, _stamp(event.snapshot.changed_at), _stamp(event.recorded_at))
            if tuple(record)[:-1] != expected or event.command_id not in commands:
                raise ValueError()
            # One command may account for consecutive legal edges, but not disjoint history.
            if event.command_id in used and used[event.command_id] != event.revision - 1:
                raise ValueError()
            used[event.command_id] = event.revision
            events.append(event)
        current = reconstruct_order(initial, tuple(events))
        if set(commands) != set(used):
            raise ValueError()
        for key, command in commands.items():
            if command['result_revision'] != used[key]:
                raise ValueError()
        first = commands[events[0].command_id]
        if (row['submission_operation_id'] != first['operation_id'] or first['operation_kind'] != 'SUBMIT'
                or first['idempotency_key'] != initial.intent.idempotency_key
                or any(c['operation_kind'] != 'TRANSITION' for k, c in commands.items() if k != first['operation_id'])):
            raise ValueError()
        return current, tuple(events)
    except (ValueError, TypeError, ArithmeticError, KeyError, IndexError):
        raise PersistenceError(PersistenceCode.INVALID_RECORD) from None


def _validate_contents(connection, version):
    if [row[0] for row in connection.execute('PRAGMA integrity_check')] != ['ok']:
        raise VirtualError(ErrorCode.INVALID_LEDGER)
    for row in connection.execute('SELECT * FROM virtual_instruments'):
        Instrument(*row)
    for row in connection.execute('SELECT portfolio_id FROM virtual_portfolios'):
        identifier(row[0])
        VirtualLedger._events(connection, row[0])
    if version == 2:
        for row in connection.execute('SELECT portfolio_id, order_id FROM virtual_orders'):
            _history(connection, *row)


def migrate_order_schema(path, *, confirmed=False, timeout=5.0):
    """Explicit trusted internal operation, not authentication. Existing virtual files only.

    A fresh fixture first uses VirtualLedger to create v1, then calls this API.
    No constructor/read/import migrates. A valid v2 repeat verifies and returns 2.
    """
    if confirmed is not True:
        raise VirtualError(ErrorCode.CONFIRMATION_REQUIRED)
    database = _Database(path, versions=(1, 2), timeout=timeout)
    with database._connection(write=True) as connection:
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        _validate_contents(connection, version)
        if version == 1:
            for statement in _extension()[0]:
                connection.execute(statement)  # No executescript: no implicit transaction commit.
            database._verify_schema(connection)
            _validate_contents(connection, 2)
    return 2


class OrderStore:
    """Internal scoped read/replay only. UUID scope is not team/user authorization."""
    def __init__(self, path, *, timeout=5.0):
        self._database = _Database(path, timeout=timeout)
        with self._database._connection():
            pass

    def history(self, portfolio_id, order_id):
        identifier(portfolio_id)
        identifier(order_id)
        with self._database._connection() as connection:
            return _history(connection, portfolio_id, order_id)[1]

    def order(self, portfolio_id, order_id):
        identifier(portfolio_id)
        identifier(order_id)
        with self._database._connection() as connection:
            return _history(connection, portfolio_id, order_id)[0]

    def orders(self, portfolio_id):
        identifier(portfolio_id)
        with self._database._connection() as connection:
            VirtualLedger._portfolio(connection, portfolio_id)
            values = []
            for row in connection.execute('SELECT order_id FROM virtual_orders WHERE portfolio_id=? ORDER BY order_id', (portfolio_id,)):
                values.append(_history(connection, portfolio_id, row[0])[0])
            return tuple(values)

    def ledger_history(self, portfolio_id):
        identifier(portfolio_id)
        with self._database._connection() as connection:
            VirtualLedger._portfolio(connection, portfolio_id)
            return VirtualLedger._events(connection, portfolio_id)

    def ledger_state(self, portfolio_id):
        return reconstruct(portfolio_id, self.ledger_history(portfolio_id))
