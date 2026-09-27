"""Read an existing ledger without initializing, migrating, or writing SQLite."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from magi.storage import DEFAULT_DB_PATH, StorageError


class ReadOnlyLedgerDatabase:
    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only = ON')
            if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise StorageError('Ledger schema is unavailable for reconciliation.')
            yield connection
        except (sqlite3.Error, OSError):
            raise StorageError('Ledger is unavailable for reconciliation.') from None
        finally:
            if connection is not None:
                connection.close()
