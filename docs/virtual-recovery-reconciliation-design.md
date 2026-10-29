# Phase 8.3d.4a — Virtual recovery, reconciliation and acknowledgment integrity design

## 1. Executive summary and status

**PROPOSED — design for review only.** Baseline: `main`, commit
`5a4361b0e8c51a489bef2757ad28a2156c13c8e3` (Package C). This document adds no
runtime, schema, test, backup, restore, migration, admission or settlement capability.
Project MAGI remains the internal name; MOIRAI remains a proposed public brand.

IMPLEMENTED means directly supported by current source. PROPOSED means a future
reviewable contract. CONDITIONAL means dependent on separately approved authority,
metadata, infrastructure or implementation. UNVERIFIED means evidence is absent.
A healthy internally consistent snapshot does not prove complete financial history,
actual caller receipt, trusted execution, or readiness to resume operation.

Recommend D1 read-only diagnostics first. D2 can validate synthetic backups; D3 can
restore into isolated staging. Activation must wait for D4's approved acknowledgment
and fencing model and D5's independent review. No component may invent lost history.

## 2. Current architecture and review basis

IMPLEMENTED: isolated USD accounting v1; immutable order/command/event persistence
v2; reservation creation and durable reservation outcomes v3; atomic lifecycle,
release and successful command outcomes v4. There is no recovery service, backup
manifest, database lineage UUID, recovery generation, acknowledgment journal, global
commit sequence or deployed writer fence. Application ID `1297503281` identifies a
format, not an individual database. A filename or inode is not durable lineage.

Reviewed architecture sources: [Beta design](beta-paper-trading-architecture.md),
[accounting](virtual-accounting-core.md), [price policy](virtual-execution-price-policy.md),
[execution contracts](virtual-execution-contracts.md), [lifecycle](virtual-order-lifecycle.md),
[reservations design](virtual-order-reservations-design.md), [persistence](virtual-order-persistence.md),
[reservations](virtual-order-reservations.md), and [Package C](virtual-order-atomic-transitions.md).
Older proposed scope and recovery targets are not evidence of implementation or approval.

Source anchors under `magi/virtual/`: `accounting.reconstruct`; `storage.VirtualLedger`
path/connection/event validation; `order_lifecycle` pure graph; `order_codec` strict
codecs and `reconstruct_order`; `order_storage._Database`, `_history`,
`_validate_contents`; `reservations` canonical contracts; `reservation_storage._replay`;
`atomic_transitions` command codecs; `transition_storage._replay`,
`_transition_synthetic`, `migrate_transition_schema`; and all four SQL schemas.
These implementations, rather than proposed services in older documents, determine
current behavior. README was reviewed and remains unchanged.

## 3. Existing guarantees and evidence

IMPLEMENTED: writes use one connection, foreign keys enabled, `BEGIN IMMEDIATE`,
exact schema verification, one outer commit and connection cleanup. Closing an
uncommitted connection rolls back non-SQL as well as SQL failures. Reads use
`mode=ro`, `query_only` and a transaction. v4 validates complete history before
mutation and again before commit. Successful canonical retries resolve before stale
revision checks. Reopening reconstructs state without an in-memory command cache.

Existing relevant suites: `test_virtual_accounting.py`,
`test_virtual_execution_contracts.py`, `test_virtual_order_lifecycle.py`,
`test_virtual_order_persistence.py`, `test_virtual_reservations.py` and
`test_virtual_atomic_transitions.py`. They cover reconstruction, immutable records,
foreign-key links, corrupt histories, migration rollback, competing connections and
processes, lock contention, fresh-process retry and lost-response behavior.
Package C specifically tests faults after command/event/release/outcome writes,
commit failure, process exits before/after commit, uncommitted-read isolation,
missing releases/outcomes, wrong scope, preserved legacy histories and exact v4 repeats.

Historical validation: developer full pytest and unrestricted discovery each
1,726 tests / 1,197 subtests; unittest 1,726. Prior independent audit: 408 focused
tests / 219 subtests and 22 adversarial checks. These were not rerun in this design
phase. Process-exit and injected exceptions do not simulate hardware power failure.

## 4. Recovery objectives and missing evidence

All future operational services in this table are PROPOSED. No RPO/RTO is approved.

| Objective | Existing state can establish | New metadata required | Independent evidence / current limit |
| --- | --- | --- | --- |
| A Process restart | Reconstructed committed histories and canonical outcomes | Recovery report and runtime profile | Cannot prove receipt or resume authority |
| B Consistency verification | Structure, FKs and business replay on one snapshot | Versioned diagnostic/state-reference format | Coherent historical truncation may remain undetectable |
| C Backup creation | No current backup operation | Identity, completion and snapshot manifest | Storage durability and source identity need evidence |
| D Backup validation | Existing validators can be reused internally | Artifact digest and validator report | Authentic origin needs a trusted registry/attestation |
| E Restore | No current restore operation | Incident, staging and activation records | Eligible backup and fenced writers must be established |
| F Acknowledged work | Successful outcomes present in this snapshot | Commit/outbox identity and receipt linkage | Independent complete receipt inventory is essential |
| G Corruption | Existing structural and semantic failures | Stable reason codes and scope references | Malicious coherent rewrites need external anchors |
| H Migration recovery | Exact version/manifests, replay, atomic migration | External migration intent/result record | Current version is not proof of migration chronology |
| I Operator fencing | Path/inode guards and SQLite write serialization | Generation/epoch and activation protocol | No current authorization, distributed fence or lease |
| J Incident diagnostics | Fixed errors and local state evidence | Restricted incident/report identifiers | No existing incident system or escalation guarantee |

## 5. Source-of-truth hierarchy

| Quantity or identity | Authority within a validated snapshot |
| --- | --- |
| Posted cash, positions, book cost, realized P&L | `virtual_events` replay through unchanged accounting rules; instruments and portfolio scope validated |
| Original intent and accepted session/policy | Canonical `virtual_orders.initial_json`, initial event and submission command cross-checked together |
| Order commands | `virtual_order_commands`, checked against all linked events and result revisions |
| State history/current state | Immutable `virtual_order_events`, contiguous legal replay; latest valid event, never a guessed timestamp |
| Reservation creation | `virtual_reservation_operations` canonical outcomes and matching `virtual_order_reservations` effects |
| Releases | `virtual_reservation_releases` matched exactly to valid terminal events and successful transition outcomes |
| Available cash/shares | Derived from posted ledger minus reconciled active obligations |
| Durable lifecycle result | `virtual_transition_outcomes` canonical request and validated effects |
| Migration version | Application ID, `user_version`, exact schema objects and validated content together |
| Migration boundary | `virtual_transition_baselines`, validated against complete original and subsequent history |

No single redundant row overrides another inconsistent record. Original reservation
rows marked ACTIVE describe creation effects; they are not v4's current active set.
A conflict makes the snapshot inconsistent. A balance, screenshot, client assertion,
backup filename or hash cannot supply missing authoritative events. Future projections
must be reproducible and cannot replace missing history.

## 6. Read-only reconciliation and L3

PROPOSED D1 owns one verified read transaction and calls the version-appropriate
complete validators on that same connection. Do not combine separate `state()` calls
from different snapshots. Existing private helpers need a narrowly reviewed diagnostic
boundary; there is no supported generic diagnostics API today. No raw-SQL shortcut
may replace business replay. No schema repair, migration or balance rewrite is allowed.

Validation order: bounded file/schema recognition; application/version and exact
manifest; SQLite integrity and foreign keys; all portfolio/instrument identities;
ledger reconstruction; complete order history; reservation operation/effect replay;
v4 boundary/outcome/release reconciliation; derived conservation. Verify:

- Contiguous ledger sequences, funding rules, exact Decimal values, unique event/key/fill
  associations, nonnegative posted resources and accounting conservation.
- Unchanged intent/session/policy, initial ACCEPTED history, command kinds, contiguous
  revisions, previous-state and recorded/effective chronology, pure permitted edges.
- Exact original reservation actor/key/command/event/instrument/portfolio associations;
  denied operations remain denied under their original creation ordering.
- One corresponding outcome per post-boundary event, exact release amount/identity,
  no missing/orphan/duplicate effect and no terminal resurrection or FILLED shortcut.
- Reserved migrated orders have baseline 1; legacy unreserved histories still validate
  in full; markers cannot conceal missing initial history.
- `available_cash = posted_cash - active_BUY_caps >= 0`; for each instrument,
  `available_shares = posted_shares - active_SELL_quantities >= 0`.

**L3:** SQL-valid state can violate business invariants. Full Python reconciliation
remains mandatory for diagnostic success, backup eligibility and restore validation.
A failed prerequisite makes dependent checks NOT_RUN, never implicitly passed.
Default hold scope is the entire database: current v4 replay is global. Narrowing a
hold to a portfolio needs separate proof that cross-scope corruption is contained.

## 7. Diagnostic contract

PROPOSED immutable `RecoveryDiagnostic` conceptual record: format version, validator
policy and runtime versions, explicit schema/app ID, snapshot reference, per-check
status/reason codes, sorted findings, bounded affected internal IDs, evidence references,
coverage and acknowledgment-verification status. No `safe` or `ready_to_trade` flag.

| Classification | Meaning |
| --- | --- |
| HEALTHY | All supported local structural and semantic checks passed for this snapshot only |
| INCONSISTENT | Readable recognized state violates a cross-row/business invariant |
| CORRUPT | Physical integrity or canonical record decoding fails |
| UNSUPPORTED_SCHEMA | Unknown version, wrong app ID or nonmatching exact manifest; do not migrate or guess |
| INCOMPLETE_EVIDENCE | Required receipt/manifest/lineage evidence is missing or its coverage is incomplete |
| UNVERIFIED | Verification could not finish, e.g. lock timeout, I/O failure or resource limit |

Retain all findings with fixed precedence for a primary summary: UNSUPPORTED_SCHEMA,
CORRUPT, INCONSISTENT, INCOMPLETE_EVIDENCE, UNVERIFIED, HEALTHY. Per-check status is
PASS/FAIL/NOT_RUN. Local integrity and acknowledgment completeness are separate axes:
local HEALTHY plus acknowledgment INCOMPLETE_EVIDENCE must not authorize activation.
Do not turn an unreadable file into HEALTHY or assert corruption merely from busy I/O.

Latest validated state reference is absent until full replay passes. Proposed reference:
versioned canonical digest of exact schema and every business record, ordered by
explicit table/key with typed, length-delimited fields and no float conversion;
plus per-portfolio ledger/reservation sequences and per-order revisions. This is not
a current global transaction ID. Counts/maxima alone cannot identify a snapshot.
Define serialization and test vectors in D1 before interoperability claims.

For identical snapshot, validator policy and evidence inputs, output findings and
state reference must be identical. Separate run time, duration, incident ID and host
telemetry from the deterministic result. Bound output without hiding failed checks:
include total counts and an explicit truncation marker. Restrict scope IDs and record
digests to authorized incident views; public logs get fixed codes and opaque report IDs.
Never emit raw SQLite errors, requests, financial rows, secrets or absolute user paths.

## 8. SQLite configuration and durability

IMPLEMENTED connection facts: explicit read-only/read-write URI, bounded timeout
(default five seconds), FKs ON per connection, query-only reads, `BEGIN` for reads,
`BEGIN IMMEDIATE` for writes, exact versioned manifests, sole outer commit. The code
sets neither `journal_mode` nor `synchronous`, WAL checkpoint policy, `fullfsync`, or
`checkpoint_fullfsync`. Exact SQL manifests do not certify those runtime settings.

UNVERIFIED: effective journal/synchronous settings of any real file, SQLite build,
VFS, filesystem and storage device. No actual database was opened to infer them.
Do not advertise a pinned DELETE/FULL profile or WAL operation based on defaults.
A future runtime must record effective PRAGMAs and compile/runtime versions and reject
unapproved profiles; this is a separate implementation and operational decision.

| Property | Evidence and boundary |
| --- | --- |
| Transaction atomicity | Existing all-or-none command tests and SQLite transaction protocol |
| Process crash recovery | Existing process-exit/reopen tests; must extend to approved journal modes |
| OS crash / power loss | Not certified; needs runtime/VFS/fsync and representative fault testing |
| Filesystem durability | Requires ordering, file and directory sync semantics on the chosen filesystem |
| Device failure | Local transactions cannot recover a destroyed device; independent retained copy needed |
| Consistent backup | Separate snapshot protocol and verification, not a consequence of BEGIN IMMEDIATE |
| Retention/replication | Neither service exists; access, deletion and independent failure domains undecided |

Do not change sync settings for performance. No RPO/RTO is inferred from process tests.
Earlier Beta design suggested numerical targets and cadence; they remain unapproved.
Drills, workload, storage fault behavior, staffing and measured restore time must support
any future promise. A checksum or successful COMMIT cannot prove device honesty.

## 9. Backup consistency

PROPOSED D2 preference: SQLite Online Backup API into a newly allocated private,
separate destination, initially synthetic only. It copies a SQLite-consistent database
including committed content represented in WAL; do not assemble DB/WAL/SHM by arbitrary
file copies. The source may change during a stepped backup, so start time is not its
logical boundary. Reconcile the finished destination and derive its state reference
there. If an exact source reference is required, hold a proven source read snapshot
or quiesce writers with a tested protocol; never attach a separately queried live tip.

| Approach | Benefit | Limits / required proof |
| --- | --- | --- |
| Online Backup API | SQLite-managed consistent image, bounded stepped work possible | Busy/restart/starvation and source/destination lifetime must be tested; no acknowledgment completeness guarantee |
| VACUUM INTO | Compact standalone logical copy | SQL/version/profile support, destination handling, space, duration and durability behavior need verification; page layout/digest differs; not the initial recommended method |
| Controlled quiescent copy | Simple when every writer/connection is stopped and SQLite state is clean | Must prove quiescence and completed SQLite journal recovery/checkpoint; never copy just a live main file or delete sidecars |

No production method is selected by this design. Backup of a corrupt source is evidence,
not an eligible recovery artifact. Preserve a hot-journal/WAL incident set before any
SQLite recovery action; ordinary opening may trigger recovery or sidecar activity.
Perform recovery experiments only on a complete isolated working copy. Do not use
`immutable=1` to pretend a changing or journal-dependent source is immutable.

A backup remains PARTIAL until API completion, destination closure, approved file
synchronization, byte digest, complete destination replay and manifest publication all
succeed. Use an exclusive destination name, restrictive permissions and explicit
Beta-only path checks. No reuse or overwrite of the source. Space exhaustion, BUSY,
I/O failure or interruption yields UNVERIFIED/PARTIAL, never an eligible backup.
Sync artifact and manifest and their directory under an approved filesystem protocol;
atomic manifest publication is the completion marker, not a guessed timestamp.
Quarantine incomplete artifacts with bounded retention; no automatic source cleanup.

## 10. Minimal backup manifest

PROPOSED versioned canonical manifest fields:

| Field group | Semantics / provenance |
| --- | --- |
| Format and backup UUID | Declared format version and unique artifact identity |
| Environment and database lineage | Trusted provisioning registry reference; absent in current v1–v4, must not infer from app ID/path |
| Generation and parent reference | External recovery-control record, CONDITIONAL until D4 |
| App ID, schema version, schema digest | Measured from the finished candidate under exact profile |
| Started/completed timestamps | Collector observations with clock provenance; not trusted commit times |
| Snapshot boundary | Canonical state reference and scoped sequences/revisions from candidate; future global sequence only when implemented |
| Artifact bytes and digest | Measured length and cryptographic digest after final close; distinguish plaintext/ciphertext digests if encryption is adopted |
| Validation | Validator version, local result, report digest, coverage and effective SQLite profile |
| Acknowledgment anchor | Independent journal/receipt checkpoint ID, covered interval and verification status; unknown remains explicit |
| Creation method and lineage | Tool/runtime/method version and source identity evidence reference |
| Retention/access classification | Approved policy reference or explicitly UNAPPROVED; no guessed expiry |

Separate asserted metadata from verified observations. An expected source reference,
when supplied, must match the actual candidate or fail validation. Authenticate the
manifest through an independently protected registry/signature policy before real
restore; a replaceable adjacent digest file is not authenticity. Keep keys outside
backups. Manifests/logs contain no raw records, credentials or public financial data.
A perfectly matching artifact hash does not prove absence of lost acknowledged work.

## 11. Acknowledgment states and what exists

| Event | Current evidence |
| --- | --- |
| Request received / validated / transaction begun | No durable Package C attempt record; cannot prove these from absence/presence alone |
| Outcome written but uncommitted | Not a durable result; invisible to other consistent readers |
| Transaction committed | Valid linked outcome/effects in current snapshot prove inclusion, not real-world receipt time |
| Response emitted | No durable transport evidence in current DB |
| Response received by caller | Not observable from current DB |
| Client acknowledgment | Not implemented; client assertion alone is not authoritative history |
| External durable receipt | Not implemented; independent evidence is required to detect backup rollback gaps |

Existing same-request replay resolves a committed lost reply while the relevant
history survives. After restore, absence of that key is ambiguous: never automatically
retry an old command as new until receipt/history gaps are resolved. A stale backup
can be internally valid while lacking every operation acknowledged afterward.

## 12. Independent acknowledgment evidence model

CONDITIONAL preferred direction: a transaction-local outbox bound to each successful
business outcome, plus an independently durable append-only receipt journal in a
separate failure/restore domain. This requires a separately versioned schema/writer
change, not an extra table added behind v4's exact manifest. Cover all user-visible
mutations under their own semantics, not just lifecycle events. Existing records
without receipts remain historically unverified; never fabricate retroactive receipts.

Proposed protocol: commit business outcome + outbox atomically; export idempotently
using stable database-lineage/generation/operation identity and canonical request,
outcome and effect digests; obtain durable authenticated journal acceptance; only
then emit a response advertised as recoverably acknowledged. No remote call while
holding the SQLite write transaction. A local COMMIT with journal unavailable stays
committed but acknowledgment-pending; timeout must not imply rollback or cause a
compensating release. Same-key retry returns/finishes the existing result. Crash after
journal acceptance but before reply is conservatively protected, even if caller never
received it. Actual receipt remains unknown unless independently evidenced.

Journal envelope: format, lineage, generation, scoped operation/key identity, sequence,
request/result/effect digests, prior-chain reference, issuer and authenticated durable
checkpoint. Retain only necessary restricted metadata; full requests are not public
logs. Digests detect gaps/conflicts but cannot reconstruct missing commands. Recovery
requires an intact authoritative database/archive or separately approved complete
history evidence; a receipt alone never authorizes synthesizing a financial event.

| Option | Detectable evidence | Limitation |
| --- | --- | --- |
| Journal in same DB/backup | Local relation to present operations | Lost with the same rollback; cannot detect post-backup loss independently |
| Client-held authenticated receipt | Positive proof a specific protected outcome should exist | Missing clients/receipts cannot prove completeness; trust/coverage required |
| Independent append-only journal | Complete protected-outcome inventory through trusted checkpoint | Separate durability, retention, authenticity and availability must be proven |
| Monotonic sequence / maximum | Ordering and potential gaps within known scope | Current per-order/portfolio sequences are not global; maximum alone misses interior holes/conflicts |
| Hash chain and external anchor | Tamper/truncation detection against a known retained prefix | No protection if anchor is equally rolled back or omitted; does not restore content |

At incident fencing, obtain an independent final receipt watermark/checkpoint and
account for all in-flight exports. Verify journal completeness/continuity, membership
and matching digests for every protected outcome through that checkpoint, not merely
`restored_max >= acknowledged_max`. No complete independent evidence means
INCOMPLETE_EVIDENCE and activation held. Same identity/different digest is INCONSISTENT;
missing protected operation is an explicit ACKNOWLEDGED_OPERATION_GAP. Current database
state cannot distinguish an old valid snapshot from one that never had later work.
Local-only committed outcomes without external acknowledgment may also have been lost;
report this residual uncertainty rather than claiming universal loss detection.

## 13. Restore workflow

PROPOSED D3 performs isolated staging only. Future activation is CONDITIONAL on D4/D5.

1. Register incident, disable automatic processing and acquire authenticated recovery authority.
2. Fence all writers and acknowledgment exporters; establish final independent evidence coverage.
3. Preserve original DB and associated journal/WAL evidence without opening or mutating the original.
4. Select a specifically approved candidate using lineage, completeness and evidence, not newest-looking name.
5. Verify authenticated manifest, artifact digest, access rights and source environment.
6. Recognize exact schema and required validator/runtime; unsupported candidates stay held.
7. Materialize into an exclusive private staging path; never overwrite the original.
8. Run physical integrity, FKs and complete semantic reconstruction on one snapshot.
9. Reconcile independent acknowledgment inventory, lineage and generation boundaries.
10. Hold missing/conflicting histories; record restricted evidence and escalation disposition.
11. Obtain separate activation approval bound to candidate digest, incident, evidence checkpoint and epoch.
12. Allocate a new non-reused recovery generation in approved control metadata.
13. Activate under the approved serialized protocol; old workers and stale handles remain fenced.
14. Start fresh approved processes; reject stale-generation requests and reconcile uncertain retries.
15. Revalidate active identity/state and acknowledge completion only after durable activation evidence.

No guessed events, manual balance adjustment, orphan deletion, timestamp rewrite,
standalone release or silent key reuse. A candidate may be useful for investigation
without ever becoming eligible for service. Operator approval cannot waive missing
history or transform incomplete evidence into completeness.

## 14. Restore atomicity and rollback limits

CONDITIONAL initial activation model: fully quiescent same-filesystem generation
directories plus a durable active-generation reference. Stage and validate everything
first; sync data/metadata and parent directories using verified filesystem semantics;
publish an activation intent then switch the reference atomically; persist completion.
No multi-file update is magically atomic. A crash-state table must resolve intent,
reference and completion combinations to one authorized generation or a hold. On
restart, compare authenticated candidate digest and control epoch; do not guess from
mtime. The exact protocol requires D4 design approval and filesystem testing.

Rename does not revoke open connections. Old SQLite handles can address an old inode;
WAL/SHM/journals must never be mixed between generations or deleted to force startup.
Close/drain connections and prove old write capability is gone before switch. Cross-
filesystem copy is staging, never an atomic activation operation. Long-lived readers
must also restart before relying on the new generation's state.

Before any new activity, rollback to the preserved generation still requires an
explicit approved generation transition and receipt reconciliation. After new commits
or acknowledgments, reverting the pointer can lose work and is forbidden as an automatic
rollback. Preserve both histories and resolve under a reviewed incident procedure.

## 15. Operator fencing

PROPOSED D4: monotonically increasing, never-reused recovery epoch from an independently
durable control plane, plus explicit database lineage/generation metadata and writer
capability. All enabled write paths must validate generation and current authorization
inside their SQLite write transaction, before idempotency result access and mutations.
Recheck current state/revision under the same lock. A cached epoch, PID, lease TTL,
frontend flag or pre-transaction remote lookup alone is insufficient.

A DB-only epoch restored with an old backup can also roll back. External authority
must reject that epoch. Conversely, a transaction check inside a stale, unlinked inode
cannot see a new DB's epoch. Initial design therefore requires exclusive recovery
ownership, drained transactions, confirmed process/connection shutdown and revoked
storage capability before activation. If those facts cannot be proven, remain held.
Do not promise atomic cross-system fencing from an ordinary network epoch check.

Serialize operators through a durable CAS/lease authority with epochs; expired lease
holders cannot activate or write after reacquisition. Lease uncertainty halts progress.
The chosen deployment must enforce that every writer, including maintenance and
migration tools, participates. Old v4 writers must reject a future fencing-enabled
schema; do not preserve a bypass that can write without generation checks. Current
inode checks are useful detection, not a replacement for this protocol.

Retry after generation change requires fresh authority and explicit reconciliation
against old receipts. Never infer that absent restored outcomes are unexecuted work.
No authentication or fencing mechanism is implemented by Package C or this document.

## 16. Migration recovery across v1–v4

| Version | Validation and preservation requirement |
| --- | --- |
| v1 | Exact base manifest/app ID, physical/FK checks and all posted event/accounting replay; do not construct VirtualLedger on missing paths because its initializer can create a DB |
| v2 | Above plus exact order extension, initial intents, every command/event/revision and pure transition |
| v3 | Above plus original reservation-operation ordering, denials and exact CREATE effects |
| v4 | Above plus complete evolved-history reconciliation, migration boundaries, terminal release and outcome correspondence |

Future D1 must use an existing-file, version-aware read boundary; constructors alone
are not equivalent to all checks. v1's current `_verify_schema` does not itself run
complete integrity/FK/business checks; a diagnostic must add those read checks explicitly.

Existing migrations are separately confirmed adjacent transitions (v1→v2→v3→v4),
transactional, exact-manifest and repeat-verifying. Recovery is never permission to
upgrade, downgrade or weaken version validation. Restoring v2 first validates as v2;
an authorized upgrade is a separate operation with a preserved pre-migration artifact.

Interrupted migration: reopen an isolated copy and determine exact old or exact target
state. Valid old state may undergo explicit retry; valid target may verify repeatably.
Mixed/unknown state is held, never patched with PRAGMA or dropped tables. Record future
migration intent/completion, runtime, before/after references and operator identity in
protected operational evidence. Current schema/version and v4 baseline markers do not
prove a complete historical migration log. Preserve all original ledger/order/reservation
rows and successful outcomes; new-version activity rules out blind old-backup rollback.

## 17. Deterministic offline crash/failure matrix

PROPOSED synthetic acceptance scenarios. Each uses explicit schedules, isolated DBs,
fixed command fixtures and controlled fault barriers; no real data/providers. State
labels PRE/POST mean byte/logical comparisons against the complete expected transaction,
not just final balance. Diagnostics are proposed D1 classifications. All allowed actions
remain subject to authorization. These are specifications, not newly executed tests.

| # / setup | Trigger | Expected state | Diagnostic | Allowed action | Forbidden action | Objective pass criterion |
| --- | --- | --- | --- | --- | --- | --- |
| 1 Valid active order | Crash before transaction | PRE | HEALTHY locally | Reconcile then identical retry | Release on timeout | No new rows/key |
| 2 Same | Crash after validation | PRE | HEALTHY | Retry after checks | Invent outcome | PRE exact |
| 3 Same | Crash after event insert | PRE | HEALTHY | Reopen/retry | Keep event alone | All new rows absent |
| 4 Cancel/expire | Crash after release insert | PRE | HEALTHY | Retry whole command | Compensating release | Reservation still active once |
| 5 Same | Crash after outcome insert | PRE | HEALTHY | Reconcile | Publish success | No uncommitted outcome |
| 6 Same | Crash before commit | PRE | HEALTHY | Retry same identity | New replacement key | PRE exact |
| 7 Valid command | Commit then crash before reply | POST | HEALTHY | Resolve original result | Apply effect twice | One event/outcome/release |
| 8 Lost reply | Same canonical retry | POST | HEALTHY | Return original | Change request on same key | Unchanged POST |
| 9 Active obligations | Restart | Same active set | HEALTHY | Hold/resume only eligible work | Age-based release | Exact C−R and Q−S |
| 10 Terminal orders | Restart/retry | Terminal preserved | HEALTHY | Return committed result | Resurrection | No extra release |
| 11 Terminal fixture | Remove required release | Evidence preserved | INCONSISTENT | Quarantine | Recreate release by guess | Reads/mutations blocked |
| 12 Terminal fixture | Attempt duplicate release | SQL reject or replay failure on damaged copy | INCONSISTENT if persisted | Preserve evidence | Deduplicate silently | No second resource effect |
| 13 Committed command | Corrupt outcome payload | Invalid snapshot held | CORRUPT/INCONSISTENT by failure layer | Diagnose copy | Replace payload | Fixed classified failure |
| 14 Valid history | Delete initial/intermediate event | Held | INCONSISTENT | Incident review | Reconstruct from current state | No false HEALTHY |
| 15 Valid file | Unknown user_version | Untouched | UNSUPPORTED_SCHEMA | Obtain approved reader | Guess latest schema | No migration/write |
| 16 v3 fixture | Kill at DDL/version/baseline/commit barriers | Exact v3 or complete v4 | Version-specific result | Validate then authorized retry | Patch mixed state | All-or-none migration |
| 17 Active writer fixture | Backup across commit barrier | One coherent committed snapshot | HEALTHY locally | Validate destination/reference | Pair old data with new live tip | No mixed transaction |
| 18 Destination in progress | Kill backup process | PARTIAL | UNVERIFIED | Quarantine/restart new destination | Publish as eligible | No completion marker |
| 19 Completed candidate | Corrupt bytes/records | Candidate held | CORRUPT or INCONSISTENT | Select another approved candidate | Repair source silently | Digest/replay catches change |
| 20 Older valid candidate | Restore to staging | Old coherent state | Local HEALTHY, coverage pending | Compare independent evidence | Activate based on age | No automatic activation |
| 21 Receipt inventory extends candidate | Compare missing protected operation | Staging held | INCOMPLETE_EVIDENCE + gap code | Recover authoritative history/review | Reuse missing key | Explicit missing identity found |
| 22 Candidate incompatible with runtime | Stage validation | Original untouched | UNSUPPORTED_SCHEMA | Approved matching runtime | Auto-upgrade | No active switch |
| 23 Old open connection/process | Attempt write after activation | New generation unchanged; old capability revoked | UNVERIFIED/hold on fence failure | Reject stale writer | Trust inode check alone | No acknowledged stale-generation write |
| 24 Two authorized operators | Race activation CAS | One authorized epoch | Conflict report | Loser stops/revalidates | Last-writer-wins rename | One durable active generation |
| 25 Authenticated manifest | Artifact digest differs | Held | CORRUPT artifact code | Preserve/reselect | Recompute manifest to bless | No activation |
| 26 Damaged pages | integrity_check fails | Held | CORRUPT | Preserve original and copy | Run repair in place | No business HEALTHY |
| 27 Writer/read lock barrier | Recovery lock deadline | Existing state unchanged | UNVERIFIED | Bounded later retry | Kill arbitrary process/unbounded loop | No partial action |
| 28 Backup/activation fixture | Disk-full or injected I/O/sync error | Prior active state or explicit ambiguous hold; candidate incomplete | UNVERIFIED | Preserve/check actual durable state | Claim success from rename alone | Never ambiguous success |
| 29 Two portfolios | Cross-link corruption | Held | INCONSISTENT | Scope-aware incident | Reassign IDs | No cross-portfolio effect |
| 30 Old coherent DB | No independent receipt source | Local result only | INCOMPLETE_EVIDENCE | Investigate/hold | Claim no acknowledged loss | Activation denied |

Additional D4 tests: journal accepted/reply lost, journal unavailable after local commit,
outbox duplicate export, interior receipt omission despite a higher max sequence,
wrong lineage, tampered/rolled-back anchor, lease expiry during activation, crash at
each file/directory sync and active-reference publication, old DB-handle writes,
client replay across generations and unknown in-flight exporter status. Require
consistent classification and no fabricated financial event in every case.

## 18. Reconciliation performance

IMPLEMENTED v4 constructor and each public read invoke full replay. A write replays
before and after changes. `_validate_contents` includes integrity checking and all
order histories; reservation association and v4 reconciliation revisit histories.
Multiple portfolios and commands can therefore amplify reads/decoding beyond a single
linear pass. No production-scale benchmark or bound is established.

Measure separately: file size, ledger/order/command/reservation counts, worst history
length, number of portfolios, parsing/replay time, integrity-check cost, peak memory,
lock duration, concurrent read latency, backup starvation and full restore time. Use
synthetic workloads with reported hardware/runtime and cold/warm conditions. Cancellation
or resource limits produce UNVERIFIED with coverage, never partial HEALTHY.

CONDITIONAL optimization: trusted checkpoints/incremental validation only after a
separate proof of complete prefix binding, schema/validator identity, append-only
suffix checks and safe invalidation on restore/migration. Hashes/caches cannot bypass
missing history or L3. D1 should prefer full replay; correctness precedes speed.

## 19. L1: failed commands and retries

Only successful Package C transitions persist durable outcomes. An unsuccessful attempt
can later become eligible (e.g. awaiting-price after the prerequisite freeze). It did
not reserve its key or record a durable denial. New-key terminal commands fail rather
than persist the earlier design's proposed no-op. D1 must distinguish committed outcome,
uncommitted unsuccessful attempt, unknown lost reply and conflicting canonical reuse.
It cannot infer which absent case happened from the restored DB alone.

Future APIs must label acknowledgment-pending/unknown separately from denied or rolled
back, retain canonical identities and reconcile external evidence before retry after
restore. Durable failure/no-op semantics require separate approval, not recovery-time
synthesis. Product risk: treating a transient failure as a permanent denial can cause
surprising later success; inventing a new key can obscure outcome resolution.

## 20. L2: future admission

v4 supports advancing/releasing existing reservations only. Current replay deliberately
reconstructs all original v3 creations/denials before applying v4 releases. It cannot
admit new obligations against released capacity by relaxing a version check. Existing
v3 writers reject v4. D1/D2/D3 must pin this profile rather than generalize it forever.

Future admission needs separately approved chronological create/release ordering,
denial replay, sequence/identity domain, new-order baselines and schema/replay versioning.
Revisit state-reference encoding, manifests, receipt coverage, migration validation,
conservation and recovery tests then. Recovery never supplies an admission writer.

## 21. L3: reconciliation as an enforcement boundary

An SQL/FK-valid image is insufficient. D1 must run all applicable existing semantic
validators; D2 validates the actual completed backup; D3 validates the actual staged
restore; activation repeats identity and complete validation against the chosen image.
No utility may silently omit invalid rows, infer terminal effects, count only balances
or expose a raw write connection as an operator capability. Private names and synthetic
UUIDs provide no authentication. Coherent administrator rewrites require independent
anchors to detect; current replay does not provide cryptographic audit guarantees.

## 22. Privileged security and authorization

CONDITIONAL production requirements: authenticated named operators, least privilege,
separate backup-read/stage/activation roles, revocation and generation checks, an
independent activation reviewer under an approved policy, immutable restricted action
logs and incident linkage. Approvals bind exact lineage/generation/candidate/evidence
references; stale approvals cannot authorize another image. Emergency procedure must
be explicit and audited, never a default bypass of integrity or receipt requirements.

Treat backups as sensitive financial records: approved encryption at rest/in transit,
separate key management and restore-key drills, integrity/authenticity protection,
restricted private directories, access auditing and approved retention/deletion/legal
hold. Encrypting a backup does not prove its authenticity or receipt completeness.
No secrets in manifests/public logs. No publicly accessible recovery endpoint is proposed.
Current internal APIs implement none of these operator capabilities.

## 23. Owner/Beta isolation

All future paths are explicit Beta-owned allowlisted storage, outside this checkout,
owner homes/configuration and owner database roots, with validated lineage, ownership,
permissions and symlink/hardlink checks. Do not derive a default from owner storage or
copy owner `.env`, accounts, backups or credentials. Path validation is necessary but
not sufficient: deployment must isolate OS identities, storage mounts and capabilities.

Recovery runtime has no Toss/broker imports or capabilities, no live provider clients,
and no network except separately approved recovery evidence/storage services in future
production. Offline fixtures block all real network and real credential/owner DB reads.
This phase made no such service requests and read no `.env` contents.

## 24. Operational policy decisions

| Decision | Offline design work | External evidence/approval required |
| --- | --- | --- |
| RPO, RTO, cadence | Define loss classes and measurement procedure | Approved loss tolerance, workloads, drills and staffing |
| Retention/deletion | Classify DB, receipt, manifest and incident evidence together | Legal/data rights, independent journal coverage and policy |
| Encryption/access | Threat model, key separation and restore tests | Key service, operators, rotation and storage controls |
| Receipt source/coverage | Protocol, sequence and gap/conflict rules | Independent durability, completeness and authenticity |
| Fencing/generations | CAS and crash-state protocol, stale-worker tests | Deployment control, exclusive storage access and all-writer participation |
| Activation | Staging/reference model and failure table | Filesystem/VFS sync guarantees and authorization policy |
| Corruption/escalation | Stable classifications, holds and evidence preservation | Incident ownership, escalation and approved recovery disposition |
| SQLite/storage profile | Test matrices for versions/journal modes | Effective PRAGMAs, device failure behavior and multi-process topology |

None is silently approved. No numerical recovery promise is established here.

## 25. Offline acceptance and validation record

Future implementation acceptance: every matrix case has explicit assertions, reproducible
synthetic state, guarded child processes, no owner paths/credentials/broker construction
and no unexpected network attempt. Validate both business state and all related records;
repeat independent-process races and deterministic digest/diagnostic comparisons. Audit
failures must not be swallowed by tests expecting generic exceptions. No fixture may
weaken production path, schema or business validators to obtain a pass.

This design phase used source/schema/test inspection and documentation checks only;
no application import, SQLite connection, backup, restore, migration, provider request
or broker construction was required. No runtime regression was necessary to resolve a
new material behavioral question. Historical test results are in section 3, not claimed
as new runs. Validate document links, section/matrix completeness, whitespace, secret
patterns and exact one-file scope before review. No confirmed production defect was
identified; operational limitations are not treated as implemented guarantees.

## 26. Independently reviewable implementation packages

| Package | Dependency and bounded deliverable | Exit gate |
| --- | --- | --- |
| D1 Read-only diagnostics | Approved classification/state-reference design; exact v1–v4 existing-file readers and complete replay | Deterministic results, corruption/scope/readonly tests, no mutation, no false completeness claim |
| D2 Synthetic backup/manifest | D1; approve synthetic method and format | Concurrent consistent snapshot, partial-artifact handling, digest and complete validation tests; no production backup |
| D3 Staged restore verification | D1/D2 | Restore only to separate fixtures, verify identity/history/evidence classifications; no activation or overwriting |
| D4 Receipt integrity, generation and fencing | Explicit protocol/schema/security review; independent evidence and storage authority choices | Split into receipt/outbox and activation/fencing subpackages; all-writer enforcement and cross-system crash/coverage tests pass before activation is exposed |
| D5 Independent recovery audit | D1–D4 and selected operational profile | Adversarial process, corruption, acknowledgment-gap, restore-activation and security review; no unresolved critical/high defect |

D3 may model missing receipts as INCOMPLETE_EVIDENCE but cannot depend on an imaginary
D4 service or activate before it exists. None of these packages authorizes admission,
settlement, frontend work or deployment. Full regressions belong to implementation
milestones when shared behavior changes, with the established strict offline controls.

## 27. Critical Beta blockers

No deployed authentication/portfolio grants or recovery operators; no proven generation
fence; no independent complete receipt evidence; no approved backup retention/durability
or RPO/RTO; no verified activation protocol; no v4 admission; no approved trusted
calendar/time/price/seal authority or settlement; no demonstrated physical owner/Beta
isolation. Existing local HEALTHY state cannot remove these gates. Provider OFFICIAL/
FINAL claims and synthetic context/evidence cannot authorize execution or recovery.

## 28. Open decisions and README maintenance

Review format compatibility and canonical state references; receipt scope and privacy;
external anchor trust/retention; unknown committed-but-unacknowledged work; migration
and generation lineage; operator approval/revocation; activation crash resolution;
whole-DB versus proven scoped holds; durability profile; benchmark budgets; and whether
future failed-command outcomes should become durable. Record decisions before dependent
implementation, with explicit evidence and versioning.

README currently describes Package A as the latest virtual persistence capability and
Package B as proposed. A separately authorized update should record committed B/C,
explicit v1–v4 compatibility, Package D design-only status, absent backup/restore and
acknowledgment guarantees, admission chronology dependencies, trusted execution blockers,
owner/Beta separation and the revised closed-Beta roadmap. Keep historical milestone
status distinct from current repository status. No README or earlier document is edited
here; no repository/package rename is proposed.
