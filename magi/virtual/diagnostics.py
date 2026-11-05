"""Internal read-only SQLite diagnostics; no initialization or write API."""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import os
import sqlite3
import stat

from .models import VirtualError, Instrument, identifier
from .storage import VirtualLedger, APPLICATION_ID, _schema
from .order_storage import _extension as _orders, _history
from .reservation_storage import _extension as _reservations, _replay as _reservation_replay
from .transition_storage import _extension as _transitions, _replay as _transition_replay


# Committed rowid-table PRIMARY KEY/UNIQUE constraints, in schema version order.
# Each distinct constraint creates sqlite_autoindex_<table>_<ordinal> (SQL NULL).
# Keep this catalog independent of the inspected database; no rootpage assumptions.
_AUTOINDEXES = (
    (1, 'virtual_portfolios', 2),
    (1, 'virtual_instruments', 3),
    (1, 'virtual_events', 4),
    (2, 'virtual_orders', 3),
    (2, 'virtual_order_commands', 4),
    (2, 'virtual_order_events', 3),
    (3, 'virtual_reservation_operations', 4),
    (3, 'virtual_order_reservations', 5),
    (4, 'virtual_transition_baselines', 1),
    (4, 'virtual_transition_outcomes', 4),
    (4, 'virtual_reservation_releases', 4),
)


class Status(str, Enum):
    HEALTHY = 'HEALTHY'
    INCONSISTENT = 'INCONSISTENT'
    CORRUPT = 'CORRUPT'
    UNSUPPORTED_SCHEMA = 'UNSUPPORTED_SCHEMA'
    INCOMPLETE_EVIDENCE = 'INCOMPLETE_EVIDENCE'
    UNVERIFIED = 'UNVERIFIED'


@dataclass(frozen=True)
class Check:
    name: str
    result: str  # PASS, FAIL, NOT_RUN, NOT_APPLICABLE


@dataclass(frozen=True)
class Diagnostic:
    local_status: Status
    schema_version: int | None
    checks: tuple[Check, ...]
    issue_code: str | None
    acknowledgment_status: Status = Status.UNVERIFIED
    scope: str = 'WHOLE_VIRTUAL_DATABASE'


class _Unavailable(Exception):
    pass


def _explicit_catalog(manifest):
    """Enrich trusted committed CREATE headers, not arbitrary or source SQL."""
    rows = []
    for name, sql in manifest.items():
        header = sql.splitlines()[0].split()
        if header == ['CREATE', 'TABLE', name, '(']:
            kind, owner = 'table', name
        elif (len(header) > 5 and header[:5] == ['CREATE', 'UNIQUE', 'INDEX', name, 'ON']
              and '(' in header[5]):
            kind, owner = 'index', header[5].split('(', 1)[0]
        elif (len(header) == 7 and header[:4] == ['CREATE', 'TRIGGER', name, 'BEFORE']
              and header[4] in ('INSERT', 'UPDATE', 'DELETE') and header[5] == 'ON'):
            kind, owner = 'trigger', header[6]
        else:
            raise ValueError('Unsupported committed schema header')
        if not manifest.get(owner, '').startswith('CREATE TABLE ' + owner + ' (\n'):
            raise ValueError('Unknown committed schema owner')
        rows.append((kind, name, owner, sql))
    return rows


def _source(path, limit):
    if (not isinstance(path, (str, Path)) or type(limit) is not int
            or not 4096 <= limit <= 64 * 1024 * 1024):
        raise _Unavailable()
    # Reuse only the existing path validator, never the initializing constructor.
    probe = object.__new__(VirtualLedger)
    probe.path, probe._stat_identity = Path(path), None
    probe._safe_path()
    info = probe.path.stat()  # Missing files are never created.
    if not stat.S_ISREG(info.st_mode) or not 100 <= info.st_size <= limit:
        raise _Unavailable()
    probe._stat_identity = (info.st_dev, info.st_ino)
    _source_state(probe)
    return probe


def _source_state(probe):
    probe._safe_path()
    if any(os.path.lexists(str(probe.path) + suffix)
           for suffix in ('-journal', '-wal', '-shm')):
        raise _Unavailable()


def diagnose(path, *, max_bytes=64 * 1024 * 1024):
    """Observe one SQLite read transaction on an explicit private virtual path.

    Supports local rollback-journal databases with standard SQLite locking only.
    WAL/sidecars fail closed. Exclusive connection locking is configured BEFORE
    the first read, so a raced WAL switch cannot initialize a shared-memory file.
    No source image, custom lock, immutable/nolock URI or recovery writer exists.
    """
    names = ('snapshot', 'schema', 'physical', 'foreign_keys', 'ledger', 'orders',
             'reservations', 'transitions')
    checks = {name: 'NOT_RUN' for name in names}
    version = None
    stage = 'snapshot'
    def result(status, code=None):
        if status is not Status.HEALTHY:
            checks[stage] = 'FAIL'
        return Diagnostic(status, version, tuple(Check(n, checks[n]) for n in names), code)
    connection = None
    try:
        probe = _source(path, max_bytes)
        connection = sqlite3.connect(probe.path.as_uri() + '?mode=ro', uri=True,
                                     timeout=0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        # Connection-local locking policy, not journal-mode mutation. In WAL mode
        # SQLite must obtain an exclusive lock instead of opening shared memory.
        # Read-only/contended WAL opens consequently fail without creating SHM.
        if connection.execute('PRAGMA locking_mode = EXCLUSIVE').fetchone()[0] != 'exclusive':
            raise _Unavailable()
        connection.execute('PRAGMA query_only = ON')
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA temp_store = MEMORY')
        connection.execute('BEGIN')
        # BEGIN alone is deferred: this read establishes the single snapshot.
        connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
        if connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise _Unavailable()
        _source_state(probe)
        pages = connection.execute('PRAGMA page_count').fetchone()[0]
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        if pages * page_size > max_bytes:
            raise _Unavailable()
        checks[stage] = 'PASS'
        stage = 'schema'
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        app = connection.execute('PRAGMA application_id').fetchone()[0]
        if app != APPLICATION_ID or version not in (1, 2, 3, 4):
            return result(Status.UNSUPPORTED_SCHEMA, 'UNSUPPORTED_SCHEMA')
        expected = _schema()[1]
        for introduced, extension in ((2, _orders), (3, _reservations), (4, _transitions)):
            if version >= introduced:
                expected = expected | extension()[1]
        # Partition every catalog row without deduplicating either side. Explicit
        # SQL and full identity must match trusted headers; NULL SQL matches autoindexes.
        explicit, automatic = [], []
        for kind, name, owner, sql in connection.execute(
                'SELECT type, name, tbl_name, sql FROM sqlite_master'):
            if sql is None:
                automatic.append((kind, name, owner))
            else:
                explicit.append((kind, name, owner, sql))
        expected_automatic = [('index', f'sqlite_autoindex_{table}_{ordinal}', table)
                              for introduced, table, count in _AUTOINDEXES
                              if version >= introduced
                              for ordinal in range(1, count + 1)]
        if (sorted(explicit) != sorted(_explicit_catalog(expected))
                or sorted(automatic) != sorted(expected_automatic)):
            return result(Status.UNSUPPORTED_SCHEMA, 'SCHEMA_MISMATCH')
        checks[stage] = 'PASS'
        for name, minimum in (('orders', 2), ('reservations', 3), ('transitions', 4)):
            if version < minimum:
                checks[name] = 'NOT_APPLICABLE'
        stage = 'physical'
        if [r[0] for r in connection.execute('PRAGMA integrity_check')] != ['ok']:
            return result(Status.CORRUPT, 'PHYSICAL_INTEGRITY')
        checks[stage] = 'PASS'
        stage = 'foreign_keys'
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            return result(Status.INCONSISTENT, 'FOREIGN_KEY_INTEGRITY')
        checks[stage] = 'PASS'
        stage = 'ledger'
        for row in connection.execute('SELECT * FROM virtual_instruments ORDER BY instrument_id'):
            Instrument(*row)
        for row in connection.execute('SELECT portfolio_id FROM virtual_portfolios ORDER BY portfolio_id'):
            identifier(row[0])
            VirtualLedger._events(connection, row[0])
        checks[stage] = 'PASS'
        if version >= 2:
            stage = 'orders'
            for row in connection.execute('SELECT portfolio_id, order_id FROM virtual_orders ORDER BY portfolio_id, order_id'):
                _history(connection, *row)
            checks[stage] = 'PASS'
        if version >= 3:
            # v4 is indivisible: never label the partial original-obligation replay complete.
            stage = 'transitions' if version == 4 else 'reservations'
            (_transition_replay if version == 4 else _reservation_replay)(connection)
            checks['reservations'] = 'PASS'
            checks[stage] = 'PASS'
        stage = 'snapshot'
        _source_state(probe)
        return result(Status.HEALTHY)
    except (OSError, _Unavailable):
        return result(Status.UNVERIFIED, 'SOURCE_UNAVAILABLE')
    except VirtualError:
        if stage == 'snapshot':
            return result(Status.UNVERIFIED, 'SOURCE_UNAVAILABLE')
        return result(Status.INCONSISTENT, 'INVALID_' + stage.upper())
    except sqlite3.Error as exc:
        primary = getattr(exc, 'sqlite_errorcode', 0) & 255
        if primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
            return result(Status.CORRUPT, 'SQLITE_CORRUPTION')
        return result(Status.UNVERIFIED, 'SQLITE_UNAVAILABLE')
    except (ValueError, TypeError, ArithmeticError, KeyError, IndexError):
        # Existing codecs normalize canonical/semantic errors to fixed ValueError codes.
        return result(Status.INCONSISTENT, 'INVALID_' + stage.upper())
    finally:
        if connection is not None:
            connection.close()
