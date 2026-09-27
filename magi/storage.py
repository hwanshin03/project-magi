"""Local SQLite infrastructure and sanitized failure/credential boundaries."""

import io
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / 'data' / 'magi.db'
ENV_PATH = Path(__file__).resolve().parent.parent / '.env'


class StorageError(Exception):
    """Safe to display: never carries raw SQLite errors or configuration values."""


def timestamp(value=None):
    """Canonical UTC; a date-only execution date means midnight UTC."""
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        if len(value) == 10:
            value += 'T00:00:00+00:00'
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timestamp requires an ISO date or timezone-aware datetime')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def check_sensitive(value):
    """Reject, rather than silently alter, records containing known secrets."""
    try:
        secrets = [v for k, v in os.environ.items() if v and re.search(
            r'API_KEY|CLIENT_ID|TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|AUTH', k, re.I)]
        if ENV_PATH.exists():
            values = dotenv_values(stream=io.StringIO(ENV_PATH.read_text(encoding='utf-8')))
            secrets.extend(v for v in values.values() if v)
    except (OSError, ValueError):
        raise StorageError('Cannot safely validate persistence content.') from None

    def strings(item):
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for key, val in item.items():
                yield from strings(key)
                yield from strings(val)
        elif isinstance(item, (list, tuple)):
            for val in item:
                yield from strings(val)

    for text in strings(value):
        if any(secret in text for secret in secrets) or re.search(
                r'\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|Bearer\s+\S+)', text):
            raise StorageError('Refusing to persist or retrieve sensitive content.')


def json_text(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class Database:
    def __init__(self, path=DEFAULT_DB_PATH, timeout=1.0):
        self.path = Path(path)
        self.timeout = timeout
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Create with private permissions before SQLite writes any records.
            descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            pass
        except OSError:
            raise StorageError('Memory database initialization failed.') from None
        with self.connect() as connection:
            try:
                # Lock before reading the version; concurrent openers cannot race.
                connection.execute('BEGIN IMMEDIATE')
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1, 2):
                    raise StorageError('Unsupported memory schema version.')
                if version == 0:
                    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                        raise StorageError('Portfolio database migration failed.')
                    self._execute_schema(connection, Path(__file__).with_name('schema.sql'))
                elif version == 1:
                    self._execute_schema(connection, Path(__file__).with_name('migrations') / '002_accounts.sql')
                if connection.execute('PRAGMA foreign_key_check').fetchone():
                    raise StorageError('Portfolio database migration failed.')
            except (sqlite3.Error, OSError):
                raise StorageError('Portfolio database migration failed.') from None

    @staticmethod
    def _execute_schema(connection, path):
        # executescript implicitly commits a pending transaction: execute complete
        # statements individually so DDL, version and indexes roll back together.
        statement = ''
        for line in path.read_text(encoding='utf-8').splitlines(True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ''
        if statement.strip():
            raise StorageError('Portfolio database migration failed.')

    @contextmanager
    def connect(self):
        connection = None
        try:
            connection = sqlite3.connect(str(self.path), timeout=self.timeout)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys = ON')
            with connection:
                yield connection
        except (sqlite3.Error, OSError):
            raise StorageError('Memory database operation failed.') from None
        finally:
            if connection is not None:
                connection.close()
