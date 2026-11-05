"""Internal synthetic fixture backups. Trusted fixture process and private local FS only."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import stat

from . import diagnostics
from .models import identifier

_LIMIT = 64 * 1024 * 1024
_ARTIFACT = 'backup.virtual.sqlite3'
_MANIFEST = 'manifest.json'


class BackupError(ValueError):
    """Fixed failure code, with no submitted paths or raw SQLite errors."""


def _fail(code):
    raise BackupError(code) from None


@dataclass(frozen=True)
class Manifest:
    backup_id: str
    schema_version: int
    completed_at: str
    byte_size: int
    sha256: str
    format_version: int = 1
    artifact: str = _ARTIFACT
    environment: str = 'SYNTHETIC_FIXTURE_CLAIM'
    validation: str = 'D1-v1-local-HEALTHY'
    implementation: str = 'D2-sqlite-backup-v1'
    acknowledgment: str = 'UNVERIFIED'

    def __post_init__(self):
        try:
            identifier(self.backup_id)
            if (type(self.format_version) is not int or self.format_version != 1
                    or type(self.schema_version) is not int or self.schema_version not in (1, 2, 3, 4)
                    or type(self.byte_size) is not int or not 100 <= self.byte_size <= _LIMIT
                    or type(self.sha256) is not str or re.fullmatch('[0-9a-f]{64}', self.sha256) is None
                    or type(self.completed_at) is not str):
                _fail('MANIFEST_INVALID')
            stamp = datetime.fromisoformat(self.completed_at)
            if (stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0
                    or stamp.isoformat(timespec='microseconds') != self.completed_at):
                _fail('MANIFEST_INVALID')
            for field, value in (('artifact', _ARTIFACT), ('environment', 'SYNTHETIC_FIXTURE_CLAIM'),
                                 ('validation', 'D1-v1-local-HEALTHY'), ('implementation', 'D2-sqlite-backup-v1'),
                                 ('acknowledgment', 'UNVERIFIED')):
                if type(getattr(self, field)) is not str or getattr(self, field) != value:
                    _fail('MANIFEST_INVALID')
        except (ValueError, TypeError, AttributeError):
            _fail('MANIFEST_INVALID')


def encode_manifest(manifest):
    if type(manifest) is not Manifest:
        _fail('MANIFEST_INVALID')
    validated = Manifest(**asdict(manifest))
    return json.dumps(asdict(validated), sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def decode_manifest(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail('MANIFEST_INVALID')
            result[key] = value
        return result
    try:
        if type(text) is not str or len(text) > 4096:
            _fail('MANIFEST_INVALID')
        values = json.loads(text, object_pairs_hook=unique)
        if type(values) is not dict or set(values) != set(Manifest.__dataclass_fields__):
            _fail('MANIFEST_INVALID')
        manifest = Manifest(**values)
        if encode_manifest(manifest) != text:
            _fail('MANIFEST_INVALID')
        return manifest
    except (ValueError, TypeError, RecursionError):
        _fail('MANIFEST_INVALID')


def _private(path, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or (not directory and info.st_nlink != 1)):
        _fail('UNSAFE_PATH')
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class Workspace:
    """An owned fixture root, not an authentication or data-origin attestation."""
    root: Path
    identity: tuple

    def check(self):
        _root_scope(self.root)
        if _private(self.root, True) != self.identity:
            _fail('UNSAFE_PATH')


def _root_scope(root):
    if (not root.is_absolute() or root.resolve() != root
            or not root.is_relative_to('/private/tmp') or root == Path('/private/tmp')):
        _fail('UNSAFE_PATH')
    for parent in root.parents:
        if parent == Path('/private/tmp'):
            break
        _private(parent, True)


def create_workspace(root):
    """Reserve a NEW private root under /private/tmp for trusted fixture provisioning.

    No database is created. The fixture harness must generate source.virtual.sqlite3
    here using synthetic data. Importing personal/production data violates this API's
    caller contract; file contents cannot prove their real-world provenance.
    """
    try:
        root = Path(root)
        _root_scope(root)
        root.mkdir(mode=0o700)
        return Workspace(root, _private(root, True))
    except BackupError:
        raise
    except (OSError, ValueError, TypeError):
        _fail('UNSAFE_WORKSPACE')


def _paths(workspace, source, destination):
    if type(workspace) is not Workspace:
        _fail('UNSAFE_PATH')
    workspace.check()
    source, destination = Path(source), Path(destination)
    if (source != workspace.root / 'source.virtual.sqlite3'
            or destination.parent != workspace.root
            or re.fullmatch(r'backup-[0-9a-f-]{36}', destination.name) is None):
        _fail('UNSAFE_PATH')
    identifier(destination.name[7:])
    return source, destination


def _digest(path):
    _private(path)
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while block := stream.read(65536):
            size += len(block)
            if size > _LIMIT:
                _fail('ARTIFACT_INVALID')
            digest.update(block)
    return size, digest.hexdigest()


def _healthy(path):
    report = diagnostics.diagnose(path)
    if report.local_status is not diagnostics.Status.HEALTHY:
        _fail('LOCAL_' + report.local_status.value)
    return report


def verify_backup(workspace, destination):
    """Verify a complete local pair; return a strict manifest or a fixed error."""
    try:
        _, destination = _paths(workspace, workspace.root / 'source.virtual.sqlite3', destination)
        _private(destination, True)
        if set(p.name for p in destination.iterdir()) != {_ARTIFACT, _MANIFEST}:
            _fail('ARTIFACT_INCOMPLETE')
        manifest_path = destination / _MANIFEST
        _private(manifest_path)
        with manifest_path.open('rb') as stream:
            data = stream.read(4097)
        manifest = decode_manifest(data.decode('ascii'))
        if destination.name != 'backup-' + manifest.backup_id:
            _fail('MANIFEST_MISMATCH')
        artifact = destination / _ARTIFACT
        before = _digest(artifact)
        if before != (manifest.byte_size, manifest.sha256):
            _fail('DIGEST_MISMATCH')
        report = _healthy(artifact)
        if report.schema_version != manifest.schema_version:
            _fail('MANIFEST_MISMATCH')
        if _digest(artifact) != before:
            _fail('DIGEST_MISMATCH')
        workspace.check()
        return manifest
    except BackupError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        _fail('ARTIFACT_UNAVAILABLE')


def _sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_manifest(path, manifest):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
        stream.write(encode_manifest(manifest))
        stream.flush()
        os.fsync(stream.fileno())


def create_backup(workspace, source, destination):
    """Snapshot a provisioned fixture. Existing destinations always reject.

    The completed manifest is a last-published commit marker. Crashes can leave an
    incomplete directory or a complete pair with an uncertain return. Call verify
    to classify it; retry creation requires a fresh destination. No auto-deletion
    of a prior attempt or unrelated artifact occurs.
    """
    owned = False
    published = False
    source_connection = target_connection = None
    try:
        source, destination = _paths(workspace, source, destination)
        probe = diagnostics._source(source, _LIMIT)
        # Match D1's source opening policy before the first read, including WAL races.
        source_connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True,
                                             timeout=0, isolation_level=None)
        if source_connection.execute('PRAGMA locking_mode=EXCLUSIVE').fetchone()[0] != 'exclusive':
            _fail('SOURCE_UNAVAILABLE')
        source_connection.execute('PRAGMA query_only=ON')
        source_connection.execute('BEGIN')
        source_connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
        if source_connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            _fail('UNSUPPORTED_JOURNAL')
        diagnostics._source_state(probe)
        pages = source_connection.execute('PRAGMA page_count').fetchone()[0]
        page_size = source_connection.execute('PRAGMA page_size').fetchone()[0]
        if pages * page_size > _LIMIT:
            _fail('SOURCE_UNAVAILABLE')
        destination.mkdir(mode=0o700)
        owned = True
        artifact = destination / _ARTIFACT
        descriptor = os.open(artifact, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        target_connection = sqlite3.connect(artifact.as_uri() + '?mode=rw', uri=True,
                                            timeout=0, isolation_level=None)
        steps = 0
        def progress(status, remaining, total):
            nonlocal steps
            steps += 1
            if status not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE) or steps > pages + 1:
                _fail('BACKUP_INTERRUPTED')
        source_connection.backup(target_connection, pages=64, progress=progress, sleep=0)
        target_connection.close()
        target_connection = None
        diagnostics._source_state(probe)
        source_connection.close()
        source_connection = None
        report = _healthy(artifact)
        _sync(artifact)
        size, digest = _digest(artifact)
        manifest = Manifest(destination.name[7:], report.schema_version,
                            datetime.now(timezone.utc).isoformat(timespec='microseconds'), size, digest)
        pending = destination / 'manifest.pending'
        _write_manifest(pending, manifest)
        if decode_manifest(pending.read_text(encoding='ascii')) != manifest or _digest(artifact) != (size, digest):
            _fail('MANIFEST_MISMATCH')
        # link is exclusive, unlike replace/rename which may overwrite an existing file.
        os.link(pending, destination / _MANIFEST)
        pending.unlink()
        _sync(destination)
        _sync(workspace.root)
        result = verify_backup(workspace, destination)
        published = True
        return result
    except BackupError:
        raise
    except FileExistsError:
        _fail('DESTINATION_EXISTS')
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError, diagnostics._Unavailable):
        _fail('BACKUP_UNAVAILABLE')
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        if owned and not published:
            # Only fixed names in this call's exclusive directory. Never recurse.
            for name in (_MANIFEST, 'manifest.pending', _ARTIFACT,
                         _ARTIFACT + '-journal', _ARTIFACT + '-wal', _ARTIFACT + '-shm'):
                try:
                    (destination / name).unlink(missing_ok=True)
                except OSError:
                    pass  # An undeletable incomplete pair is rejected on subsequent verify.
            try:
                destination.rmdir()
            except OSError:
                pass
