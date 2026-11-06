# Package D3: isolated synthetic restore staging

This implementation follows [the approved D3 design](virtual-staged-restore-design.md).
It adds only `magi/virtual/staged_restore.py`, its offline tests, and this document.
Existing D1/D2 implementations, import policy, schemas, dependencies, agents,
configuration, and README are unchanged. Project MAGI remains the internal name;
MOIRAI remains only a proposed brand candidate.

## Scope and trust

D3 operates on B, a completed D2 bundle, and C, a separate candidate. It does not
open A, the original virtual database. There is no implicit source lookup, owner
DB access, migration, repair, settlement, order admission, or fallback to `magi.db`.

Trusted synthetic fixture provisioning is an external precondition. Workspace
objects, UUIDs, manifests, and `SYNTHETIC_FIXTURE_CLAIM` are not authentication or
proof of origin. Use one trusted process/attempt per private fixture context on
supported local storage. Network shares, hostile same-user races, privileged
attackers, and production recovery are outside this contract.

There is no restore activation, active-path switching, writer restart, operator
fencing, recovery generation, external receipt proof, or production/Beta readiness
claim. Acknowledgment completeness is always `UNVERIFIED`.

## Internal API and layout

`stage_candidate(input_workspace, bundle, staging_workspace, *, operation_id,
expected_schema_version, expected_format_version=1, max_bytes=64*1024*1024,
expected_backup_id=None, expected_sha256=None)` creates a candidate.

`validate_staged_candidate` takes the same arguments and only observes an existing
candidate. It does not create, overwrite, clean up, repair, or activate it. Both
calls return a frozen `StagingResult`; invalid arguments fail closed.

Both workspace arguments must be existing `backup.Workspace` objects. D3 reuses
`Workspace.check()` unchanged: canonical absolute paths under `/private/tmp`,
owned private ancestors/root, and the recorded root device/inode. Workspaces must
be disjoint and non-nested. The input bundle is a canonical direct child of its
workspace. The staging root must be `restore-<operation_id>` and empty for creation.
Provision that root explicitly using D2 `create_workspace`; D3 does not infer it.

Layout:

```text
input-root/backup-<D2-backup-id>/
    backup.virtual.sqlite3
    manifest.json
restore-<D3-operation-id>/backup-<same-D2-backup-id>/
    backup.virtual.sqlite3
    manifest.json
```

The operation identity is distinct in role from the original D2 backup identity.
No replacement backup identity or manifest is generated. Optional identity/digest
pins restrict the verified original; they do not authenticate its source.

## Verification and copying

1. Check both workspaces, isolation, input parameters, and destination emptiness.
2. Check exact bundle contents and private directory/single-link regular files.
3. Call unchanged `backup.verify_backup(input_workspace, bundle)`.
4. Bind the returned strict Manifest to schema/format, size, optional pins, and
   observed device/inode identities; recheck those identities.
5. Exclusively create the private candidate directory (0700) and each file (0600).
6. Copy artifact and original manifest bytes using no-follow file opens,
   exclusive destination creation, and bounded 65,536-byte reads. Handle short
   writes, premature EOF, growth, and flush errors. Sync each completed file.
7. Reverify B through D2 and compare its entire Manifest and filesystem identities.
8. Check C independently: exact layout, separate device/inode identities, full D2
   verification, and exact equality to the original verified Manifest.
9. Sync candidate/root directories for creation and recheck roots, layout, and
   observed identities before returning local success.

`encode_manifest` is used only to bound the canonical source manifest's length;
it does not replace the copied bytes. D2 independently enforces canonical ASCII
JSON, exact fields, filename/folder identity, the 4096-byte manifest limit, artifact
size/SHA-256, and supported schema. D3 imports no `hashlib` and implements no hash
algorithm. Its imports remain within the original virtual-package allowlist.

Each D2 verification invokes full D1 read-only reconciliation and verifies the
artifact digest before and after it. Therefore B is fully verified twice and C
once. D3 does not duplicate schema validation, financial replay, or SQL logic.
Schema versions 1–4 are supported without migration; DELETE journal assumptions
and rejection of unsupported sidecars/journal modes are inherited from D1/D2.

D1 covers exact schema/application identity, funding and ledger replay, cash,
shares, cost basis, realized P&L, orders, reservations/releases, terminal command
outcomes, cross-portfolio relationships, and migration boundaries as applicable.
SQL integrity and foreign-key checks alone do not establish financial consistency.

## Result interpretation

The immutable result contains `outcome`, `stage`, fixed `issue` code or null,
validated `operation_id` or null, verified `manifest` or null, relative candidate
artifact name or null, `cleanup`, `acknowledgment`, and policy identity
`D3-staged-validation-v1`. It contains no absolute submitted path or raw exception
text. A populated manifest/candidate field on a failure is context, not success.

| Outcome family | Meaning |
| --- | --- |
| `LOCALLY_VALIDATED_CANDIDATE` | Supported local artifact binding and complete reconciliation passed |
| `INVALID_BUNDLE`, `ARTIFACT_INCOMPLETE`, `ARTIFACT_UNAVAILABLE` | Missing, incomplete, invalid, or inaccessible bundle |
| `MANIFEST_INVALID`, `MANIFEST_MISMATCH` | Invalid canonical manifest or manifest/schema/folder mismatch |
| `DIGEST_MISMATCH`, `ARTIFACT_INVALID` | Artifact size/digest or bounded-artifact verification failed |
| `UNSAFE_WORKSPACE`, `UNSAFE_PATH` | Unsupported path, permissions, type, link, or identity |
| `DESTINATION_EXISTS` | Creation root occupied; nothing overwritten |
| `STAGING_CREATION_FAILED`, `COPY_INTERRUPTED` | Exclusive creation or copy/flush failed |
| `ORIGINAL_CHANGED`, `IDENTITY_MISMATCH` | Original changed or explicit expected identity does not match |
| `INVALID_CANDIDATE` | Candidate layout/identity/binding failed |
| `UNSUPPORTED_SCHEMA`, `LOCAL_UNSUPPORTED_SCHEMA` | Requested or actual version unsupported |
| `LOCAL_CORRUPT`, `LOCAL_INCONSISTENT` | D1 structural corruption or financial-history inconsistency |
| `LOCAL_UNVERIFIED`, `LOCAL_INCOMPLETE_EVIDENCE`, `INCOMPLETE_VALIDATION` | Required validation could not establish success |
| `INVALID_INPUT`, `RESOURCE_LIMIT` | Unsupported input shape or copy limit |

`stage` distinguishes INPUT, ORIGINAL, CREATE, COPY, ORIGINAL_RECHECK, CANDIDATE,
and FINAL. D2's fixed diagnostic code is retained even when raised during candidate
verification; the stage establishes which artifact failed. Unrecognized D2 errors
are reduced to INCOMPLETE_VALIDATION. Programming errors outside the enumerated
exceptions propagate and never create a success result. KeyboardInterrupt also
propagates through ownership-checked cleanup.

## Failure, cleanup, and retry

Only a directory and files exclusively created by the current call can be removed.
Before cleanup, D3 rechecks the staging workspace, directory identity, exact owned
names, and every file identity/type/permissions/link count. Unknown or substituted
files stop cleanup. There is no recursive deletion. Removal errors produce
`RETAINED_UNVERIFIED`; partial removal is possible, never a successful candidate.
The original bundle and staging root are never cleanup targets.

Complete candidates reaching candidate/final verification are preserved even on
failure as unverified evidence. `REMOVED` means this call's candidate was removed;
`NOT_CREATED` means this call owns no candidate directory, not that the path is
absent. `RETAINED_VALIDATED` is only paired with local success.

Creation retries reject any occupied staging root. After a process crash or an
uncertain return, explicit `validate_staged_candidate` runs full original and
candidate checks again. A complete valid candidate can then pass; partial or
corrupt candidates fail. There is no durable command idempotency or persistent
success marker. Process termination need not run cleanup. fsync and crash tests
are not proof of power-loss durability.

## Limits

The artifact limit is at most 64 MiB; the optional D3 copy limit may be between
4096 bytes and that ceiling. D2's original verification still uses its own 64 MiB
limit before the narrower D3 check. Copy buffering is bounded, but D1 replay memory,
CPU, and filesystem I/O time are not bounded by that buffer. D3 has no hard overall
runtime deadline, capacity reservation, worker, or concurrency guarantee.

A candidate consumes one artifact plus a small manifest and filesystem overhead.
Repeated original/candidate verification incurs full replay cost. There is no disk
cache or automatic retention service. Tests simulate copy/write/sync failures and
process exits, not physical disk exhaustion or actual power loss.

Filesystem identities are observations under the trusted fixture threat model.
They do not defeat all hostile same-user replacement races. A result does not make
a file immutable forever and is not a reusable authorization capability. Any future
privileged use must freshly validate identity, integrity, and authorization.

## Offline evidence

The D3 tests cover all four schema versions; exact bytes and independent inodes;
source absence and forbidden source opens; frozen results; canonical manifest and
artifact failures; wrong pins and limits; links, permissions, nonregular files,
relative/traversal/nested/replaced roots; original substitution; candidate corruption;
SQL-valid inconsistent releases; unsupported rehashed schema; short writes; cleanup
refusal/failure; and repeated explicit validation after corruption.

A populated v4 fixture is compared table-for-table, including multiple portfolios,
orders, reservations, releases, and outcomes. A separate buy/sell fixture asserts
cash 945, shares 7, book cost 70, realized P&L 15, and unchanged replay after migration
to v4 and D3 copying. No financial writes are performed by D3 itself.

Ten subprocess crash checkpoints cover before/after staging creation, during/after
artifact and manifest copy, original re-verification, candidate verification, after
validation, and uncertain return. Tests assert original preservation, refusal to
overwrite occupied roots, and fail-closed handling of partial candidates.

The temporary external regression harness clears inherited credentials, disables
.env loading, constrains SQLite to its own temporary fixture root, blocks real
network transports and socket operations, and permits only synthetic MockTransport
Toss construction in existing tests. Fresh-process isolation tests receive only
base audit guards, preserving their assertions that owner/broker modules are not
imported. Their TMPDIR is constrained to the same disposable root. An optional
urllib3 import-time IPv6 probe is suppressed; network blocking remains active.
No repository test assertion or import-policy exception is changed. Temporary
harnesses, logs, and synthetic artifacts are removed after validation.

Final offline validation against baseline
`5812e01f2b3c3e65c48b20aba256975eedb3914b`:

| Check | Result |
| --- | --- |
| D3 focused | 71 passed |
| Entire virtual subsystem | 718 passed, 271 subtests |
| Full pytest | 2036 passed, 1249 subtests |
| Full unittest | 2036 passed |
| Unrestricted repository discovery | 2036 passed across 45 test files |
| Python syntax / MAGI imports | 174 files / 128 modules passed |
| Exact dependency pins / pip check | Passed |
| Original virtual import-policy and isolation tests | Passed unchanged |
| New-file secret/whitespace scan and tracked diff checks | Passed |

Final guarded runs recorded zero network attempts, credential reads, and database
access outside temporary fixtures. Each full suite constructed 121 mock Toss
sessions and 48 mock broker adapters; D3/virtual focused tests constructed none.
The Google SDK emitted its existing Pydantic deprecation warning. During harness
development, the optional IPv6 loopback bind probe was blocked; it was then
suppressed before the final runs, with no real network transmission.

## Independent audit and later prerequisites

Audit ownership and cleanup on every interruption, no-follow/exclusive-open
assumptions, B/C identity preservation, actual D2/D1 call order, exact scope/import
policy, and the limitations of path/inode observations under same-user races.
Review that complete replay is required rather than SQL-only validation, and that
neither a frozen result nor a manifest implies authenticated recovery.

Before any Package D4 or production use, separately approve authentication and
trusted provenance, external acknowledgment accounting, writer fencing/generations,
activation/rollback policy, operational resource limits, filesystem/power-loss
validation, and independent audit resolution. This package implements none of them.
README maintenance can later link this implementation and its audit status; README
and all prior architecture documents remain unchanged in this task.
