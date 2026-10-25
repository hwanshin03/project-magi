"""Explicit-path virtual SQLite ledger; event history is the only balance authority."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import json
import os
import re
import sqlite3
import stat

from .accounting import reconstruct
from .models import (ZERO, ErrorCode, Event, EventKind, Instrument, Side, SyntheticFill,
                     VirtualError, identifier, instant, money, usd)

APPLICATION_ID = 1297503281
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _decimal_text(value):
    # Fixed-point normalization does not depend on the caller's Decimal context.
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _request(kind, amount, fill):
    data = {'kind': kind.value, 'amount': _decimal_text(amount), 'currency': 'USD'}
    if fill is not None:
        data['fill'] = dict(fill_id=fill.fill_id, instrument_id=fill.instrument_id,
                            side=fill.side.value, quantity=_decimal_text(fill.quantity),
                            price=_decimal_text(fill.price), fees=_decimal_text(fill.fees),
                            provenance_id=fill.provenance_id, currency=fill.currency)
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def _schema():
    statements, pending = [], ''
    for line in Path(__file__).with_name('schema.sql').read_text().splitlines(True):
        if line.lstrip().startswith('--'):
            continue
        pending += line
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip().rstrip(';'))
            pending = ''
    if pending.strip():
        raise VirtualError(ErrorCode.INVALID_LEDGER)
    objects = {}
    for statement in statements:
        match = re.match(r'CREATE (?:TABLE|UNIQUE INDEX|TRIGGER) (\w+)', statement)
        if match:
            objects[match[1]] = statement
    return statements, objects


class VirtualLedger:
    """Trusted internal synthetic boundary, not a multiuser or production execution API.

    Require an absolute *.virtual.sqlite3 path outside the checkout. The existing
    parent directory must be privately controlled by the caller. No paths or parent
    directories are inferred or created. Injected clock is for offline testing.
    """
    def __init__(self, path, *, clock=None, timeout=5.0):
        if (not isinstance(path, (str, Path)) or not str(path)
                or type(timeout) not in (int, float) or not 0 < timeout <= 30):
            raise VirtualError(ErrorCode.UNSAFE_STORAGE)
        self.path = Path(path)
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        if not callable(self.clock):
            raise VirtualError(ErrorCode.INVALID_INPUT)
        self.timeout = timeout
        self._stat_identity = None
        statements, self._objects = _schema()
        self._safe_path()
        created = False
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
            created = True
        except FileExistsError:
            pass
        except OSError:
            raise VirtualError(ErrorCode.STORAGE_UNAVAILABLE) from None
        self._safe_path()
        self._stat_identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        if created:
            with self._connection(write=True, verify=False) as connection:
                for statement in statements:
                    connection.execute(statement)
                self._verify_schema(connection)
        else:
            # Existing foreign/owner-shaped databases are never initialized or migrated.
            with self._connection():
                pass

    def _safe_path(self):
        p = self.path
        try:
            if (not p.is_absolute() or not p.name.endswith('.virtual.sqlite3')
                    or p.is_symlink() or not p.parent.is_dir()
                    or p.resolve().is_relative_to(REPOSITORY_ROOT)
                    or any(part in ('.git', '.aws', '.ssh', '.codex', '.venv') for part in p.parts)):
                raise VirtualError(ErrorCode.UNSAFE_STORAGE)
            if p.exists():
                info = p.stat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_mode & 0o077 or info.st_uid != os.getuid()
                        or (self._stat_identity is not None and
                            (info.st_dev, info.st_ino) != self._stat_identity)):
                    raise VirtualError(ErrorCode.UNSAFE_STORAGE)
        except OSError:
            raise VirtualError(ErrorCode.UNSAFE_STORAGE) from None

    def _verify_schema(self, connection):
        objects = dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"))
        if (connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                or connection.execute('PRAGMA user_version').fetchone()[0] != 1
                or objects != self._objects):
            raise VirtualError(ErrorCode.INVALID_LEDGER)

    @contextmanager
    def _connection(self, *, write=False, verify=True):
        self._safe_path()
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                                         uri=True, timeout=self.timeout)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys = ON')
            if not write:
                connection.execute('PRAGMA query_only = ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if verify:
                self._verify_schema(connection)
            yield connection
            connection.commit()
        except sqlite3.Error:
            raise VirtualError(ErrorCode.STORAGE_UNAVAILABLE) from None
        finally:
            if connection is not None:
                connection.close()  # Rolls back any uncommitted operation, including non-SQL failures.

    def register_instrument(self, instrument):
        """Explicit internal synthetic allowlist; no ticker discovery or listing claim."""
        if type(instrument) is not Instrument or replace(instrument) != instrument:
            raise VirtualError(ErrorCode.INVALID_INPUT)
        values = (instrument.instrument_id, instrument.symbol, instrument.market, instrument.currency)
        with self._connection(write=True) as connection:
            found = connection.execute('SELECT * FROM virtual_instruments WHERE instrument_id=? OR (symbol=? AND market=? AND currency=?)', values).fetchall()
            if found:
                if len(found) != 1 or tuple(found[0]) != values:
                    raise VirtualError(ErrorCode.IDENTITY_CONFLICT)
                return
            connection.execute('INSERT INTO virtual_instruments VALUES (?,?,?,?)', values)

    def create_portfolio(self, portfolio_id, *, currency='USD'):
        identifier(portfolio_id); usd(currency)
        with self._connection(write=True) as connection:
            connection.execute('INSERT INTO virtual_portfolios VALUES (?,?) ON CONFLICT(portfolio_id) DO NOTHING',
                               (portfolio_id, currency))

    @staticmethod
    def _portfolio(connection, portfolio_id):
        if connection.execute('SELECT currency FROM virtual_portfolios WHERE portfolio_id=?',
                              (portfolio_id,)).fetchone() is None:
            raise VirtualError(ErrorCode.UNKNOWN_PORTFOLIO)

    @staticmethod
    def _events(connection, portfolio_id):
        events = []
        try:
            for row in connection.execute('SELECT * FROM virtual_events WHERE portfolio_id=? ORDER BY sequence', (portfolio_id,)):
                fill = None
                if row['kind'] != 'FUNDING':
                    fill = SyntheticFill(row['fill_id'], row['instrument_id'], Side(row['kind']),
                                         Decimal(row['quantity']), Decimal(row['price']),
                                         row['provenance_id'], Decimal(row['fees']), row['currency'])
                    instrument = connection.execute('SELECT * FROM virtual_instruments WHERE instrument_id=?',
                                                    (fill.instrument_id,)).fetchone()
                    if instrument is None:
                        raise ValueError()
                    Instrument(*instrument)
                event = Event(row['sequence'], row['event_id'], row['portfolio_id'], EventKind(row['kind']),
                              row['currency'], Decimal(row['amount']), fill,
                              datetime.fromisoformat(row['effective_at']), datetime.fromisoformat(row['recorded_at']),
                              row['idempotency_key'])
                if row['request_json'] != _request(event.kind, event.amount, event.fill):
                    raise ValueError()
                events.append(event)
            reconstruct(portfolio_id, events)
        except (ValueError, TypeError, ArithmeticError):
            raise VirtualError(ErrorCode.INVALID_LEDGER) from None
        return tuple(events)

    def history(self, portfolio_id):
        identifier(portfolio_id)
        with self._connection() as connection:
            self._portfolio(connection, portfolio_id)
            return self._events(connection, portfolio_id)

    def state(self, portfolio_id):
        return reconstruct(portfolio_id, self.history(portfolio_id))

    def fund(self, portfolio_id, amount, *, idempotency_key, confirmed=False):
        if confirmed is not True:
            raise VirtualError(ErrorCode.CONFIRMATION_REQUIRED)
        return self._append(portfolio_id, EventKind.FUNDING, money(amount, positive=True), None, idempotency_key)

    def apply_synthetic_fill(self, portfolio_id, fill, *, idempotency_key):
        """Offline/internal only: a SyntheticFill is not authentication or trusted market data."""
        if type(fill) is not SyntheticFill or replace(fill) != fill:
            raise VirtualError(ErrorCode.INVALID_INPUT)
        return self._append(portfolio_id, EventKind(fill.side.value), ZERO, fill, idempotency_key)

    def _append(self, portfolio_id, kind, amount, fill, key):
        identifier(portfolio_id); identifier(key)
        request = _request(kind, amount, fill)
        with self._connection(write=True) as connection:
            self._portfolio(connection, portfolio_id)
            events = self._events(connection, portfolio_id)
            previous = connection.execute('SELECT request_json, sequence FROM virtual_events WHERE portfolio_id=? AND idempotency_key=?',
                                          (portfolio_id, key)).fetchone()
            if previous:
                if previous['request_json'] != request:
                    raise VirtualError(ErrorCode.IDEMPOTENCY_CONFLICT)
                return events[previous['sequence'] - 1]
            if kind is EventKind.FUNDING and events:
                raise VirtualError(ErrorCode.ALREADY_FUNDED)
            if fill is not None:
                if not connection.execute('SELECT 1 FROM virtual_instruments WHERE instrument_id=?', (fill.instrument_id,)).fetchone():
                    raise VirtualError(ErrorCode.UNKNOWN_INSTRUMENT)
                if any(e.fill is not None and e.fill.fill_id == fill.fill_id for e in events):
                    raise VirtualError(ErrorCode.DUPLICATE_FILL)
            now = instant(self.clock())
            if events and now < events[-1].recorded_at:
                raise VirtualError(ErrorCode.CLOCK_REGRESSION)
            event = Event(len(events) + 1, str(uuid4()), portfolio_id, kind, 'USD', amount, fill, now, now, key)
            reconstruct(portfolio_id, (*events, event))
            fields = (None,) * 6 if fill is None else (
                fill.fill_id, fill.instrument_id, _decimal_text(fill.quantity), _decimal_text(fill.price),
                _decimal_text(fill.fees), fill.provenance_id)
            connection.execute('INSERT INTO virtual_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (portfolio_id, event.sequence, event.event_id, kind.value, 'USD',
                                _decimal_text(amount), *fields, now.isoformat(), now.isoformat(), key, request))
            return event
