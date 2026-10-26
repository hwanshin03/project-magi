# Phase 8.3d.1 — Isolated virtual order persistence foundation

Implemented Package A of the [reservation design](virtual-order-reservations-design.md).
This is an internal offline storage foundation, not trusted order admission or a
deployable Beta service. Persisted examples are synthetic test fixtures. Reading or
decoding ACCEPTED does not establish acceptance authority, funding or reservations.

## Scope and compatibility

Added [order_codec.py](../magi/virtual/order_codec.py),
[order_storage.py](../magi/virtual/order_storage.py) and
[order_schema.sql](../magi/virtual/order_schema.sql). No existing production module,
schema, initializer, accounting calculation, test or dependency was changed.

The [v1 ledger](virtual-accounting-core.md),
[price/calendar contracts](virtual-execution-contracts.md) and
[pure lifecycle](virtual-order-lifecycle.md) retain their original behavior.
The [execution policy](virtual-execution-price-policy.md) and
[Beta architecture](beta-paper-trading-architecture.md) remain proposed policies;
no source, calendar, fee, cutoff, deadline, authentication or deployment approval
is supplied by this implementation.

## Explicit migration

`migrate_order_schema(path, confirmed=True, timeout=5.0)` is a dedicated trusted
internal operation. Exact `True` is required; it is operator acknowledgement, not
end-user authentication. The path must identify an existing isolated virtual file.
To prepare a fresh offline fixture, explicitly create a v1 `VirtualLedger` first,
then migrate it. Neither migration nor OrderStore creates missing files or parents.

The existing absolute `*.virtual.sqlite3` path, repository/configuration-directory
exclusions, private file permissions, ownership, symlink/hardlink and inode checks
are reused through a private existing-file adapter. It borrows only the original
path/connection guards; it does not inherit or expose ledger mutation methods.
The directory and calling process must remain trusted. File checks alone do not
provide deployment isolation or protection against an administrator.

Within one `BEGIN IMMEDIATE` transaction, migration:

1. Checks application ID 1297503281, version and the complete exact SQL object map.
2. Checks foreign keys and SQLite integrity, validates existing identities, and
   reconstructs every portfolio using the unchanged accounting replay function.
3. For exact v1, executes the additive statements individually and sets version 2.
   It does not use `executescript`, rebuild tables or rewrite ledger rows.
4. Verifies the complete v2 manifest and content before the sole outer commit.

An exact valid v2 request verifies all persisted history and returns 2 without DDL.
Unknown versions, extra/missing/modified objects, foreign or empty existing files,
partial migrations and corrupt history fail closed. Failures during DDL, validation
or commit roll back the entire operation. Lock waits are bounded; no automatic retry
or downgrade is supplied. Operators must fence writers and retain a verified backup
before a separately authorized real virtual-database migration. No real database was
migrated in this milestone.

Ordinary reads use read-only URI/query-only transactions and never migrate. Import
opens no database. Original v1 runtimes continue to reject v2, including a previously
constructed v1 instance; direct synthetic writes cannot bypass future reservations
through a new v2 public writer. This is intentional version isolation, not continued
v1 write compatibility against an upgraded database.

## Minimal v2 structures

The three original tables and all their constraints/triggers remain exactly intact.
Package A adds three tables:

| Table | Implemented structural purpose |
| --- | --- |
| virtual_orders | Immutable order/portfolio/instrument/currency columns, unique submission operation, canonical initial ACCEPTED snapshot; no mutable current-state projection |
| virtual_order_commands | Immutable operation/actor/key identity, SUBMIT or TRANSITION category and resulting revision; scoped key uniqueness |
| virtual_order_events | Immutable event UUID, scoped order/revision, command reference, prior/next state, effective/recorded instants and canonical event document |

The fourth proposed table, reservations, is deferred: no Package A read/codec or
migration invariant requires an inert financial table. Current state is reconstructed
from history, so there is no redundant current-state projection to update or repair.
These choices preserve the design's intent/history authority and smallest-foundation
scope. Package B needs an explicit reviewed schema extension, not permissive optional
tables or a silent upgrade.

Foreign keys bind orders to virtual portfolios/instruments and commands/events to
the same portfolio/order. Deferred cyclic links require each command's result to
reference its own event and every order to reference its submission command. Primary
and unique keys protect global order/event/operation identities, scoped revisions,
submission association and `(portfolio, actor, operation_kind, idempotency_key)`.
SQL enum checks exclude FILLED and REJECTED. An insertion trigger requires contiguous
revisions, matching prior state and nonregressing effective/recorded timestamps.
Update/delete triggers protect all three histories. Pure lifecycle validation remains
the authority for legal edges and cutoff/expiry semantics; SQL does not implement a
competing policy graph.

There is no command execution, business-denial cache or business-level submission
replay. The command records are structural fixture associations, not authentication
or completed admission. Future command request digests, outcome categories, BUY caps,
admission sequence, authorization/commit receipts and reservation effects require a
separately approved schema/service extension. Existing fill idempotency stays in its
original domain. A command may reference consecutive legal edges; its result must
be the final associated revision. Orphan, disjoint or inconsistent associations fail.

## Strict codecs and reconstruction

`encode_snapshot` / `decode_snapshot` preserve actual OrderSnapshot fields: intent,
selection, policy, state, changed_at and expiration evidence. Nested calendar/source
fields are explicit. Canonical JSON has a required version, sorted keys and compact
separators; unknown/missing/duplicate fields, noncanonical representations and oversized
documents are rejected. No pickle, generic object loading, float-to-Decimal coercion,
clock lookup, default timestamp or arbitrary metadata bag exists.

Quantities use canonical bounded Decimal text. Equivalent whole-share Decimal values
normalize identically without depending on the caller's decimal context. Instants are
canonical UTC ISO strings with six microsecond digits; IANA session timezone and local
date are retained separately. Policy durations use exact integer microseconds. Optional
evidence deadlines remain explicitly None. Existing UUID/enum/numeric/calendar/DST and
lifecycle constructors validate decoded values; no caller claims become trusted times.

OrderEvent adds explicit event/command UUIDs, revision, prior state and recorded_at to
the resulting immutable snapshot. Effective time is snapshot.changed_at, retained
separately from recording time. Recording cannot precede effect; neither is replaced
by the current clock. Event codecs revalidate nested values and reject forged FILLED.

`reconstruct_order(initial, events)` requires complete tuple history starting at
revision 1, verifies the initial ACCEPTED snapshot, rejects missing/duplicate/reordered
events and regressing recording times, and invokes the existing `validate_transition`
for each subsequent edge. That validator preserves intent/selection and rejects
impossible edges, temporal violations and terminal resurrection. It never repairs
history or chooses a convenient branch. Dataclass immutability is not protection
against hostile code controlling the process.

## Scoped internal reads

`OrderStore(path)` requires exact v2 and exposes only:

- `order(portfolio_id, order_id)`: reconstructed current snapshot.
- `history(portfolio_id, order_id)`: validated immutable event tuple.
- `orders(portfolio_id)`: scoped snapshots ordered by canonical order UUID.
- `ledger_history(portfolio_id)` and `ledger_state(portfolio_id)`: unchanged ledger
  event decoding/replay in a read-only v2 transaction.

Every operation requires explicit UUID scope; there is no implicit global listing.
An order absent from the requested scope yields fixed MISSING_ORDER, including an
order that exists under a different portfolio. Corrupt payload/history/association
yields INVALID_RECORD. Unknown portfolio listing uses the existing UNKNOWN_PORTFOLIO;
schema/storage errors retain the existing fixed virtual error codes. No exception
exposes raw JSON, paths or underlying SQLite messages.

Reads validate exact schema and foreign keys, compare indexed columns with decoded
payload identities/times/states, validate command associations and reconstruct the
whole requested history in one snapshot. No order, reservation or ledger changes
occur. Scope is not ownership authorization: these methods are not public endpoints.

## Tests, isolation and future work

[Permanent tests](../tests/test_virtual_order_persistence.py) cover strict codecs,
precision, existing lifecycle/DST behavior, immutable events, replay/corruption,
scope and database constraints; fresh/funded/traded v1 migration, event/key/balance
preservation, repeat migration, exact manifests, malformed/foreign files, injected
DDL/commit failures, lock contention, process restart and read/import isolation.
Only test code inserts synthetic order fixtures into isolated temporary databases.
New tests construct no Toss or broker adapters. Existing accounting fill fixtures
exercise v1 preservation only; the new production modules never call a fill operation.

No existing production modification was necessary. The private v1 path/connection
reuse is an explicit dependency covered by both old virtual tests and new migration/
read tests. The schema retains ordinary append-only protections; host administrators
can bypass them, so corruption detection and separate Beta deployment remain necessary.

Missing capabilities: trusted admission, BUY caps/reservations, share reservations,
available-resource enforcement, command execution/replay, authentication/team grants,
real calendar/price authority, commit-proof receipts, settlement and public APIs.
No financial reservation table or mutation, worker, provider call, new dependency,
owner DB access or broker path is introduced. Future atomic settlement still needs
separate authorization; original accounting transaction ownership is unchanged.

Recommended next work is separately approved Package B: versioned reservation
structures and atomic synthetic order/reservation tests, with trusted admission and
deployment still gated. README remains unchanged; a later documentation milestone
should describe current research/three-agent integration, virtual accounting,
contracts/lifecycle/persistence status, missing reservations/trusted execution,
authentication, owner/Beta isolation and the closed-Beta roadmap. Project MAGI remains
the internal name; MOIRAI remains a proposed public brand.

## Package A validation record

Final focused regression: 245 passed with 198 subtests (68 accounting, 53 execution
contract, 64 lifecycle and 60 new persistence tests). Final full pytest and unrestricted
repository discovery each passed 1,563 tests and 1,176 subtests; unittest discovery
passed 1,563 tests. Syntax validation covered 162 Python files and imports covered
121 MAGI modules. Exact dependency pins and pip check passed. One existing Google SDK
deprecation warning remains unrelated to this change.

Final runs used an empty credential environment, disabled dotenv loading, a nonexistent
test credential path for the existing secret validator, temporary-only database access,
blocked real HTTP transports and socket audit guards, including Python child processes.
All final boundary counters were zero. The first focused run stopped an import-time
urllib3 local IPv6 bind probe before it executed; the temporary runner then disabled
IPv6 capability probing and repeated validation with the same denial guards. No real
network transmission occurred. Each broad suite constructed 48 fixture-only broker
adapters and 121 synthetic Toss sessions using MockTransport; focused Package A/virtual
tests constructed none. No real broker or credential initialization was permitted.

Only this document, three new production files and one permanent test file were added.
Tracked files, HEAD and staging remained unchanged. Tracked/untracked whitespace and
secret-pattern review passed; existing credential-URL rejection fixtures were classified
as synthetic tests. The real .env remained ignored and its metadata unchanged without
reading its contents; production databases remained absent. Temporary validation
scripts/logs and the workflow-owned sleep assertion are removed during final cleanup.
