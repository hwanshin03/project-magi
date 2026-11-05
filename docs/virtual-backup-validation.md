# Synthetic virtual backup and validation (Package D2)

## Scope and import policy

`magi.virtual.backup` is an internal, offline fixture facility. It creates and
validates SQLite backup artifacts. It has no CLI, public endpoint, broker client,
owner-storage import, credential reader, deployment integration, or scheduler.
It does not restore, activate, migrate, repair, admit orders, release reservations,
settle trades, establish operator fencing, or provide Beta readiness.

The only existing-file change is two lines in
`VirtualAccountingTests.test_import_closure_contains_no_owner_broker_or_network_modules`.
They permit `hashlib` for the exact path `magi/virtual/backup.py`. The original
global allowlist and every assertion remain unchanged. All virtual Python files,
including the new module, remain in normal inspection scope. No other module gains
an import permission. Production SHA-256 uses ordinary `import hashlib` and
`hashlib.sha256()`; no dynamic import, shell hashing, or custom cryptography exists.

Negative policy tests exercise the original test against isolated synthetic trees:
`backup_extra.py`, `storage.py`, and `backup_manifest.py` cannot import `hashlib`;
`backup.py` cannot import `ctypes`, `subprocess`, or `tempfile`. Existing permitted
SQLite and execution-contract timezone imports remain permitted.

## Actual source trust boundary

A trusted offline fixture process calls `create_workspace` with a NEW absolute,
canonical directory below `/private/tmp`. Creation is exclusive, mode 0700; it
records the directory device/inode. Intermediate directories below `/private/tmp`
must be private, owned by the current user, and free of symlink traversal. Each
operation rechecks the root identity and path boundary.

The caller must generate synthetic contents in that workspace. The explicit source
must be exactly `<workspace>/source.virtual.sqlite3`. The explicit destination must
be a direct child named `backup-<canonical UUID>`. No current-directory lookup,
default source, `magi.db` fallback, implicit database creation, or migration exists.
Source validation reuses D1's non-initializing private-file checks, including
ownership, permissions, link count, size, path identity, and sidecar rejection.

This is filesystem isolation within a trusted fixture process, **not authorization
or proof of data origin**. A Workspace object, private path, UUID, or manifest claim
cannot prove that contents are synthetic. Copying personal or production data into
the workspace violates the caller contract and cannot be detected from its bytes.
Untrusted same-user code, arbitrary callers, malicious concurrent path replacement,
and hostile filesystem administrators are outside this internal facility's scope.
Use beyond the controlled fixture process requires a separate trusted provisioning
and authorization boundary. The automated tests provision only synthetic fixtures,
with a separate guard rejecting database access outside their owned temporary root.

## SQLite consistency and supported configurations

Only local filesystems with normal SQLite locking and rollback journal mode
`DELETE` are supported. WAL, existing `-wal`/`-shm`/`-journal` sidecars, hot journals,
unavailable locks, and unsupported modes fail closed. The implementation never
changes the source journal mode, checkpoints it, or uses `immutable=1`/`nolock=1`.

One source connection opens with `mode=ro`, zero busy timeout, and connection-local
exclusive locking configured before its first read (the committed D1 opening
policy). It enables `query_only`, begins a transaction, and reads the catalog to
establish the snapshot. Journal mode, source identity/sidecars, and logical page
size are checked inside that transaction. `Connection.backup()` copies this same
read snapshot into a new destination connection; no main-file copy is used.
The source transaction stays open until copying and source-state rechecking finish.

The backup uses 64-page steps and zero retry sleep. Busy/error progress callbacks
and a conservative step bound fail closed. Source and artifact limits are 64 MiB.
These are size/iteration limits, not a total-runtime guarantee for D1 replay or a
stalled filesystem operation. Destination and source connections close before the
authoritative byte digest. D1 validates the completed copy, so its schema and all
histories are checked in the exact copied state rather than a second source read.

A synthetic second connection can begin a pending write, but its commit is blocked
by the reader; the test rolls it back and verifies the coherent backup. An exclusive
writer already holding the lock causes failure. Supported cooperating writers may
commit before snapshot establishment or after the reader closes. The completion
timestamp is an observation, not a transaction watermark or receipt boundary.
Tests compare unchanged source bytes and all populated-v4 table rows.

## Immutable manifest and byte binding

The frozen `Manifest` has exactly these fields:

| Field | Contract |
| --- | --- |
| `format_version` | Exact integer 1 |
| `backup_id` | Canonical UUID matching the destination directory |
| `artifact` | Fixed `backup.virtual.sqlite3` filename |
| `schema_version` | Exact integer 1, 2, 3, or 4, verified by D1 |
| `environment` | `SYNTHETIC_FIXTURE_CLAIM`; trusted caller assertion only |
| `completed_at` | UTC ISO timestamp, six fractional digits and `+00:00` |
| `byte_size` | Exact integer, 100 bytes through 64 MiB |
| `sha256` | 64 lowercase hexadecimal characters |
| `validation` | `D1-v1-local-HEALTHY` |
| `implementation` | `D2-sqlite-backup-v1` |
| `acknowledgment` | Always `UNVERIFIED` |

Encoding is ASCII JSON with sorted keys, compact separators, and no extra whitespace
or newline. Decoding rejects inputs over 4096 characters, duplicate/missing/extra
fields, wrong types (including boolean integers), invalid values, and any encoding
that differs from canonical re-encoding. No source identity, account data, financial
rows, secrets, or independent external receipt evidence is invented or embedded.

SHA-256 streams the completed artifact in 64 KiB blocks after SQLite closes. Both
length and digest must match. Verification checks private regular files, the exact
two-file directory contents, manifest/directory identity, binding, full D1 health
and schema version, then rechecks the digest. Tests cover one-byte mutation,
truncation, incorrect digest/length, swapped artifacts/manifests, and a rehashed
invalid database that still fails D1.

A matching digest proves artifact/manifest consistency only. It does not establish
authenticity, source ownership, freshness, receipt completeness, or physical storage
durability. There are no signatures, keys, or authentication claims.

## Publication, interruption, and retry

The exclusive private destination directory is the staging location. The artifact
is exclusively created mode 0600 and opened with `mode=rw`; existing destinations
always reject. After copying, connection closure, full D1 validation and artifact
fsync, the digest and manifest are created. `manifest.pending` is exclusively written
mode 0600, flushed, fsynced, decoded, and checked against the artifact. An exclusive
hard link publishes it as `manifest.json`; the pending link is removed, and the
destination and workspace directories are fsynced. A final independent verification
must succeed before the function returns.

`manifest.json` is a completion marker, not an atomic multi-file transaction. Readers
must always call `verify_backup`; marker existence alone is insufficient. A crash
between linking and unlinking the pending file leaves an extra file and is rejected.
Process-crash tests cover an incomplete directory before manifest creation and a
verified complete pair before the caller receives success. An uncertain return is
resolved by explicit verification, never by silently overwriting or deleting the
earlier destination. Retrying creation at an occupied destination rejects.

Ordinary failure or KeyboardInterrupt closes connections and attempts to remove
only fixed artifact/sidecar/manifest names in the directory exclusively created by
that call. Cleanup does not recurse or remove prior attempts. Failed cleanup can
leave an incomplete directory; future creation rejects it and verification fails
unless a complete valid pair remains. There is no automatic retry or repair.

Fixed `BackupError` categories cover unsafe paths/workspaces, source or backup
unavailability, unsupported journals, existing destinations, interrupted copying,
incomplete/invalid/unavailable artifacts, malformed manifests, identity mismatch,
digest mismatch, and `LOCAL_<D1 status>`. Submitted paths and underlying exception
messages are not included in the error text. Disk/permission errors, partial writes,
validation failures, publication failure, and interrupted copying are tested.

The design assumes a private local POSIX filesystem with exclusive creation, hard
links, fsync, and SQLite locking. Unsupported operations fail without fallback.
Windows, shared/network filesystems, hostile same-user races, and power-loss recovery
are not validated. Fsync use and process-crash tests do not prove physical durability.

## Full D1 validation and limitations

The committed `diagnostics.diagnose` remains unchanged. It checks exact v1-v4 schema
catalogs (including D1-A01/A02/A03 protections), physical integrity, foreign keys,
all ledger histories, order histories, reservations, and v4 releases/outcomes with
cross-portfolio reconciliation. No duplicate schema checker or financial replay is
implemented. Supported older versions are validated as-is, without migration.
Populated-v4 tests retain ledger, orders, reservations, releases, and command outcomes.

`HEALTHY` means local consistency only. Acknowledgment completeness remains
`UNVERIFIED`. D3 would need separately reviewed restoration/activation boundaries,
trusted source provisioning, authorization, independent receipt evidence, generation
fencing, crash/power-loss validation, and operating procedures. None is implemented
or authorized by this package.

## Offline validation

Permanent tests cover manifest strictness, all supported versions, populated v4,
path/permission/link rejection, source preservation, competing writer behavior,
WAL/sidecars, tampering, corrupt and unsupported schemas/history, simulated disk and
publication failures, crash recovery classification, and the exact import exception.
Existing D1 and virtual accounting/order/reservation/transition tests are retained.

The temporary regression harness uses an empty environment, disables dotenv and the
SDK import-time IPv6 probe, redirects the owner secret-validator path to an absent
temporary file while preserving test-specific overrides, blocks sockets/DNS/real
HTTP transports, and rejects database paths outside the owned fixture root. Python
child tests receive the same guards. Existing Toss tests may construct only explicitly
recognized synthetic credentials with in-memory MockTransport; D2 constructs none.
No permanent safety guard or existing test assertion is weakened.

Final validation on macOS, Python 3.12.14 / SQLite 3.53.1:

| Check | Result |
| --- | --- |
| New D2 tests | 56 passed, 36 subtests |
| Focused D2/D1/accounting/order/reservation/transition regression | 530 passed, 166 subtests |
| Full pytest | 1,965 passed, 1,249 subtests |
| Full unittest discovery | 1,965 passed |
| Unrestricted pytest (`python_files=*.py`, repository root) | 1,965 passed, 1,249 subtests |
| Python syntax | 172 files passed |
| MAGI imports | 126 submodules plus the root package passed |
| Dependencies | Five pins match; pip check passes; no dependency changes |
| Original import policy and negative policy cases | Passed |
| Changed-file whitespace and secret-pattern scans | Passed |

The initial full regression passed 1,964 tests. A direct manifest-serialization
failure case was added during final review, then focused and all three full discovery
runs passed again. Simplification removed an unused import and kept one manifest
representation, the existing D1 replay/catalog validator, fixed filenames, and
fixed failure codes. The test fixture root was made explicit so Mac temporary-path
defaults do not make the tests depend on the temporary guard's configuration.

Guard counters recorded zero real network attempts/transmissions, credential reads,
production database accesses, real broker initialization, and test repository writes.
Across the final three full runs, existing tests constructed 363 synthetic Toss
sessions and 144 in-memory MockTransport broker adapters. These are fixture-only
constructions; focused/D2 tests constructed none. No live provider or broker operation
occurred. Temporary guard adaptations are described above and removed after review.

The only existing tracked-file difference is the authorized two-line test exception;
all other tracked hashes match the baseline. The real `.env` was not read: its
identity, size, modification/change metadata remain unchanged, and Git ignores it.
No production database is present in the repository. Secret-pattern matches outside
the changes are unchanged synthetic rejection fixtures. The existing trailing space
at `magi/melchior.py:33` and Google SDK/Pydantic warning remain outside D2 scope.
HEAD remains `486a54d0529dc72597ba73c93ba23ed551107223` on `main`; staging stays empty.
No commit, push, D3 implementation, restore, or activation is performed.
