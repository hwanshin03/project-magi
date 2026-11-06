"""Internal synthetic staging only. No activation or source database access."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import stat

from . import backup
from .models import identifier

_LIMIT = 64 * 1024 * 1024
_NAMES = ('backup.virtual.sqlite3', 'manifest.json')


@dataclass(frozen=True)
class StagingResult:
    outcome: str
    stage: str
    issue: str | None
    operation_id: str | None
    manifest: backup.Manifest | None
    candidate: str | None
    cleanup: str
    acknowledgment: str = 'UNVERIFIED'
    policy: str = 'D3-staged-validation-v1'


class _Failure(ValueError):
    pass


def _require(condition, code='UNSAFE_WORKSPACE'):
    if not condition:
        raise _Failure(code)


def _identity(path, directory=False):
    s = path.lstat()
    _require((stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
             and s.st_uid == os.getuid() and not s.st_mode & 0o077
             and (directory or s.st_nlink == 1))
    return s.st_dev, s.st_ino


def _bundle(path):
    result = (_identity(path, True),)
    _require(set(p.name for p in path.iterdir()) == set(_NAMES), 'INVALID_BUNDLE')
    return result + tuple(_identity(path / n) for n in _NAMES)


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy(source, destination, expected, size, owned):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        _require((info.st_dev, info.st_ino) == expected, 'ORIGINAL_CHANGED')
        target = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            owned[destination.name] = _identity(destination)
            remaining = size
            while remaining:
                block = os.read(fd, min(65536, remaining))
                _require(bool(block), 'COPY_INTERRUPTED')
                remaining -= len(block)
                view = memoryview(block)
                while view:
                    written = os.write(target, view)
                    _require(written > 0, 'COPY_INTERRUPTED')
                    view = view[written:]
            _require(not os.read(fd, 1), 'ORIGINAL_CHANGED')
            os.fsync(target)
        finally:
            os.close(target)
    finally:
        os.close(fd)


def _cleanup(workspace, destination, directory_identity, owned):
    if directory_identity is None:
        return 'NOT_CREATED'
    try:
        workspace.check()
        _require(_identity(destination, True) == directory_identity)
        _require(set(p.name for p in destination.iterdir()) == set(owned))
        for name, identity in owned.items():
            _require(_identity(destination / name) == identity)
        for name in owned:
            (destination / name).unlink()
        destination.rmdir()
        return 'REMOVED'
    except (OSError, ValueError):
        return 'RETAINED_UNVERIFIED'


def _run(input_workspace, bundle, staging_workspace, *, operation_id,
         expected_schema_version, expected_format_version, max_bytes,
         expected_backup_id, expected_sha256, create):
    stage = 'INPUT'
    manifest = candidate = None
    destination = None
    directory_identity = None
    owned = {}
    result = None
    cleanup = 'NOT_CREATED'
    operation = None
    try:
        identifier(operation_id)
        operation = operation_id
        _require(type(expected_format_version) is int and expected_format_version == 1, 'UNSUPPORTED_SCHEMA')
        _require(type(expected_schema_version) is int and expected_schema_version in (1, 2, 3, 4), 'UNSUPPORTED_SCHEMA')
        _require(type(max_bytes) is int and 4096 <= max_bytes <= _LIMIT, 'RESOURCE_LIMIT')
        if expected_backup_id is not None:
            identifier(expected_backup_id)
        _require(expected_sha256 is None or (type(expected_sha256) is str
                 and re.fullmatch('[0-9a-f]{64}', expected_sha256) is not None), 'INVALID_INPUT')
        _require(type(input_workspace) is backup.Workspace and type(staging_workspace) is backup.Workspace)
        input_workspace.check()
        staging_workspace.check()
        a, c = input_workspace.root, staging_workspace.root
        _require(not a.is_relative_to(c) and not c.is_relative_to(a))
        _require(c.name == 'restore-' + operation)
        bundle = Path(bundle)
        _require(bundle.parent == a and bundle.resolve() == bundle)
        if create:
            _require(not tuple(c.iterdir()), 'DESTINATION_EXISTS')
        stage = 'ORIGINAL'
        original_identity = _bundle(bundle)
        manifest = backup.verify_backup(input_workspace, bundle)
        _require(manifest.schema_version == expected_schema_version, 'UNSUPPORTED_SCHEMA')
        _require(manifest.byte_size <= max_bytes, 'RESOURCE_LIMIT')
        _require(expected_backup_id is None or manifest.backup_id == expected_backup_id, 'IDENTITY_MISMATCH')
        _require(expected_sha256 is None or manifest.sha256 == expected_sha256, 'IDENTITY_MISMATCH')
        _require(_bundle(bundle) == original_identity, 'ORIGINAL_CHANGED')
        destination = c / ('backup-' + manifest.backup_id)
        candidate = destination.name + '/' + manifest.artifact
        if create:
            stage = 'CREATE'
            destination.mkdir(mode=0o700)
            directory_identity = _identity(destination, True)
            stage = 'COPY'
            _copy(bundle / _NAMES[0], destination / _NAMES[0], original_identity[1], manifest.byte_size, owned)
            canonical = backup.encode_manifest(manifest).encode('ascii')
            _copy(bundle / _NAMES[1], destination / _NAMES[1], original_identity[2], len(canonical), owned)
        stage = 'ORIGINAL_RECHECK'
        _require(backup.verify_backup(input_workspace, bundle) == manifest, 'ORIGINAL_CHANGED')
        _require(_bundle(bundle) == original_identity, 'ORIGINAL_CHANGED')
        stage = 'CANDIDATE'
        candidate_identity = _bundle(destination)
        _require(not set(original_identity) & set(candidate_identity), 'UNSAFE_WORKSPACE')
        _require(backup.verify_backup(staging_workspace, destination) == manifest, 'INVALID_CANDIDATE')
        stage = 'FINAL'
        if create:
            _sync(destination)
            _sync(c)
        input_workspace.check()
        staging_workspace.check()
        _require(set(p.name for p in c.iterdir()) == {destination.name}, 'INVALID_CANDIDATE')
        _require(_bundle(destination) == candidate_identity, 'INVALID_CANDIDATE')
        _require(_bundle(bundle) == original_identity, 'ORIGINAL_CHANGED')
        result = ('LOCALLY_VALIDATED_CANDIDATE', None)
        cleanup = 'RETAINED_VALIDATED'
    except backup.BackupError as exc:
        code = str(exc)
        allowed = {'MANIFEST_INVALID', 'MANIFEST_MISMATCH', 'DIGEST_MISMATCH', 'ARTIFACT_INCOMPLETE',
                   'ARTIFACT_INVALID', 'ARTIFACT_UNAVAILABLE', 'UNSAFE_PATH', 'LOCAL_UNSUPPORTED_SCHEMA',
                   'LOCAL_CORRUPT', 'LOCAL_INCONSISTENT', 'LOCAL_UNVERIFIED', 'LOCAL_INCOMPLETE_EVIDENCE'}
        result = (code if code in allowed else 'INCOMPLETE_VALIDATION', code if code in allowed else None)
    except _Failure as exc:
        result = (str(exc), str(exc))
    except (OSError, ValueError, TypeError, AttributeError):
        codes = {'CREATE': 'STAGING_CREATION_FAILED', 'COPY': 'COPY_INTERRUPTED',
                 'ORIGINAL': 'INVALID_BUNDLE', 'ORIGINAL_RECHECK': 'ORIGINAL_CHANGED',
                 'CANDIDATE': 'INVALID_CANDIDATE'}
        result = (codes.get(stage, 'INCOMPLETE_VALIDATION'), None)
    finally:
        if cleanup != 'RETAINED_VALIDATED' and directory_identity is not None:
            # Complete candidates that failed validation remain evidence, never eligible.
            if stage in ('CANDIDATE', 'FINAL'):
                cleanup = 'RETAINED_UNVERIFIED'
            else:
                cleanup = _cleanup(staging_workspace, destination, directory_identity, owned)
    return StagingResult(result[0], stage, result[1], operation, manifest, candidate, cleanup)


def stage_candidate(input_workspace, bundle, staging_workspace, *, operation_id,
                    expected_schema_version, expected_format_version=1, max_bytes=_LIMIT,
                    expected_backup_id=None, expected_sha256=None):
    """Trusted offline fixtures only; never opens the original source database."""
    return _run(input_workspace, bundle, staging_workspace, operation_id=operation_id,
                expected_schema_version=expected_schema_version, expected_format_version=expected_format_version,
                max_bytes=max_bytes, expected_backup_id=expected_backup_id, expected_sha256=expected_sha256, create=True)


def validate_staged_candidate(input_workspace, bundle, staging_workspace, *, operation_id,
                              expected_schema_version, expected_format_version=1, max_bytes=_LIMIT,
                              expected_backup_id=None, expected_sha256=None):
    """Read-only observation; caller must retain trusted workspace provenance."""
    return _run(input_workspace, bundle, staging_workspace, operation_id=operation_id,
                expected_schema_version=expected_schema_version, expected_format_version=expected_format_version,
                max_bytes=max_bytes, expected_backup_id=expected_backup_id, expected_sha256=expected_sha256, create=False)
