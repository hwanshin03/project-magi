# Phase 8.3d.2 — Durable virtual cash and share reservations

Implemented Package B of the [reservation design](virtual-order-reservations-design.md).
This is an **internal offline synthetic foundation**, not authenticated admission,
trusted acceptance, a public order endpoint or a deployable Beta service. Project
MAGI remains the internal name; MOIRAI remains a proposed public brand.

## Scope and production dependencies

New files only:

- [reservations.py](../magi/virtual/reservations.py): immutable reservation claims,
  outcomes, resource views and strict canonical codecs.
- [reservation_storage.py](../magi/virtual/reservation_storage.py): explicit v3
  adapter/migration, database replay, scoped reads and internal synthetic creation.
- [reservation_schema.sql](../magi/virtual/reservation_schema.sql): additive v3
  operation outcomes and immutable reservation effects.
- [tests](../tests/test_virtual_reservations.py): isolated synthetic fixtures and
  permanent regression coverage.
- This implementation document.

No existing production file, initializer, schema, test, README, dependency, research,
agent, owner accounting or broker module changes. Existing accounting calculations
are reused, never copied. The private v1 connection/path guards, v2 exact manifests,
order replay and ledger event decoder are explicit dependencies, covered by old
and new regression tests. No existing production modification is necessary.

## Exact schema compatibility and explicit migration

`migrate_reservation_schema(path, confirmed=True, timeout=5.0)` requires exact True
and an existing isolated virtual file. It accepts only exact v2 or exact v3 with
application ID 1297503281. A v1 file must first undergo the separately explicit
Package A migration. Imports, constructors and ordinary reads never migrate.
Missing, empty, foreign, modified, extra-object and unsupported-version files fail
closed. The manifest compares the complete SQL object map, not a subset.

The v3 adapter inherits only Package A's private existing-file adapter. It adds an
explicit manifest formed from the unchanged v1/v2 objects plus the v3 extension.
It inherits no financial or public order writer. Absolute private virtual paths,
repository/configuration-directory exclusions, mode/ownership, symlink/hardlink and
inode checks remain in force. The parent directory and process must be trusted.

One `BEGIN IMMEDIATE` transaction validates the exact source manifest, foreign keys,
SQLite integrity, every existing ledger and every order history; executes additive
DDL individually; sets version 3; validates the exact resulting manifest and complete
content; and commits once. No `executescript`, table rebuild, inferred reservation,
identifier rewrite or automatic downgrade occurs. DDL, validation and pre-commit
failures roll back completely. Repeating migration on valid v3 verifies all history
without changing bytes. Only temporary synthetic files were migrated for this task.

| Runtime | v1 | v2 | v3 |
| --- | --- | --- | --- |
| Original VirtualLedger, including already-open instances | Original accounting API | Reject | Reject |
| Package A OrderStore and migration | Migration can explicitly create v2; reads reject | Original read/replay API | Reject |
| ReservationStore / internal reservation operation | Reject | Reject | Exact v3 only |
| Reservation migration | Reject; explicitly migrate to v2 first | Explicitly migrate to v3 | Verified no-op |

This is intentional version isolation, not backward-compatible v1 writing into a
newer database. No direct funding or synthetic-fill writer is exposed on v3.
Future real migration requires separately authorized operator fencing, consistent
backup and recovery procedures. These tests do not establish deployment durability
under arbitrary hardware failure or lost backup acknowledgments.

## Immutable model and storage

`Reservation` contains canonical UUIDs for reservation, reservation operation,
portfolio, order, instrument, initial order command, initial creation event, actor
and idempotency key; `Resource.CASH` or `Resource.SHARES`; exact Decimal amount;
USD for cash or None for shares; and `ReservationStatus.ACTIVE` only. The codec
has explicit version 1, sorted compact JSON, exact fields and canonical decimal
strings. Missing/unknown/duplicate fields, unsupported versions, floats, booleans,
nonfinite numbers, malformed UUIDs, noncanonical JSON and oversized input fail.
A proposed Reservation value alone is not a committed active obligation: only a
validated `RESERVED` outcome and its matching durable row establish that effect.

Two new tables are necessary because insufficient-resource outcomes must survive
retries while creating no reservation:

| Table | Authority and constraints |
| --- | --- |
| virtual_reservation_operations | Immutable canonical request and RESERVED / INSUFFICIENT_CASH / INSUFFICIENT_SHARES outcome; global operation ID, scoped actor/key uniqueness, contiguous per-portfolio sequence and supporting command/event associations |
| virtual_order_reservations | One immutable ACTIVE effect per order and initial event; global reservation ID, unique operation, resource/amount/currency, full canonical claim and matching durable operation |

Deferred cyclic operation/effect foreign keys require every successful outcome to
have its corresponding reservation and every reservation to have its successful
outcome. Denials have no result reservation. Scoped command/order links, instrument
and event foreign keys, uniqueness, enum checks and update/delete triggers remain
mandatory. Reconciliation additionally checks all columns against canonical payloads
and complete order/ledger replay. SQL never casts or sums money as floating point.
There is no mutable balance projection, reservation release flag or redundant state
machine. No pending order creates cash, shares, book cost or a financial ledger event.

## Posted, reserved and available resources

All posted values come from unchanged `VirtualLedger._events` and
`accounting.reconstruct`, through the same connection as the reservation read/write.
Caller-supplied balances and execution prices are not accepted inputs.

- Cash: `available = posted USD cash - sum(active BUY caps) >= 0`.
- Shares, separately per portfolio/instrument:
  `available = posted whole shares - sum(active SELL quantities) >= 0`.
- Cash caps are explicitly supplied, positive, finite, bounded by existing money
  limits, exact USD cents. Fractional cents are rejected rather than rounded.
- SELL amounts are positive whole Decimal shares, at most 1,000,000 per order, and
  equal the entire associated SELL intent quantity. No fractional shares or shorts.
- Arithmetic uses the existing fresh precision-80 / ROUND_HALF_EVEN context.
- A registered instrument with no posted holdings has verified zero shares. Unknown
  portfolios, broken history or unresolved associations fail instead of returning zero.

No default cap percentage, price multiplier, fee policy or market price is invented.
A future official opening debit above the cap must not spend unreserved funds;
approved rejection/release and settlement integration are still missing. Pending
BUY shares and pending SELL proceeds never contribute to current availability.

## Internal synthetic operation and atomicity

`_reserve_synthetic(path, request, timeout=5.0)` is deliberately internal and has no
CLI, HTTP, UI or package-initializer exposure. It requires an already persisted,
reconstructible fixture order with exactly its initial ACCEPTED event. Its portfolio,
instrument, side, amount, initial event/command, actor and submission key must match.
FROZEN, AWAITING_PRICE, CANCELLED, EXPIRED and invalid histories are rejected. FILLED
remains forbidden by the unchanged lifecycle. No synthetic seeding function exists
in production; only test fixtures insert orders.

Package A ACCEPTED records are simulation claims, not financial admission promises.
An unreserved seed therefore remains an unadmitted fixture. Package B never creates
an order and then risks leaving a half-admitted order on failure. Its single write
transaction validates the preexisting supporting order and creates the reservation
operation outcome plus effect together. It does not append a new order command or
state event: the existing initial event/command is the CREATE association, and the
new operation records the reservation request/outcome. No future admission authority
is implied by adding a reservation to that synthetic history.

Under `BEGIN IMMEDIATE`, the operation:

1. Verifies exact schema, integrity and all existing ledger/order/reservation history.
2. Resolves a committed operation identity or scoped actor/submission key.
3. Validates the synthetic order association and current database-derived resources.
4. Writes a stable insufficient-resource outcome, or a successful outcome and effect.
5. Rechecks foreign keys, full reconciliation and resource invariants; commits once.

Any failure before commit rolls back all new rows. There is no separately committed
key, financial posting, provider call or automatic retry. An exception after a
successful commit is an uncertain result, resolved by replaying the same identities,
not by creating a new reservation or releasing resources.

## Idempotency, concurrency and reconstruction

Reservation operation IDs are distinct in meaning from order IDs, initial command
IDs, reservation IDs and future settlement identities. The actor/key must match the
original submission; the new operation table supplies a separate internal namespace,
not a new user-facing reservation retry endpoint. All canonical fields are compared,
including identities and cap. Equivalent Decimal spelling normalizes identically.

Identical retries return the stored outcome without additional rows or resource
use. Conflicting reuse of the operation ID or `(portfolio, actor, submission key)`
fails with IDEMPOTENCY_CONFLICT. Reusing an active reservation identity for another
order fails. Database uniqueness plus writer serialization enforce these properties
across processes; no process-local lock or object identity provides correctness.

Operations have contiguous per-portfolio sequence. Replay starts from verified posted
balances, validates every supporting order, recomputes each outcome in sequence and
reconciles successful effects against the reservation table. v3 deliberately has no
ledger writer or lifecycle service. Altering that fixed foundation under existing
outcomes can invalidate replay; no future writer may bypass reservation integration.
Ordinary immutability constraints do not protect against a malicious DB administrator.
No missing history is guessed or repaired.

`ReservationStore(path)` exposes scoped read-only/query-only transactions:

- `state(portfolio_id)`: exact posted/reserved/available cash, instrument-sorted share
  views and reservation-ID-sorted active records.
- `reservation(portfolio_id, order_id)`: validates the durable order and returns its
  reservation or None for a valid unreserved fixture.
- `outcome(portfolio_id, actor_id, idempotency_key)`: original immutable outcome or None.

Each read uses one consistent snapshot. Missing orders and wrong scope fail with
Package A's fixed MISSING_ORDER; unknown portfolios fail UNKNOWN_PORTFOLIO. Corrupt
records fail with fixed ledger/persistence codes; SQLite failures expose only
STORAGE_UNAVAILABLE. Errors never echo raw records, credentials or database paths.

## Security and remaining boundaries

An underscore, UUID, dataclass, manifest or confirmation flag is **not authentication**.
These methods are unsafe to expose directly to untrusted callers. No authenticated
team service, public order admission, trusted clock/commit receipt, real calendar,
market-data source, deployment or provider approval exists here. Physical owner/Beta
separation and separately audited authorization remain mandatory future requirements.
No owner database fallback, Toss/broker import, credential loader, network client,
research dependency, financial writer, raw payload or disk cache is introduced.

There is no standalone release operation. Future Package C must atomically validate
current reconstructed state and authorized scope/time/evidence, validate the pure
transition, release the corresponding obligation when appropriate, append immutable
transition history, persist the command outcome and commit once. Freeze/await must
preserve the obligation. This needs an explicit versioned extension to v3's ACTIVE-only
history and initial-event restriction; do not silently relax this manifest or replay.

Settlement remains separate: no FILLED/REJECTED extension, opening-price execution,
reservation consumption, BUY/SELL posting, unused-cap refund, SELL proceeds, partial
fill or corporate action. Those require approved trusted-price, authorization,
commit-proof and atomic ledger integration. Package C is not implemented here.

## Offline test coverage

Permanent synthetic tests cover numerical/codec rejection, exact resource arithmetic,
association/scope/state validation, idempotent successes and denials, real concurrent
SQLite connections, independent-process BUY/SELL races, snapshot reads during a
write, lock timeout, faults after each write, pre-commit rollback, process interruption,
lost reply after commit, fresh-process replay, corruption/orphans, exact migration,
old-runtime refusal, history preservation and owner/broker/network import isolation.
Temporary fixture databases alone are used. Existing accounting, contract, lifecycle
and persistence suites remain unchanged. Full-suite fixture-only broker construction
is separately counted and restricted to synthetic credentials and MockTransport.

## Package B validation record

Final focused regression: **314 passed, 208 subtests**, including **69 new tests**.
Final full pytest and unrestricted repository discovery each passed **1,632 tests,
1,186 subtests**; unittest discovery passed **1,632 tests**. Syntax validation covered
165 Python files; imports covered 123 MAGI modules. All five dependency pins and
pip check passed. Tracked/untracked whitespace, secret-pattern and scope checks passed.
The existing Google SDK Pydantic deprecation warning is unrelated to this milestone.

The strict simplification review removed a redundant JSON parse and an unused test
import. It retained the isolated schema parser to avoid modifying committed v1/v2
modules, and retained separate immutable outcomes so denials need no fake reservation.
No duplicate accounting algorithm, lifecycle graph, process-local locking, public
writer, mutable balance projection or speculative settlement structure was introduced.
Focused and complete regression were repeated after these changes.

Temporary validation guards blocked socket/DNS operations, real HTTP transports,
real credential files and databases outside the workflow's temporary directory,
including Python child processes. Final runs recorded zero real network transmissions,
unexpected network attempts, real credential reads, production DB accesses or real
broker initialization. Each full suite constructed 48 fixture-only broker adapters
and 121 synthetic Toss sessions using MockTransport; focused virtual tests constructed
none. No broker/Toss import is present in the new production modules.

The temporary runner disabled urllib3's local IPv6 bind capability probe while
retaining network denial. Its first broad run blocked an existing read-only
`git check-ignore` subprocess; the runner then allowed that exact command and all
suites were repeated successfully. No production code or existing test was weakened.
All runners, diagnostic logs and temporary fixture databases are removed at cleanup.
The real .env was not read; its metadata stayed unchanged and it remained ignored.
Production DB files remained absent. HEAD and staging remained unchanged; no staging,
commit, push, Package C or settlement work was performed.
