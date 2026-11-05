# Phase 8.3d.4b-R4 — Internal read-only diagnostics

Baseline: `main` at `a077e1c4d435afc81903bc983fb0eaa97a15c8b7`.
This is Package D1 only. It adds internal diagnostics and synthetic tests; it changes
no existing production file, schema, migration, write operation, dependency or README.
Project MAGI remains the repository name; MOIRAI remains a proposed public brand.

## Import-policy resolution

The previous source-image/OFD-lock/worker design was removed. There is no `ctypes`,
`fcntl`, `sys` or `subprocess` import in the production module, no dynamic import,
no native locking, no file copying/deserialization and no external worker.
The unchanged virtual import-policy test permits dataclasses, enum, pathlib, os,
sqlite3 and stat. It scans absolute imports in every virtual module, including the
relative virtual dependencies used here; it is not a general interpreter-wide
transitive import sandbox. No owner storage, Toss, broker or provider is imported by
D1. Existing replay helpers already accept a connection, so no injection-interface
extension was needed.

## SQLite connection and snapshot

`diagnose(path, max_bytes=64*1024*1024)` requires an explicit existing absolute
`*.virtual.sqlite3` path outside the checkout. Existing virtual path protections
validate ownership, private file permissions, regular-file/single-link identity,
forbidden directories and symlinks. A privately controlled parent and ordinary local
SQLite filesystem locking remain caller prerequisites, as in existing virtual storage.
No directory or missing file is created. The accepted byte limit is 4 KiB–64 MiB;
source size and transaction-visible page count are checked against it.

The source is opened once using an escaped `Path.as_uri()` plus `mode=ro`, `uri=True`,
`timeout=0` and explicit transaction control. The connection sets, in order:

1. `PRAGMA locking_mode = EXCLUSIVE`, before any database read.
2. `PRAGMA query_only = ON`, `foreign_keys = ON`, `temp_store = MEMORY`.
3. `BEGIN`, then an explicit schema read to establish the deferred transaction's snapshot.

All schema, integrity, ledger, order, reservation, release and outcome checks consume
that same connection and transaction. Nothing commits or replaces the transaction.
Closing the connection in `finally` releases it on success and expected failures.
There is no `BEGIN IMMEDIATE`, source write handle, initializer, migration, backup,
`immutable=1`, `nolock=1`, journal-mode assignment or independent read connection.

The locking-mode setting is connection-local; it is not a database journal-mode
change. SQLite's exclusive WAL locking uses no shared-memory file. Setting it before
the first read prevents an opening race into ordinary WAL shared-memory initialization.
On the tested runtime, a read-only WAL open fails with IOERR_LOCK or BUSY. D1 also
explicitly rejects any observed journal mode other than DELETE. It does not interpret
successful WAL opening on a different build as supported WAL diagnostics.

A normal read-only open alone was insufficient as a design: WAL can involve source-side
shared-memory files. A deferred BEGIN alone was insufficient to establish a snapshot.
The discarded file-image approach could not substitute for SQLite's transaction rules.
The selected approach uses SQLite locking itself and requires no existing API changes.

## Supported configurations and fail-closed limits

Support is limited to existing local rollback-journal databases, observed as DELETE,
using standard cooperating SQLite locking and a privately controlled source path.
This is not certification of network filesystems, custom VFS implementations, malicious
same-user path replacement, noncooperating raw writers or arbitrary platforms/builds.
The tested Mac runtime is Python 3.12 with SQLite 3.53.1. No global durability or
platform-independent filesystem guarantee is claimed. A reported DELETE mode is not
a persisted durability-profile or synchronous-policy attestation.

Any existing `-journal`, `-wal` or `-shm` path is rejected before SQLite opens. Source
identity and sidecars are checked again after snapshot establishment and before success.
These checks supplement SQLite locking; they do not constitute snapshot proof themselves.
WAL, persistent sidecar evidence, unknown modes, missing/unsafe paths, bounded-resource
failures and lock/read errors fail closed. Sidecars are never deleted or recovered.
`mode=ro` prevents writing a raced hot journal's recovery back to the source.

A RESERVED writer with no changes may coexist with a valid old committed read snapshot.
An EXCLUSIVE writer prevents inspection. A competing rollback writer can start a write
but cannot commit while the established reader holds its snapshot. If its journal is
observed, diagnostics return UNVERIFIED. D1 must not be run in a caller transaction
that requires a competing commit before the diagnostic can finish. Locks are released
on connection close; there is no waiting/retry policy or operational fencing service.

Tests cover WAL switches between preflight/open and between open/first read, hot
journals already present and raced into opening, writer contention, commit blocking,
source bytes/directory preservation, one-connection replay and transaction cleanup.
All diagnostic source writes are prohibited, including transient source-side artifacts;
post-run comparisons are evidence in addition to the preventive opening configuration.

## Validation and authority

Exact manifests are assembled from existing schema builders for:

| Version | Applicable history |
| --- | --- |
| v1 | Accounting |
| v2 | Accounting and durable orders |
| v3 | Accounting, orders and original reservations/outcomes |
| v4 | Complete transition history, original reservations, migration boundaries, releases and outcomes |

Application ID, version, every non-null schema definition, and the complete NULL-SQL
automatic-index catalog must match. SQLite physical integrity and foreign keys are
checked before business replay. Older valid versions
mark later-version checks NOT_APPLICABLE; unknown or modified schemas are not downgraded.

`VirtualLedger._events` invokes established accounting reconstruction, including cash,
shares, weighted cost and realized profit. `_history` invokes canonical order decoding
and the existing lifecycle validator. v3 uses complete reservation replay. v4 invokes
complete `_transition_replay`, which includes historical reservation associations,
original availability/denials, migration baselines, transition commands, terminal
releases and durable outcomes. Partial original reservation replay is never labeled
final v4 availability. No financial arithmetic or state machine is reimplemented.
Some granular checks repeat work inside complete replay deliberately: the complete
business validator is never bypassed for an optimization.

## Results, scope and limits

Frozen `Diagnostic` contains `local_status`, `schema_version`, an ordered tuple of frozen
`Check(name, result)` values, a fixed `issue_code`, `acknowledgment_status=UNVERIFIED`,
and `scope=WHOLE_VIRTUAL_DATABASE`. Check order is snapshot, schema, physical,
foreign_keys, ledger, orders, reservations, transitions. Check outcomes are PASS, FAIL,
NOT_RUN or NOT_APPLICABLE. Checks depending on a failed earlier check are not reported
as passed. Reports carry no financial rows, raw exceptions, credentials, source path,
wall-clock timestamp or fabricated entity/snapshot identifier.

| Status | Meaning |
| --- | --- |
| HEALTHY | Applicable local checks completed on one supported snapshot |
| UNSUPPORTED_SCHEMA | Wrong app/version or exact manifest mismatch |
| CORRUPT | SQLite physical/NOTADB corruption or failed physical integrity |
| INCONSISTENT | FK violation, invalid canonical record or business-history inconsistency |
| UNVERIFIED | Unsafe/unavailable source, mode, lock, resource or observation failure |
| INCOMPLETE_EVIDENCE | Reserved vocabulary; no external evidence mechanism is implemented |

The interface is internal and whole-database scoped, with no public endpoint, portfolio
listing or inferred per-portfolio authorization. It validates every portfolio and cannot
silently omit another portfolio's corruption. It does not disclose guessed entity IDs
when the complete replay cannot safely attribute a failure.

Local HEALTHY does not prove all acknowledged operations survived, absence of coherent
historical truncation, backup currency, power-loss durability, authentication, trusted
prices or Beta readiness. Without independently approved receipts, acknowledgment
completeness is always UNVERIFIED. No RPO/RTO or recovery capability is established.

L1 remains: unrecorded failed lifecycle attempts are not durable denials. L2 remains:
no new v4 reservation admission. L3 remains: full Python business reconciliation is
required beyond SQL constraints. There is no repair, backup/restore, receipt journal,
outbox, operator fencing, admission, settlement, deployment or authentication work.

## Synthetic coverage and review

The 30 initial test purposes were retained and adapted to SQLite ownership. In particular,
the obsolete in-memory-only connection assertion now checks the exact source URI and
read-only flags. The old BEGIN IMMEDIATE rejection test now uses BEGIN EXCLUSIVE;
a separate test demonstrates the valid unchanged RESERVED-reader case. No existing
tracked test or security assertion was changed or skipped.

There are 183 D1 tests, including 33 R2 manifest tests, 26 R3 automatic-index tests
and 55 R4 explicit-identity tests.
Coverage maps to the original acceptance areas as follows:

| Acceptance areas | Evidence |
| --- | --- |
| 1–8, 41, 43 | v1–v4, version/app/manifest/extra-object/trigger and missing-path tests |
| 9–16, 46, 62, 64 | Exact read-only URI, blocked mutations, no creation/migration, byte/directory comparisons, source-API inspection and failure cleanup |
| 17–24 | Existing replay observed with explicit cash/share/book-cost/profit/available-resource assertions; pending orders do not post resources |
| 25–30 | Duplicate insert constraints, missing reservation/release/terminal, amount corruption, complete cancellation/expiration replay |
| 31–40, 42, 44–45 | Other-portfolio corruption, wrong instruments, conflicting revisions/chronology, forged FILLED JSON, canonical/Decimal corruption, FK errors and migration-boundary checks |
| 47–50 | Repeated reports, fixed check order, immutable values, no authority fields, acknowledgment UNVERIFIED |
| 51–53 | Failed command creates no outcome, v4 admission remains rejected, complete v4 replay injection and nonterminal obligations |
| 54–60 | Owner-path rejection before open, unchanged import policy, no worker/copy/writer APIs, guarded credential/network/broker boundaries; internal API only |
| 61, 63 | Concurrent commit blocking, exclusive contention, opening races, hot journals, WAL/sidecar/unknown-mode/resource rejection |
| 65 | Full unchanged accounting/order/reservation/transition focused suites |

Duplicate evidence cannot be inserted under the exact committed unique constraints;
tests assert rejection and unchanged valid history, not successful insertion of impossible
rows. Semantic-corruption tests explicitly assert SQL integrity and FK checks pass before
requiring INCONSISTENT. Missing evidence cases do not substitute zero balances.

Simplification retained existing manifests and business validators rather than adding
parallel algorithms. Removed the native lock, process worker and source-image machinery;
kept only the path/snapshot checks and immutable diagnostic model. No general snapshot
framework, dependency or production-interface extension was introduced.

## Historical R1 validation record

The following results predate R2 and are not verification of the correction:

| Check | Result |
| --- | --- |
| D1 permanent tests | 69 passing tests included in focused/full runs |
| Focused accounting/order/reservation/transition/D1 | 360 passed; 114 subtests |
| Full pytest | 1,795 passed; 1,197 subtests |
| Full unittest discovery | 1,795 passed |
| Unrestricted pytest (`python_files=*.py`, repository root) | 1,795 passed; 1,197 subtests |
| Python syntax | 170 files |
| MAGI imports | 125 modules |
| Dependency pins and pip check | Passed |
| Original import allowlist | Passed unchanged |
| Changed-file whitespace and secret-pattern review | Passed |

The original 1,726-test baseline plus 69 D1 tests accounts for the final total. An
existing Google SDK/Pydantic deprecation warning remains. Full-file whitespace results
are qualified below. All runs use synthetic temporary databases. Temporary guards start with an empty credential environment,
disable dotenv/import-time IPv6 capability probing, block real socket/DNS/send and HTTP
transports, deny credential/owner DB files, restrict SQLite to test paths, and propagate
the same restrictions into synthetic Python fault-injection children. The owner
secret-validator path is redirected to an absent temporary file; fixture-specific test
patches still run. Existing Toss/broker construction is allowed only with explicit
synthetic credentials and MockTransport. D1 constructs no such adapters. Each full suite constructs 121 fixture-only Toss
sessions and 48 fixture-only broker adapters. Across both full regression rounds,
726 mock sessions and 288 mock adapters were counted; these are not real broker
initializations. Guard counters recorded zero network attempts/transmissions,
credential-file accesses and owner/production database accesses, with no violation
records. No live calls or actual broker operations occurred. Temporary harness files
are removed during final cleanup, and only the workflow-owned caffeinate is terminated.

A whole-repository pattern scan found only eight existing synthetic rejection-test
strings, none in D1. Full-file whitespace inspection found one pre-existing trailing
space at `magi/melchior.py:33`; it remains unchanged because R1 permits only three draft
files. Changed-file whitespace and `git diff --check` pass. Historical warnings must
not be presented as new D1 failures or silently edited outside scope.

Future README maintenance should reflect committed Packages B/C and D design, then D1's
reviewed status. README remains unchanged here. D2 remains separately authorized future
synthetic backup/manifest work; this milestone does not implement or begin it.

## Historical R2 correction and independent re-audit handoff

D1-A01 was independently confirmed HIGH: a dictionary keyed only by object name
collapsed a table/view/index sharing an expected trigger name. SQLite integrity and
FK checks passed while the diagnostic incorrectly returned HEALTHY. R2 reproduced
that result before editing. The prior independent audit remains FAILED; these
implementation tests do not constitute independent approval.

At R2, the source query was `SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL`.
The correction sorted a list of every `(name, sql)` tuple and compared it to sorted
existing manifest items. No source row is deduplicated: even repeated identical tuples
remain separate entries. SQL strings are compared verbatim under the existing policy;
creation/enumeration order is irrelevant. Expected manifests remain unchanged.
This is a lossless comparison of the existing policy's fields, not a new generic
schema format. Object type and owning-table changes in CREATE definitions remain
exact SQL mismatches; raw sqlite_schema metadata has not gained a new independent
validation contract.

At R2, existing NULL-SQL exclusions were preserved for SQLite-generated automatic indexes,
whose constraint definitions are represented in table SQL. No name-prefix exclusion
is added. Explicit tables, indexes, triggers and views have non-NULL definitions and
cannot disappear through a name collision.

Permanent `ManifestCollisionTests` covers 24 cases: table/view/index collisions across
v1–v4, with the unauthorized object created both before and after the expected trigger.
Every fixture independently queries both objects and the original trigger's exact SQL,
and asserts SQL integrity/FK validity before requiring UNSUPPORTED_SCHEMA. Four tests
recreate legitimate triggers in reverse name order and require HEALTHY. Five more
cover unique extras, missing/modified objects, wrong type and wrong owning table.
All existing 69 D1 tests remain. Source/directory preservation and repeated deterministic
reports are asserted by the shared D1 inspection helper.

Only schema comparison changed in production. The read-only connection, snapshot,
complete financial replay, classifications, immutable reports and L1/L2/L3 are unchanged.
No dependency, import, schema, migration or write-path change was made.

### Writer-blocking operational restriction

D1 is an internal diagnostic, NOT approved for production concurrent-writer operation.
Its read transaction can delay writer commits for the full diagnostic duration.
`timeout=0` bounds SQLite lock-acquisition waiting, not Python reconciliation or total
execution time. No complete execution-time budget is enforced. A paused diagnostic
retains its lock; no naturally occurring infinite hang was demonstrated by the audit.
D1 cannot guarantee nonblocking behavior for live trading writers.

Safe scheduling, workload bounds, operator quiescence and/or a separately approved
cancellation mechanism remain future operational gates. Documentation does not enforce
quiescence. Network filesystems, custom VFS behavior, noncooperating writers and arbitrary
platforms remain unsupported/unverified as described above. A contract requiring a
hard total-runtime bound remains blocked until a mechanism is separately approved.
No watchdog, thread/process cancellation, native lock, backup or restore was added.

Re-audit priorities: independently reconstruct the six original counterexamples;
verify both creation orders and row multiplicity; confirm valid v1–v4 schemas and
internal-index handling; inspect unchanged snapshot/replay ownership and import policy;
and assess the explicitly restricted internal-only scheduling contract. Do not infer
Beta readiness, acknowledgment completeness, production approval or a passed independent
audit from this correction. Nothing is staged, committed or pushed.

### R2 final validation (corrective implementation, not independent approval)

- Original false HEALTHY reproduced before correction on a temporary v1 fixture.
- New manifest tests: 33 passed; total D1 tests: 102.
- Focused suites: 393 passed, 114 subtests; original import-policy test unchanged.
- Final full pytest: 1,828 passed, 1,197 subtests.
- Final unittest discovery: 1,828 passed.
- Final unrestricted discovery: 1,828 passed, 1,197 subtests.
- Syntax: 170 files; imports: 125 MAGI modules; dependency pins/pip check passed.
- Changed-file whitespace and tracked/untracked secret-pattern scan passed; the eight
  existing synthetic security-fixture matches remain unchanged. No new secret matches.
- Existing Google SDK deprecation warning and baseline whitespace limitation remain.
- Guard counters: zero network attempts/transmissions, real credential reads and
  production DB accesses. Each full suite used 121 synthetic MockTransport Toss sessions
  and 48 mock broker adapters; D1/focused tests constructed none. Across the two full
  rounds: 726 mock sessions and 288 mock adapters, zero real broker operations.

The temporary harness used an empty credential environment, disabled dotenv and the
import-time IPv6 probe, redirected owner secret validation to an absent temporary path,
blocked real socket/HTTP transports and credential files, restricted SQLite to temporary
fixtures, and propagated guards to existing synthetic fault-injection children. No
security test was excluded. The wrong-owner fixture was strengthened during review to
preserve exact trigger SQL except its owning table; focused/full regression was rerun.

At R2 completion, HEAD and staging remained unchanged. Only the three D1 draft files changed. Cleanup removed
the temporary harness and stopped only workflow-owned caffeinate. Status: READY FOR
INDEPENDENT RE-AUDIT, not approved for commit or production use.

## Historical R3 D1-A02 correction and independent re-audit handoff

The second independent audit verified D1-A01 as RESOLVED, but blocked approval on
D1-A02 (MEDIUM): filtering out every `sql IS NULL` catalog row hid duplicate or
misattributed automatic-index records. This was catalog tampering through synthetic
`PRAGMA writable_schema` fixtures, not a demonstrated financial corruption or source
mutation by diagnostics. R3 remains corrective implementation, not independent approval.

### Reproduction and correction

Before editing, both original mechanisms were reproduced on temporary v1–v4 databases:

```sql
-- Separate synthetic fixtures only; close and reopen before inspection.
PRAGMA writable_schema=ON;
INSERT INTO sqlite_master
SELECT * FROM sqlite_master WHERE name='sqlite_autoindex_virtual_portfolios_1';
PRAGMA writable_schema=OFF;
```

```sql
PRAGMA writable_schema=ON;
UPDATE sqlite_master SET tbl_name='virtual_events'
WHERE name='sqlite_autoindex_virtual_portfolios_1';
PRAGMA writable_schema=OFF;
```

Raw catalog queries confirmed the altered rows. The explicit-SQL manifest was unchanged,
`integrity_check` returned `ok`, foreign-key checks returned no violations, and old D1
returned HEALTHY in all eight baseline reproductions. The same two alterations on
populated v4 histories are now permanent tests, covering all ten reported audit failures.

The production query now reads `type, name, tbl_name, sql` for **every** catalog row.
One pass partitions rows into two lists without discarding or deduplicating any row:

- Non-NULL SQL: sorted `(name, sql)` tuples still compare exactly against the existing
  committed manifests. D1-A01's duplicate-name and row-multiplicity protection remains.
- NULL SQL: sorted `(type, name, tbl_name)` tuples must exactly match the trusted
  automatic-index manifest. SQL nullness is enforced by the partition itself. Every
  expected row has type `index`, an exact name and exact owning table, once only.

Unexpected, duplicated, missing or misattributed rows return
`UNSUPPORTED_SCHEMA / SCHEMA_MISMATCH` when SQLite can expose the catalog. Malformed
catalogs rejected by SQLite loading remain `CORRUPT / SQLITE_CORRUPTION`. Neither path
reaches business replay or reports HEALTHY. There is no prefix exemption, set comparison,
name-keyed source dictionary, dynamic acceptance through `index_list`, or automatic repair.

### Trusted automatic-index metadata

The immutable `_AUTOINDEXES` tuple records each committed table's introduction version
and number of distinct indexed PRIMARY KEY/UNIQUE constraints. Those constraints are
defined in the unchanged `schema.sql`, `order_schema.sql`, `reservation_schema.sql` and
`transition_schema.sql`. The fixed schemas use ordinary rowid tables with non-INTEGER
primary keys; their automatic-index names are `sqlite_autoindex_<table>_<ordinal>` with
ordinals 1 through the recorded count. Explicit `virtual_one_funding` retains its SQL
and belongs only to the explicit manifest.

| Introduced | Table | Automatic indexes |
| --- | --- | ---: |
| v1 | virtual_portfolios | 2 |
| v1 | virtual_instruments | 3 |
| v1 | virtual_events | 4 |
| v2 | virtual_orders | 3 |
| v2 | virtual_order_commands | 4 |
| v2 | virtual_order_events | 3 |
| v3 | virtual_reservation_operations | 4 |
| v3 | virtual_order_reservations | 5 |
| v4 | virtual_transition_baselines | 1 |
| v4 | virtual_transition_outcomes | 4 |
| v4 | virtual_reservation_releases | 4 |

Expected totals are **9 / 19 / 28 / 37** for v1 / v2 / v3 / v4. This small static manifest
was selected instead of a general SQL parser or an extra connection executing canonical
DDL at diagnostic runtime. It does not learn expected metadata from the inspected
database. Legitimate fixtures created from the committed schemas and migrations verify
the manifest independently of the diagnostic's constants. No committed schema currently
needs another NULL-SQL internal-object category, `sqlite_sequence`, or statistics table.

Root-page numbers are deliberately excluded: file layout is not structural identity.
The supported/tested runtime remains Python 3.12.14 / SQLite 3.53.1. Different runtimes
are not granted speculative compatibility: if they produce a different catalog, exact
comparison fails closed. Future schema changes require explicit manifest review and
legitimate-schema tests; no runtime-generated catalog is accepted as its own authority.

### Permanent regressions and simplification

The 26 new tests cover normal automatic indexes, duplicate rows, wrong owners and wrong
types across v1–v4; both original alterations in populated v4 histories; three identical
catalog rows; missing/renamed/extra indexes; unexpected NULL-SQL tables; moving explicit
indexes into the NULL-SQL partition and automatic indexes out of it; and explicit-SQL
collisions under reversed enumeration. Valid and altered automatic catalogs are checked
with normal and reversed row enumeration and repeated deterministic reports.

Every adversarial fixture closes its writer, reopens, and compares the complete raw
catalog with the intended changed rows including identity/type/owner/NULL status and
multiplicity. A fixture-only `writable_schema=ON` inspection exposes rows even where
ordinary SQLite schema loading rejects them; no catalog write occurs in that inspection.
Separate ordinary connections assert the actual integrity/FK results. Missing automatic
indexes produce a physical-check corruption result; certain malformed names/SQL
are rejected during SQLite loading. Construction failure is never counted as detection.
In particular, changed index type to `trigger` still passes ordinary integrity/FK checks
on the tested runtime, and the new catalog comparison rejects it across all versions.

All existing tests remain, including D1-A01's 33 manifest cases, legitimate reordered
triggers, missing/modified explicit objects, source-byte/directory preservation, single
connection/transaction ownership, and the original import-policy test without alteration.
New D1 tests construct no Toss sessions or broker adapters.

Simplification review retained one catalog query, two lossless lists, and one small
immutable versioned manifest. No SQL parser, second database connection, duplicated
financial algorithm, rootpage constants, mutable cache, fallback, dependency, import or
unrelated refactor was added. Focused regression was rerun after this review.

### R3 validation and preserved boundaries

| Check | R3 result |
| --- | --- |
| New automatic-index tests | 26 passed, included below |
| Complete D1 module | 128 passed |
| Focused virtual-system regression | 419 passed; 126 subtests |
| Full pytest | 1,854 passed; 1,209 subtests |
| Full unittest discovery | 1,854 passed |
| Unrestricted pytest (`python_files=*.py`, repository root) | 1,854 passed; 1,209 subtests |
| Python syntax | 170 files passed |
| MAGI imports | 125 submodules plus the root package passed |
| Dependency pins / pip check | All five pins match; no broken requirements |
| Original import-policy test | Passed unchanged |

All runs use strict offline guards, temporary synthetic databases, an empty credential
environment, disabled dotenv/import-time IPv6 probing, blocked real socket/HTTP traffic,
denied real credential files, restricted SQLite paths and propagated subprocess guards.
Owner-secret validation points at an absent temporary file while existing fixture-level
patches remain effective. Only existing full-suite tests may construct Toss/broker
objects, using explicit synthetic credentials and in-memory MockTransport. These are
reported separately from real broker initialization. No security assertion is weakened.
Each full suite constructed 121 fixture-only Toss sessions and 48 mock broker adapters;
the three full runs totaled 363 sessions and 144 adapters. D1/focused runs constructed
none. Guard counters recorded zero real network attempts/transmissions, credential
reads, production DB accesses, real broker initializations and test repository writes.
No actual brokerage operations occurred. A new fixture helper initially asserted that
the explicit manifest was unchanged even when intentionally moving an explicit index
to NULL SQL; that test-only assertion was corrected before the passing full runs.

Tracked/untracked secret-pattern review found only existing synthetic rejection strings
(eight URL-fixture lines and two synthetic key-shape lines), none in the D1 changes.
Changed-file whitespace and `git diff --check` pass. The pre-existing trailing space in
`magi/melchior.py:33` remains outside authorized scope. The existing Google SDK/Pydantic
deprecation warning remains. No tracked file, dependency, schema, configuration, README,
original import-policy test or `.env` changes are authorized or included.

The source still uses its one read-only connection and snapshot for complete existing
Python reconciliation. Report classifications, local HEALTHY scope, acknowledgment
UNVERIFIED, and L1/L2/L3 are unchanged. No authentication, recovery authorization, repair,
backup/restore, admission, settlement or Package D2 work is included.

The writer-blocking restriction above remains mandatory: internal/offline or quiescent
use only; no production concurrent-writer approval, enforced total-runtime budget or
guaranteed writer-blocking duration. Documentation does not enforce quiescence. Network
filesystems, custom VFS, noncooperating writers and arbitrary platform behavior remain
unsupported/unverified. This correction does not approve Beta deployment.

Independent re-audit priorities are the ten original false-HEALTHY cases, completeness
and provenance of the fixed expected catalog, duplicate/null/type/owner handling, row
ordering, all valid versions, preserved D1-A01 behavior, and unchanged source/snapshot/
replay/import boundaries. The reviewer must verify these separately. Only the three D1
draft files change; HEAD and staging remain unchanged; no commit or push is performed.
Temporary harness artifacts and the workflow-owned caffeinate process are removed at
completion. R3 status: **READY FOR INDEPENDENT RE-AUDIT**, not audit or commit approval.

## R4 D1-A03 correction and re-audit handoff

The independent R3 audit resolved D1-A01 and D1-A02 but found D1-A03 (MEDIUM):
explicit objects were reduced to `(name, sql)`, discarding their raw type and owner.
Appending `char(0)||'unexpected'` to `type` or `tbl_name` through fixture-only
`writable_schema` preserved name/SQL and passed SQLite integrity/FK checks, but D1
incorrectly returned HEALTHY. Before R4 editing, both mechanisms were reproduced on
fresh v1 fixtures; reopened catalog BLOB/hex reads confirmed the embedded NUL bytes.
No financial corruption or source mutation by D1 was demonstrated.

R4 retains every explicit row as `(type, name, tbl_name, sql)`. `_explicit_catalog`
enriches the existing trusted committed manifest with type and ownership, accepting
only its current CREATE TABLE, CREATE UNIQUE INDEX and CREATE TRIGGER BEFORE headers.
Table ownership is the table name; index/trigger ownership comes from the trusted
header's ON clause and must refer to a table in that same trusted manifest. This is a
narrow extractor for the committed schema forms, not a general SQL parser and never
a parser for inspected source SQL. Unknown trusted forms fail closed. No expected
identity is learned from source PRAGMAs or source catalog metadata.

Sorted complete explicit tuples now compare exactly, including SQL. NUL-bearing
metadata, uppercase aliases and other mismatches cannot match canonical expected
values. No trimming, case folding or NUL stripping is performed. Malformed source
catalogs rejected by SQLite remain CORRUPT; observable mismatches return
UNSUPPORTED_SCHEMA / SCHEMA_MISMATCH. The NULL-SQL path and its independently checked
9/19/28/37 automatic-index totals are unchanged. Both lists preserve duplicates.
No root-page constants, extra source connection, schema write, import or dependency
was introduced.

The 55 new permanent tests include all 26 reported failures: 24 NUL cases covering
explicit tables/indexes/triggers, both identity fields, and v1–v4, plus both populated
v4 cases. A further 24 tests reject unsupported non-NUL uppercase identities. Four
canonical tests construct databases directly from committed SQL and compare the full
catalog to the enriched manifest. One test checks unsupported trusted headers fail
closed. Alteration tests reopen the database, verify complete catalog multiplicity,
unchanged name/SQL and byte-exact metadata, assert real integrity/FK outcomes, compare
normal/reversed enumeration and repeated reports, and verify all financial records
remain unchanged. Existing A01/A02, reordered-trigger, snapshot, reconciliation and
original import-policy tests remain unchanged.

Simplification review retained one catalog query and the existing two-list design.
The only new helper reads the three known trusted header forms; there is no second
schema framework, mutable cache, permissive fallback or duplicated accounting logic.
The complete existing Python replay, one read-only source connection/transaction,
query_only enforcement, source restrictions and cleanup remain unchanged.

HEALTHY remains local-only; acknowledgment remains UNVERIFIED. L1 (missing durable
failed-command outcomes), L2 (no new v4 reservation admission), and L3 (mandatory full
Python reconciliation) remain unresolved. D1 remains internal/offline or quiescent:
writer commits may be delayed, timeout=0 bounds lock waiting only, and no whole-run
budget or enforced quiescence exists. Production concurrent-writer operation, other
SQLite/platform/filesystem configurations and Beta deployment are not approved.
There is no repair, backup/restore, authentication, admission, settlement or D2 work.

Independent re-audit should reproduce all 26 failures, challenge non-NUL aliases and
full raw identity representation, derive canonical expected metadata independently,
and revisit duplicate rows, A01/A02, supported versions, populated histories, and
snapshot/replay/import boundaries. This corrective implementation is not independent
audit approval. Only the three D1 drafts change; no staging, commit or push is authorized.

### R4 final validation

| Check | R4 result |
| --- | --- |
| New explicit-identity cases | 55 passed, including all 26 reported failures |
| Complete D1 module | 183 passed; 16 subtests |
| Focused virtual regression | 474 passed; 130 subtests |
| Full pytest | 1,909 passed; 1,213 subtests |
| Full unittest discovery | 1,909 passed |
| Unrestricted pytest (`python_files=*.py`, repository root) | 1,909 passed; 1,213 subtests |
| Python syntax | 170 files passed |
| MAGI imports | 125 submodules plus the root package passed |
| Dependencies | All five pins match; pip check reports no broken requirements |
| Original import-policy and broker-isolation tests | Passed unchanged |

The tested environment is macOS arm64, Python 3.12.14, SQLite 3.53.1. Focused tests
were repeated after the small trusted-header bounds check; full regression ran on
the final implementation. Final simplification review required no further code changes.

Temporary offline harness adaptations used an empty credential environment, disabled
dotenv and the SDK's import-time IPv6 probe, redirected owner secret validation to an
absent temporary path, blocked real socket/DNS/HTTP transports and real credential
files, restricted SQLite to owned temporary fixtures, and propagated guards to the
existing Python fault-injection children. These adaptations did not change repository
tests or their security assertions. Guard counters recorded zero real network attempts
or transmissions, real credential reads, production DB accesses, real broker
initializations, or test repository writes. Existing full-suite fixture tests constructed
363 synthetic Toss sessions and 144 MockTransport broker adapters across the three
full runs; these are test-only objects, not real broker initialization. D1/focused runs
constructed none. No actual broker operation or live provider request occurred.

Secret-pattern review of tracked/untracked files found only unchanged synthetic
rejection fixtures (nine URL-fixture lines and two key-shape lines), with no matches
in the three drafts. Changed-file whitespace and git diff checks pass. The unchanged
baseline trailing space at `magi/melchior.py:33` and existing Google SDK/Pydantic
deprecation warning remain outside this correction. No schema, dependency, original
test, configuration, README or other tracked file changed; all 254 tracked file hashes
match the starting state. The real `.env` remains ignored, unread and unchanged by
file identity/size/modification metadata. Repository inventory contains no production
database, and guarded tests use temporary databases only.

The workflow-owned caffeinate assertion is verified during work and removed during
cleanup, along with only this run's temporary harness, logs and synthetic fixtures.
The three D1 drafts remain untracked, staging remains empty, and HEAD remains the
baseline. No commit or push occurs. Next action is independent re-audit of D1-A03 and
preserved A01/A02 boundaries, not Package D2 or deployment approval.
