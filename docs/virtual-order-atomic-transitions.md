# Phase 8.3d.3 — Atomic virtual order transitions and reservation release

Package C adds an **internal synthetic-only** transaction boundary to the
[Package B reservation foundation](virtual-order-reservations.md). It is not a
public order service, authenticated executor, scheduler or deployable Beta system.
Project MAGI remains the repository name; MOIRAI remains a proposed brand candidate.

## Implemented surface

- [atomic_transitions.py](../magi/virtual/atomic_transitions.py): immutable synthetic
  context, single-edge command, durable outcome and strict canonical codecs.
- [transition_storage.py](../magi/virtual/transition_storage.py): explicit migration,
  scoped reads/reconciliation and internal `_transition_synthetic` operation.
- [transition_schema.sql](../magi/virtual/transition_schema.sql): additive v4 tables.
- [tests](../tests/test_virtual_atomic_transitions.py): permanent offline fixtures,
  concurrent SQLite connections/processes, corruption and crash tests.

No initializer, CLI, owner service, research/agent/provider/broker module, accounting
calculation, existing schema, dependency or README changes. The one necessary shared
production modification is described below. There is no fixture order-seeding API in
production; tests alone construct the existing persisted synthetic orders.

`SyntheticTransition` binds actor, lifecycle idempotency key, original reservation ID,
explicit `SyntheticContext` and one `OrderEvent`. The event contains global event and
command IDs, expected previous revision (`revision - 1`), prior state, requested
snapshot and recording time. Its snapshot retains portfolio/order/instrument scope,
immutable intent, original session/policy and any expiration evidence. The proposed
snapshot is compared with `transition_order` applied to the actual persisted state;
callers cannot replace intent, session, quantity or policy.

`SyntheticContext` has only a fixture UUID and explicit receipt instant. Its exact
class is required, with no default context, trust flag or grant. It is deliberately
not an authentication facade. Both context and events are claims used in offline
simulations. Neither knowing a UUID nor constructing these values confers authority.

The codec uses version 1, exact fields, compact sorted JSON, existing strict event
codecs and canonical UTC timestamps. Unknown/missing/duplicate fields, invalid UUIDs,
noncanonical or oversized records and unsupported versions fail closed. Frozen values
are revalidated before use. Errors expose fixed virtual/persistence/policy codes;
SQLite details and submitted payloads are not emitted.

## Supported operations

The unchanged [pure lifecycle](virtual-order-lifecycle.md) remains authoritative:

| Edge | Conditions and reservation effect |
| --- | --- |
| ACCEPTED → FROZEN | At/after the original cutoff; preserve the active reservation |
| FROZEN → AWAITING_PRICE | At/after the original regular opening; preserve the active reservation |
| ACCEPTED → CANCELLED | Strictly before cutoff; release the entire associated reservation |
| ACCEPTED/FROZEN/AWAITING_PRICE → EXPIRED | Existing explicit expiration rules; release the entire associated reservation |

Every operation requires a valid active reservation. Unreserved ACCEPTED fixtures and
insufficient-resource outcomes are not funded orders. Package C cannot create a missing
reservation, reinterpret a denial or release nonexistent resources. BUY cap and SELL
quantity validation remain Package B's original contract. SELL reserves the complete
order quantity; a BUY cap is an exact positive cent amount, not a price.

Cancellation at or after cutoff fails even if a worker has left the order ACCEPTED.
FROZEN/AWAITING_PRICE cannot cancel. There is no state regression, same-state edge,
terminal resurrection, implicit freeze, rollover or local-clock advancement.

Expiration requires explicit `ExpirationEvidence` and `NO_SEAL`. `UNKNOWN` or `SEALED`
preserves the reservation. PRICE_DEADLINE requires the original explicit deadline and
an instant at/after it. SESSION_INVALIDATED requires the unchanged pure validator's
matching known revised calendar and instant no later than original opening. Missing,
contradictory or post-opening ambiguous evidence fails; age/provider failure alone
never causes expiry. Original calendar and policy remain intact, while the revised
calendar/evidence is retained in the immutable event and canonical command.

FILLED remains inaccessible. No REJECTED extension, price acquisition, fill, funding,
settlement, book-cost change, realized P&L posting or financial ledger mutation exists.
There is no public admission, independent release method or generic SQL endpoint.

## One transaction owner

`_transition_synthetic(path, command, timeout=5.0)` uses the existing private connection
and path guards through a v4-only adapter. It inherits no financial writer. Within one
`BEGIN IMMEDIATE` transaction it:

1. Verifies exact application/version/schema, foreign keys and complete persisted history.
2. Resolves an identical durable command before stale-revision checks; conflicting reuse fails.
3. Reconstructs the scoped order and verifies expected revision/state and active reservation.
4. Calls the existing pure transition function and compares the complete resulting snapshot.
5. Inserts the original Package A command association and immutable state event.
6. For cancellation/expiry only, appends the full reservation release effect.
7. Inserts the canonical command/outcome association.
8. Rechecks schema/FKs and reconciles all history, outcomes, releases and availability.
9. Commits once through the outer connection owner, then returns the outcome.

No nested independently committed service, process-local lock, in-memory command cache,
provider call or automatic retry occurs. Any pre-commit exception rolls back every new
row, including command keys. An uncertain post-commit response is resolved by replaying
the same canonical request, not by creating a fresh key or compensating release.

Read transactions use query-only/read-only connections and one consistent snapshot.
`TransitionStore` exposes scoped `state`, `history`, `outcome` and `reservation_outcome`.
There is no global order listing. `state` returns active reservations and exact resource
availability; `history` and `outcome` retain terminal events and release decisions.
Original reservation outcomes remain readable even after their obligations are released.

## Explicit v4 extension

v3 can record CREATE and insufficient-resource outcomes only. It cannot safely represent
release by changing an ACTIVE row: that would rewrite its immutable history. v4 therefore
adds three append-only tables and preserves every existing object and record:

| Table | Purpose |
| --- | --- |
| virtual_transition_baselines | One migration-time history boundary per existing order; preserves valid older unreserved synthetic transition histories without claiming they were funded Package C commands |
| virtual_transition_outcomes | Exact canonical command, scoped actor/key, event/reservation references and release decision |
| virtual_reservation_releases | One full immutable release per original reservation, linked to its terminal event and command |

A successful outcome needing release and its effect have deferred cyclic foreign keys.
Existing command/event cyclic links, unique scoped keys and contiguous-revision trigger
remain intact. New unique keys enforce one release per reservation and one result per
command/event. Update/delete triggers protect all new tables. Full replay checks scope,
instrument, actor/key, canonical payloads, exact release amount, terminal evidence and
one-to-one associations beyond the local SQL constraints.

`migrate_transition_schema(path, confirmed=True)` accepts exact v3 or v4 only. On v3,
it validates original Package B replay, executes the additive DDL individually, records
existing order-history boundaries, sets version 4, verifies the exact target manifest
and replays everything before the sole commit. No executescript, table rebuild, record
rewrite or automatic migration occurs. An exact valid v4 repeat verifies without mutation.
Missing/foreign/modified/unknown files and v1/v2 fail; those versions require their own
separately explicit prior migrations. Failed DDL, version update, boundary insertion,
validation or commit leaves the original v3 intact.

Reserved v3 orders necessarily have exactly their initial ACCEPTED event; their v4
baseline is revision 1. Existing valid unreserved histories can be retained at their
original revision but cannot gain a Package C transition without a reservation. Every
post-baseline event must match a canonical Package C outcome. These markers are ordinary
immutable database history, not cryptographic protection against a database administrator.

Old v1/v2/v3 readers and writers deliberately reject v4, including existing instances.
There is no v4 order creation or new reservation-creation writer. Synthetic fixtures must
establish all orders/reservations through the prior stages before migration. Released
resources become available in reconstruction; re-admission is not implemented. No silent
reinterpretation of the v3 runtime, downgrade or real-database migration is supplied.

## Conservation and replay

First replay the original v3 reservation operations against unchanged posted history,
in their original per-portfolio order. Historical denials retain their original outcome;
a later release does not retroactively turn insufficient cash/shares into success.
Then validate every subsequent order edge/outcome and derive the exact terminal releases.
A reservation can close once, only through its own valid terminal event.

For each portfolio, `available cash = posted cash - active BUY caps`. Per instrument,
`available shares = posted shares - active SELL quantities`. Full validated release
adds back only that reservation's original amount. Arithmetic uses the original fixed
Decimal context; SQL never sums monetary text using floats. Nonterminal edges change
neither availability nor posted resources. Pending proceeds/shares are never included.

All original reservation rows remain ACTIVE *creation records*, not mutable current-state
projections. The v4 active set is original successful CREATE effects minus reconciled
terminal releases. Returned active records are deterministically reservation-ID sorted;
share views are instrument-ID sorted. No mutable balances or disk caches exist.

## Idempotency, revisions and failure semantics

Lifecycle command IDs and `(portfolio, actor, lifecycle key)` resolve exact canonical
replays, including the original event, fixture receipt, evidence and outcome. Changed
canonical content conflicts. Submission, reservation creation, lifecycle command, state
event and future fill identities retain distinct roles. A fill identifier supplies no
execution authority and no fill API is called.

Expected state/revision is validated under the SQLite write lock. Competing freeze,
cancel or expire operations have at most one accepted edge from a given revision;
identical concurrent commands return the same recorded result. Database uniqueness and
serialization work across processes. A stale/new-key terminal request fails without a
new outcome/event/release. The earlier design's proposed durable new-key terminal no-op
is intentionally not implemented in this smallest single-edge boundary; only identical
committed commands replay successfully. No terminal state is reinterpreted.

Malformed, stale or ineligible commands produce no durable business outcome or reserved
key. Only successful supported transitions persist outcomes. Lock failures return the
existing fixed storage code; callers can retry the complete command unchanged. Restart
requires no cache: reconstruct history, availability and outcomes from committed rows.

Missing events, invalid revisions/commands, missing release/outcome, orphan effects,
incorrect amounts, conflicting evidence and malformed manifests fail closed. Nothing is
silently repaired or discarded. Hostile administrators can rewrite a coherent database;
these controls are not a signed external audit or backup-acknowledgment ledger.

## Necessary existing-production modification

Only [reservation_storage.py](../magi/virtual/reservation_storage.py) changes:
`_association` and `_replay` receive a private `evolved_orders=False` keyword. Default
v3 behavior still requires the reservation's order to have only its original ACCEPTED
event. The v4 caller explicitly allows later validated history while retaining the
original event/command/actor/key, instrument, side and quantity association checks.
Its result describes original v3 obligations; the v4 layer separately reconciles releases.

A new module alone could not reuse this replay after a valid transition because v3's
initial-state restriction would reject it. Alternatives were copying reservation
arithmetic/reconciliation or modifying old v3 semantics globally; both were rejected.
No shared writer, schema, public API or default behavior changes. Package B, old ledger,
order storage, new migration and transition tests cover downstream compatibility.

MODIFICATION NECESSARY: YES. AUTHORIZED SCOPE: YES.
DOWNSTREAM REGRESSION TESTED: YES.

## Authority and deployment limitations

Receipt, effective transition and recorded timestamps are distinct explicit synthetic
claims. Existing validators require aware unambiguous timestamps and canonical UTC;
receipt cannot precede prior recorded history or follow effect/recording. No clock is
read. A supplied early receipt cannot extend the pure cancellation cutoff, but these
fixtures **cannot prove actual server receipt, commit time or historical authority**.
No operational cutoff/deadline is approved by this implementation.

Future real use needs authenticated actors, server-derived portfolio grants, serialized
revocation checks, trusted receipt/effective/commit evidence, clock health, approved
calendar/status/evidence authority and physically isolated Beta storage/runtime. Reads
and idempotent replay also need authorization. A UUID, confirmation flag, synthetic
context or private Python name is not a security capability. No public endpoint or
production authorization facade is provided here.

Owner storage, `.env`, brokerage credentials, Toss and live market/research clients are
absent from the new import closure. No production DB, real order, broker action or live
payload is involved. Existing full-regression Toss fixtures are separately authorized
synthetic credentials plus MockTransport; Package C tests construct no broker adapter.

Recommended separately authorized Package D: consistent backup/restore and acknowledged
operation-gap detection, restart diagnostics, operator fencing and recovery/reconciliation
procedures, with corrupt/incomplete histories held for review. Do not silently repair
records or invent missing commands. Package D, settlement, public admission, authentication,
price/calendar acquisition, workers, frontend and deployment are not implemented here.

## Package C offline validation record

Added 94 permanent tests. Final focused virtual regression: **408 passed, 219 subtests**.
Final pytest and unrestricted repository discovery each passed **1,726 tests and 1,197
subtests**; unittest discovery passed **1,726 tests**. Syntax validation covered 168
Python files and imports covered 125 MAGI modules. All five pinned dependencies and
pip check passed. One existing Google SDK Pydantic deprecation warning remains unrelated.

Coverage includes both reservation types, every supported edge, exact cutoff/deadline
boundaries, missing/unknown/sealed evidence, stale revisions, conflicting keys, cross-order
scope, immutable history, malformed records, migration rollback, lost replies, read snapshots
during writes, lock timeouts and independent-process races/crashes before and after commit.
Original accounting/order/reservation suites remain unchanged. No public seeding/admission,
release shortcut, market acquisition or settlement is exposed.

Simplification review removed a redundant recording-time check already implied by the
validated receipt/effective/recorded chain. It retained the small migration baseline table
to preserve valid old unreserved histories, and reused Package B replay rather than copying
its reservation arithmetic. Additional adverse migration/corruption/process tests were
added; focused and all three broad discovery modes were rerun afterward.

Temporary validation guards blocked socket/DNS/send and real HTTP transport operations,
real credential files and databases outside workflow-owned temporary fixtures. Python
child processes received the same boundary guards, including isolated `-I` children.
The runner used an empty credential environment, disabled dotenv, and pointed the existing
owner secret validator at a nonexistent temporary path. It disabled urllib3's local IPv6
capability probe and allowed only the existing read-only `git check-ignore` child command
in addition to guarded Python test children. No production or existing test assertion was
weakened. Temporary runners/logs/databases are removed during final cleanup.

Final runs recorded zero real transmissions, unexpected network attempts, real credential
reads, production database accesses or real broker initialization. Each broad suite used
48 fixture-only broker adapters and 121 synthetic Toss sessions with MockTransport;
focused virtual tests constructed none. No live model/provider verification occurred.
The real `.env` was not read or modified and remains ignored. README is unchanged.
HEAD remains the approved Package B baseline and staging stays empty; no commit or push.

Final scope/security review covered all tracked and untracked source documents. Secret
pattern matches were limited to five unchanged synthetic credential-URL rejection tests;
none occurred in Package C files. Changed-file whitespace and `git diff --check` passed.
The whole-repository whitespace scan also noted one unchanged pre-existing trailing-space
line in `magi/melchior.py:33`, outside this task's scope; it was not modified. Final review
also removed three unused test imports, followed by another complete offline regression.
