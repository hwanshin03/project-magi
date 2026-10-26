# Phase 8.3d.0 — Virtual order persistence, reservations and atomic accounting design

Status: **PROPOSED — design for review; no persistence, reservation or settlement implementation.**
Reviewed baseline: `main`, `e0396d146fcf02221723b369831161056097be15`.
This milestone adds only this document. It authorizes no subsequent code, schema,
migration, test, dependency, README, deployment or live-provider change.

## 1. Executive summary and status vocabulary

Recommend one isolated virtual SQLite transaction domain for immutable order intent,
order history, reservation projections, command outcomes and existing accounting
events. Use the existing pure lifecycle for the supported edges. Create an order and
its reservation together; cancel/expire and release together. Later, consume the
reservation, append the accounting event and record settlement/finality together.
Do not connect independently committing services and call that atomic integration.

The first implementation should establish an explicit virtual schema migration and
strict persistence codecs, with admission disabled. Follow with small reservation,
state-operation and recovery milestones. Trusted settlement is a separate gated
milestone. No intermediate milestone exposes a public order endpoint.

| Label | Meaning here |
| --- | --- |
| IMPLEMENTED | Verified in the baseline code, within its current offline/internal contract |
| PROPOSED | A concrete future design requiring separate implementation authorization |
| CONDITIONAL | Usable only after the named approval, evidence or integration dependency |
| UNVERIFIED | External authority, operating behavior or future correctness not established by this review |

All new tables, services, commands and receipts below are PROPOSED unless explicitly
marked otherwise. A table name in this document does not mean the table exists.
Real calendar authority, execution-price eligibility, authentication and deployment
remain UNVERIFIED/unimplemented. This is not a deployable Paper Trading service.
Project MAGI remains the internal name; MOIRAI remains a proposed platform brand.

Read this with the approved [Beta architecture](beta-paper-trading-architecture.md),
[accounting core](virtual-accounting-core.md), [execution policy](virtual-execution-price-policy.md),
[price/calendar contracts](virtual-execution-contracts.md) and
[pure lifecycle](virtual-order-lifecycle.md). Earlier documents contain historical
implementation-status statements. The code inventory below defines the current state;
the execution design's pre-deadline seal/recovery refinement remains applicable.

## 2. Current reusable interfaces — IMPLEMENTED

| Actual interface | Reusable guarantee | Boundary |
| --- | --- | --- |
| `models.identifier`, `number`, `money`, `arithmetic`, `Side`, `Instrument` | Canonical UUIDs, bounded Decimal values, fixed precision 80 / ROUND_HALF_EVEN, US/USD instrument allowlist | UUIDs do not verify ownership; Instrument has no listing MIC or mapping history; `money` rounds, so a proposed exact-cent debit cap needs an additional no-rounding validation |
| `SyntheticFill`, `Event`, `PortfolioState`, `Position` | Immutable synthetic accounting inputs/results, whole-share fills, bounded prices, fees, cash, basis and realized P&L | No order, trusted fill, authorization or reservation value; fills remain internal offline inputs |
| `accounting.reconstruct(portfolio_id, events)` | Contiguous per-portfolio sequence, single first funding, duplicate detection, nonnegative cash/shares, cents accounting and conservation | Validates posted events only; it cannot see or protect outstanding reservations |
| `VirtualLedger.create_portfolio`, `register_instrument`, `fund` | Explicit identities, one-time confirmed funding, scoped uniqueness | Trusted internal setup only; `confirmed=True` is not authentication |
| `VirtualLedger.apply_synthetic_fill(..., idempotency_key=...)` | Canonical request replay, unique portfolio/fill, atomic event append | Opens/commits its own transaction; cannot be called inside future settlement as a composable writer |
| `VirtualLedger.history`, `state` | Schema/history verification and replay-based balances | Each opens its own read transaction; future mutation must read through its existing write connection instead |
| `VirtualLedger._connection`, `_append`, `_events`, `_portfolio`, `_request` | Private transaction, replay, scope and canonical-request machinery | Private implementation details, not public transaction or execution capabilities |
| `SourceReference`, `PriceObservation`, `CalendarSession`, `SyntheticCalendar` | Immutable normalized claims; strict timestamps, identity/coverage/shape validation | OFFICIAL/FINAL flags and matching timestamps do not authorize settlement; synthetic calendar only |
| `SessionPolicy`, `SessionSelection`, `select_session` | Explicit cutoff/deadline parameters, deterministic first eligible session, unknown coverage fails closed | Supplied times/calendar are simulation inputs; no accepted-at durability proof or live authority |
| `OrderIntent`, `OrderSnapshot`, `prepare_order`, `validate_transition`, `transition_order` | Immutable intent, supported state edges, timing, cancellation and explicit expiration checks | No storage, BUY cap, actor grants or durable idempotency; FILLED rejects with SETTLEMENT_REQUIRED; REJECTED is absent |

Sources: [models](../magi/virtual/models.py), [accounting](../magi/virtual/accounting.py),
[storage](../magi/virtual/storage.py), [schema](../magi/virtual/schema.sql),
[execution contracts](../magi/virtual/execution_contracts.py),
[session policy](../magi/virtual/session_policy.py),
[order lifecycle](../magi/virtual/order_lifecycle.py).

## 3. Confirmed integration limitations and minimum future changes

These are limitations of intentionally narrower contracts, not confirmed defects in
their authorized scope. No production correction was made during this review.

| Responsible boundary | Confirmed limitation | Minimum separately approved change and regression |
| --- | --- | --- |
| `storage._append` / `_connection` | `_append` enters its own write connection; `_connection` executes BEGIN IMMEDIATE, yields, commits, then closes. Another outer transaction cannot roll back its committed event | Extract a private transaction-local append primitive while retaining the v1 public wrapper. Prove rollback at every combined-write step, including commit failure; retain existing append/idempotency/concurrency tests |
| `storage._verify_schema`, `_schema`, `schema.sql` | Exact `sqlite_master` object map, application ID 1297503281 and user_version 1 are required. Extra tables/indexes/triggers or version 2 are rejected | Explicit migration plus exact versioned manifests; no relaxed subset check. Test unknown/foreign/tampered schemas, preservation and interrupted DDL |
| `accounting.reconstruct`, `Event`, SQL CHECK | effective_at equals recorded_at; storage generates both from its posting clock and rejects clock regression | Preserve v1 equality/replay. Later add an immutable settlement envelope with separate verified execution_at; test joined time semantics rather than backdating the clock |
| `OrderIntent` / `OrderSnapshot` | No BUY debit cap, actor, persisted revision, command outcome or trusted admission receipt | Add a versioned persistence envelope around the unchanged values; no need to alter pure intents merely to persist extra service metadata. Test strict round trips, tamper detection and immutable fields |
| `order_lifecycle` | FILLED always blocked; accepted-order REJECTED is not represented. Calendar invalidation after original opening also fails unresolved | Keep these restrictions. A later approved settlement-state contract must cover FILLED, over-cap rejection and terminal effects; separate effective-time/cohort invalidation design is needed. Do not disguise these as EXPIRED |
| Existing direct synthetic writes | Posted-only checks could spend reserved cash/shares if allowed against a future order-enabled database | Keep v1 runtime restricted to v1; future order-enabled runtime exposes only scoped composite operations. Test that direct synthetic writes and arbitrary raw-connection calls cannot enter that runtime |
| Existing SELL fee arithmetic | A fee may exceed gross if posted cash covers the difference; it does not check available cash after BUY reservations | Initial proposed settlement profile requires explicit zero fees. A nonzero-fee extension needs cash-exposure reservation/solvency design and tests; do not change existing accounting arithmetic |

No change to owner storage, owner accounting, Toss, research, agents, voting,
AnalysisResult, fingerprints or dependencies is necessary for this proposal.

## 4. Source of truth and storage ownership

PROPOSED: keep ledger, orders, reservations and their command/audit records in the
same private virtual file. Later, required settlement links and canonical seal
decisions join that transaction domain. No owner database, attached owner file,
second order database, cross-file compensation or in-memory registry is authoritative.

- Existing immutable `virtual_events` remain the authority for posted cash, shares,
  book cost and realized P&L; retain pure replay and no new cash/position cache.
- Immutable accepted-order fields plus append-only order events are the authority
  for commitments, state history and reservation obligations.
- Current state/revision and one reservation row per order are checked projections.
  Reconstruct them from immutable records and compare before financial mutations.
- Immutable command outcomes bind requests to results. A response cache, worker
  queue or lease is neither an idempotency authority nor permission to settle.
- Read cash, reservations and state in one consistent read transaction. Do not join
  `ledger.state()` from one snapshot to reservation rows from another.

Keep the existing explicit absolute `*.virtual.sqlite3` path requirements, private
parent directory, file ownership/mode, symlink/hardlink/inode checks, application ID,
foreign keys and exact schema validation. A filename is not proof of Beta ownership.
Use a dedicated Beta environment/volume and only known virtual schema identities.
No runtime constructor silently migrates, initializes a foreign/empty existing file,
copies an owner ledger or chooses a default database.

Recommend a single-host local persistent volume and SQLite writers serialized by
BEGIN IMMEDIATE. Multiple processes/connections are supported by the design; a
single human user or process is not assumed. Network filesystems, copied replicas
and distributed locks are outside this design. WAL is optional and not required;
if adopted later, its durability/checkpoint/backup behavior needs dedicated tests.

## 5. Minimum durable order and history

PROPOSED `virtual_orders` combines immutable accepted intent with a small current
projection. The service constructs it from a server-derived scope and resolved
session, never from an arbitrary caller-supplied OrderSnapshot.

| Proposed fields | Meaning / mutability |
| --- | --- |
| order_id, portfolio_id, instrument_id, exchange, currency | Server-assigned order UUID; scoped portfolio FK; registered instrument FK; explicit listing exchange; USD. Immutable |
| side, quantity, max_debit | Existing side and whole-share constraints. BUY has exact positive USD-cent cap; SELL has no cash cap. Immutable |
| submitted_at, accepted_at, acceptance_sequence | submitted_at means trusted server receipt for this persisted intent; acceptance is assigned under the admission write lock. Stable portfolio acceptance ordering. Immutable |
| target_session snapshot | Full normalized CalendarSession, date/ID, zone, UTC open/close, coverage/status, original SourceReference/revision. Immutable; no later calendar overwrite |
| policy snapshot | Serialization version, policy family/version, explicit freeze duration, optional evidence-wait duration, fee/rule profile, derived cutoff/deadline. Immutable; derived values must equal recomputation |
| admission scope/provenance | Beta actor ID, competition/team scope reference, authorization decision/revision reference, instrument-mapping revision, calendar authority/synthetic-mode reference, immutable accepted-content digest. Immutable; bounded references only |
| submission idempotency association | Unique submission_operation_id and portfolio/actor/operation/key plus normalized request digest; original OrderIntent.idempotency_key retains the submission key. Immutable |
| state, state_revision, changed_at | Current projection; updated only with its append-only event under compare-and-swap revision checking |

Preserve client-claimed time, if useful at all, as optional bounded telemetry outside
authoritative intent and idempotency semantics. Reject client choices of acceptance,
target session, policy, actor/team ownership, execution price/time and trusted status.
An advisory preview is not the accepted result. A retry never rewrites the first
accepted order's receipt time, session or cap.

Keep canonical UTC instants, exchange-local date/IANA zone, explicit duration units
and lossless bounded decimal strings. Reject floats, unknown fields, malformed or
unsupported schema versions. Use a new strict codec; do not reuse pickle or broaden
research serializers. A digest is a consistency check, not authentication or a
replacement for retained normalized fields needed to replay a decision.

PROPOSED `virtual_order_events`: immutable event UUID, scoped order ID, contiguous
order revision (initial acceptance = 1), prior/result state, server transition time,
actor/service identity, command association, fixed reason code, immutable evidence
reference/snapshot, and reservation action/amount. ACCEPTED records CREATE; later
events record NONE, RELEASE or future CONSUME. No generic arbitrary balance delta.
Current projection must equal the last event; every edge is revalidated on replay.
Events and accepted intent cannot be updated/deleted. No state update without audit.

No REJECTED order is fabricated on failed initial admission: a durable authorized
business-denial command outcome can exist without an order or reservation. This
refines the broader proposed admission model without changing Phase 8.3c states.
Accepted-order execution rejection remains a separate later extension.

## 6. Cash reservations

Let C be posted cash reconstructed from `virtual_events`, R the exact sum of ACTIVE
BUY reservation caps and A available cash, all for one portfolio/USD:

`C >= 0; 0 <= R <= C; A = C - R >= 0`.

PROPOSED cap r is the maximum authorized **total debit**, including any approved
fees, selected by the member and validated against server rules and availability.
It is not an execution price or a claim that a particular opening price exists.
Require exact cents without implicit rounding, positive, within existing money
bounds. Persist normalized r and the policy version; do not later top it up.
No default price estimate, margin, borrowing or implicit cash reservation exists.

At admission, under one write transaction, require r <= A, create the accepted
order, add its reservation and commit its command outcome and audit together.
Reservations do not create BUY/SELL ledger events, reduce posted cash, change basis
or reduce equity twice. There is no initial funding or cash edit in reservation code.

| Situation | Required outcome |
| --- | --- |
| Competing BUY caps | First serialized valid admission reserves; later request checks updated A. Overdrawn request gets an immutable business denial, no order/reservation |
| Cancel/expire | Release precisely that order's entire active cap with its legal terminal transition; C is unchanged |
| Future debit d <= r | Atomic settlement sets C' = C-d, R' = R-r. Unused r-d becomes available; no partial reservation remains |
| Future debit d > r | No fill, resizing, top-up or use of another order's cap. Whole-order rejection/release requires the later approved REJECTED settlement contract |
| DB failure / uncertain commit | All operation effects roll back, or all committed effects are recovered by the same command/order identity; no compensating release based on timeout alone |
| Duplicate settlement | Return the stored authorized result; no second debit or reservation consumption |

For a qualifying BUY, `A' = (C-R) + (r-d) >= 0`. Preserve this proof inside the
transaction as well as checking the existing posted-cash/basis conservation equation.
Do not finance admission from pending sale proceeds, pending funding, expected
returns or another order's expected unused cap. Newly posted proceeds only affect
subsequent admission; they cannot rescue an already accepted over-cap order.

Synthetic arithmetic example only: C=100000.00, active caps 60000.00 and 30000.00
leave A=10000.00. A third 20000.00 cap is denied. Settling the first for 55000.00
would leave C=45000.00, R=30000.00, A=15000.00. No such record is created here.

## 7. Share reservations and fees

For each portfolio and stable instrument, let Q be posted shares, S the sum of
ACTIVE SELL reservations, and V available shares:

`Q >= 0; 0 <= S <= Q; V = Q - S >= 0`.

Reserve the full order quantity q only when q <= V. A partial sale of an existing
position is allowed; a partial fill of an order is not. A pending BUY contributes
no available shares. No symbol-based grouping across instrument identities, shorts,
fractional-order rounding, margin or intrabatch netting is permitted.

Cancellation/expiry releases q and leaves Q/basis unchanged. Future full settlement
sets Q'=Q-q and S'=S-q atomically, so V'=V. The existing accounting calculation
releases exact proportional/full-close basis and retains realized P&L.

The proposed initial settlement profile has explicit zero fees; it is not silently
activated by these contracts. Existing accounting supports SELL fees greater than
proceeds when posted cash suffices. A share-only reservation cannot protect cash
already reserved for BUY orders in that case. Therefore reject any nonzero-fee
settlement profile at the initial integration boundary until a separately approved
fee-liability reservation policy exists. Never weaken the posted accounting core or
spend another order's reserved cash to pay fees.

Corporate actions require a hold before affected admission/settlement. Do not rescale
order quantities, reservations or accepted identities in place. Pre-cutoff cancellation
may release under an approved system rule; frozen commitments need cohort review.
Splits, fractional entitlements and mapping changes require future typed action
events and reservation reconciliation before processing resumes. Current whole-share
models do not authorize discarding fractional entitlements or fabricating trades.

## 8. Order state and reservation integration

PROPOSED durable services must call the existing pure validators for supported
transitions using reconstructed persisted values and authoritative service time.
The reservation milestone adds atomic persistence, not new pure lifecycle edges.

| Operation / edge | Reservation effect in the same transaction |
| --- | --- |
| Successful new ACCEPTED | Exactly one CREATE of immutable BUY cap or SELL quantity; initial revision/event and key outcome together |
| ACCEPTED -> FROZEN | Preserve ACTIVE obligation; append event/revision, no cash/share posting |
| FROZEN -> AWAITING_PRICE | Preserve ACTIVE obligation; no price/outage-based release |
| ACCEPTED -> CANCELLED | Before cutoff only; exactly one RELEASE with cancellation event/outcome |
| Any supported nonterminal -> EXPIRED | Pure expiration validation plus trusted evidence resolution; exactly one RELEASE with event/outcome |
| CANCELLED / EXPIRED | No further lifecycle edge or reservation mutation |
| FILLED | Forbidden in the reservation milestone; future authorized transaction records CONSUME and ledger effect together |

Time is checked even if a row still says ACCEPTED after cutoff. A worker must record
missing legal freeze/awaiting steps rather than skip the pure graph. Several legal
steps may commit together with contiguous revisions; no externally visible partial
state. Do not add same-state transitions for retry bookkeeping.

`ExpirationEvidence` and NO_SEAL are currently caller-supplied simulation claims.
Future service callers cannot pass a boolean to release real reservations. Resolve
seal absence, deadline qualification and invalidation evidence within the trusted
transaction domain. An absent implementation/table is not proof of NO_SEAL. Offline
reservation tests use an explicitly synthetic evidence authority; a real profile
without an approved authority fails closed.

SEALED/UNKNOWN evidence must preserve reservations. A qualified seal before D(s)
protects its cohort even if workers resume after D(s). Calendar invalidation under
the current pure contract requires matching known revised data and time no later
than original opening; a post-opening halt cannot be reinterpreted as opening failure.
If earlier cohort fills exist or revision chronology is unknown, hold and reconcile.
Never overload EXPIRED to release an over-cap, corrupt, disputed or selectively
unprofitable accepted order. Those cases require their separately approved resolution.

## 9. Four different idempotency boundaries

PROPOSED `virtual_order_commands` stores immutable normalized request, digest,
operation UUID, portfolio operation sequence, actor/service identity, operation kind,
key, safe outcome and resulting order/revision reference when present. Database
uniqueness is `(portfolio_id, actor_id, operation_kind, idempotency_key)`, preserving
the earlier actor-scoped proposal inside portfolio isolation. A portfolio key alone
must not leak or collide with another actor's command. Distinct members using distinct
scopes/keys can intentionally submit identical economic intents.

| Boundary | Identity and replay rule |
| --- | --- |
| Submission | Canonical instrument, side, integer quantity, BUY cap, permitted immutable rationale refs and request schema version. Exclude server-selected session/time and client telemetry. Same scope/key/body returns original outcome before re-running admission eligibility; changed body conflicts |
| Transition command | Separate operation/key; request binds order, expected revision and fixed requested operation/evidence reference where applicable. Same key returns original operation outcome before stale-revision checks, after current authorization |
| Reservation effect | No standalone reserve/release endpoint or independent retry key. Unique order CREATE and unique terminal RELEASE/CONSUME, tied to immutable order events. Command replay never reapplies either effect |
| Settlement | Future immutable plan assigns stable fill UUID and independent ledger idempotency UUID once. Unique order, fill and ledger-event links enforce one financial outcome even under different worker-delivery keys |

Keep Phase 8.2's portfolio-scoped accounting key domain unchanged. A settlement key
is distinct from submission/cancel keys; never pass a client order key to the generic
ledger namespace, which is already shared by funding and fills. Conflicting reuse
of any stored canonical request fails; compare normalized fields as well as digest.

Authorize before reading any stored outcome. Original command result is immutable;
current order state may be returned as a separately labeled authorized projection.
Replaying acceptance after cancellation does not resurrect ACCEPTED or reserve again.

Duplicate cancellation with the same key returns its original result. With a new
key, an already-CANCELLED order returns a recorded no-op outcome, with no state event
or release. Cancellation of an EXPIRED/FILLED order is a state conflict. Duplicate
expiration is a no-op only if the established terminal reason/evidence matches; a
different purported outcome is a conflict. Other new-key stale commands fail revision
checking. These are command outcomes, not extra pure lifecycle transitions.

Persist authorized business denials (including insufficient funds/shares and denied
session admission) with no reservation/order; the same key returns that denial even
if resources later change. A genuinely new intent requires a new key. Unauthorized
or malformed requests create no business command row. Storage/lock failure that did
not commit creates no outcome; retry the same key. Never retain a key in a separate
transaction before its outcome/effects are known.

Retain command associations through the approved competition/audit retention period;
no key eviction while related history survives. Process-local locks/caches do not
provide correctness. An uncertain commit is resolved by authoritative read/retry,
not by generating a new order, fill or key.

## 10. Concurrency analysis

Every row below assumes one verified virtual write transaction, exact invariant
checks and database uniqueness. Operations may arrive from independent processes.

| Adversarial scenario | Required result |
| --- | --- |
| Two BUY orders together exceed available cash | Serialized admission; one or both within availability succeed, the excess request is denied without changing earlier reservations |
| Two SELL orders together exceed available shares | Serialized exact-quantity checks; no oversell; loser receives a stable denial |
| Concurrent BUY and SELL admission | Each reserves only posted resources after existing reservations; neither borrows the other's future fill |
| Cancellation races settlement | Before cutoff authorized cancellation can win; a valid opening settlement cannot be eligible then. At/after cutoff cancellation fails regardless of worker state; terminal/revision/unique-link checks prevent both outcomes |
| Expiration races settlement/sealing | A valid pre-D seal and its cohort prevent time expiry. Seal registration and expiry recheck serialize; unknown seal status holds. Terminal CAS prevents a second outcome; a late seal cannot resurrect an expired order |
| Two workers process one order | Expected revision/CAS plus operation/result and settlement uniqueness; one effect, exact replay or safe conflict for the other |
| Duplicate admission from two workers | Unique command scope resolves to one order/reservation; conflicting body fails |
| Retry after commit / lost response | Original stored result; no financial or reservation mutation |
| Interruption before commit | All changes roll back; identical retry is eligible for a new atomic attempt |
| Interruption after commit | Reconstruct committed state and resolve key before doing work; never infer failure from absence of a response |
| Database busy | Bounded timeout, fixed storage error, no partial effect; later whole-command retry uses same identity and refreshes time/scope |
| Settlement races new admission | Both use the same lock/domain. New admission sees either all prior resources/reservations or the complete settlement result; accepted caps and historical availability are never retroactively changed |

Preserve deterministic per-portfolio settlement ordering by target session then
acceptance sequence. An earlier unresolved session blocks later settlement. Admission
does not consume pending proceeds; accepted orders keep fixed caps. Leases may later
reduce duplicate work, but do not grant settlement authority. No distributed lock,
worker election or global mutable registry is needed for correctness.

## 11. SQLite transaction protocol

PROPOSED composite mutation protocol:

1. Resolve a trusted caller and bounded request; prepare synthetic/approved immutable
   inputs outside the write transaction. No provider, model or broker call in it.
2. Open only the configured verified virtual database; enable foreign keys on this
   connection, use BEGIN IMMEDIATE, verify exact manifest/application/version.
3. Recheck current authorization/rules and resolve any committed command outcome.
   For a new command, read posted events, orders, histories and active reservations
   through this connection. Replay and compare projections before mutation.
4. For admission assign trusted acceptance time after lock acquisition, choose the
   eligible session, validate commitment timing and resources. For transitions load
   current revision and validate the supported pure edge and authoritative evidence.
5. Write command outcome, immutable intent/event and reservation/state projections
   as one unit. Allocate stable portfolio operation/acceptance sequence under this
   lock; use uniqueness plus expected state/revision in conditional updates.
6. Require every expected update to affect exactly one row, reconcile the complete
   proposed state and verify nonnegative availability. Any failure aborts everything.
7. Commit once at the outer owner. Return a result only after commit success; uncertain
   success is resolved with the same command identity. Roll back/close on all errors.

Read transactions use the existing read-only/query-only principle. Do not call the
public `state`, `history` or fill methods from the composite writer: they open separate
connections. Do not nest BEGIN or use a savepoint around an independently committed
fill. Avoid migration helpers that implicitly commit partway through DDL.

Use exact Decimal sums under `arithmetic()` for decimal-text reservations; SQLite
SUM/CAST on text must not introduce binary floats. Local CHECK/FK/unique constraints
guard shape and linkage; cross-row solvency/replay is validated by the restricted
transactional service. SQLite triggers are not a substitute for Decimal accounting
or host security. No public raw-connection or arbitrary SQL interface is proposed.

Keep lock waits bounded. A retry restarts the entire transaction and recomputes
current time after acquiring the lock. Do not sleep with a write lock, silently
extend a cutoff, or implement an unlimited busy loop. Durability settings must not
be weakened for speed; document and test the selected journal/synchronous profile
and filesystem crash behavior before deployment.

## 12. Future atomic accounting composition — CONDITIONAL

This is not part of the reservation implementation milestone. Dependencies include
trusted source/price/time, authorization, canonical cohort handling, approved terminal
states, migration, reservation reconciliation and an independent security review.

Minimum storage extension: retain `VirtualLedger`'s existing v1 public methods and
their behavior, but extract the private append work from `_append` into a primitive
that uses the caller's already-owned verified transaction and never commits/closes.
Keep canonical `_request`, portfolio/instrument checks, replay, key/fill uniqueness,
sequence and posting-clock validation. The outer v1 wrapper still owns its transaction.
The later v2 order store invokes the primitive only through its internal settlement
facade. A private method name alone is not a security boundary.

Use fixed internal schema profiles: the existing v1 facade still refuses an
order-enabled database, including when an old process survived an upgrade. The new
v2 facade verifies the exact approved manifest; do not let callers supply arbitrary
manifests, connections, clock functions or migration SQL. Shared private connection
logic may be extracted only where necessary. Initial funding/registration use a
separate trusted setup path; direct synthetic fills never enter the deployed v2 path.

Future settlement, in one transaction, must:

1. Authorize the service scope; resolve an existing settlement outcome first.
2. Load the immutable order and current legal pre-settlement state/revision, verify
   durable pre-cutoff admission, frozen authorization and current cohort safety.
3. Resolve the canonical eligible seal, original instrument/session/mapping, price,
   zero-fee profile and evidence timing. Use the persisted plan's stable identities.
4. Verify the exact active reservation and replay C/R/Q/S; reject any inconsistency.
   Compute whole-order debit/proceeds using existing accounting rules and BUY cap.
5. Append the accounting event through the transaction-local primitive. Close the
   reservation with CONSUME, record actual debit and unused cap where applicable,
   append the approved terminal state event, update projections, insert immutable
   settlement/evidence links and command outcome. No intermediate commit.
6. Recheck ledger conservation, availability, one-fill/order and all cross-links,
   then commit once. Failure at any step rolls back every order and financial effect.

For d > r, no accounting event is created. The future approved REJECTED operation
releases the entire cap with reason PRICE_EXCEEDS_RESERVED_CASH in one transaction.
Until that contract exists, hold the order/reservation for review; do not relabel
it as EXPIRED or manually free money. Reaching this case cannot activate a new state
without approval. Ordinary reservation code continues to reject FILLED.

Preserve `Event.effective_at == recorded_at == ledger posting time`. An immutable
one-to-one settlement envelope holds verified execution_at/precision, posted_at,
order/fill/event IDs, canonical seal reference, rules, authorization evidence and
normalized financial amounts. Require posted_at to equal the event timestamp and
execution_at <= posted_at with the full commitment/evidence chronology. Do not inject
an earlier opening into the ledger clock. Reports join the envelope for economic
time and retain posting-time knowledge; historical buying power comes from posted
resources at admission, never retroactive execution-time proceeds.

## 13. Trusted-price boundary

IMPLEMENTED PriceObservation is provider metadata, even with OFFICIAL and FINAL.
No provider is selected here. An eligible session, SourceReference UUID, matching
timestamp, `SealStatus.NO_SEAL` or user-provided evidence ID is not authority.

CONDITIONAL settlement resolves evidence produced by an independently approved
source/profile/validator path: original unadjusted official event, correct stable
listing/session/currency, finality and revisions, action/status coverage, rights,
timing and sufficient retained normalized provenance. Canonical seal/price and cohort
must be shared across teams and independent of worker order. Raw HTTP/model payloads,
headers, secrets and credential-bearing URLs must not enter order storage.

Reservation operations need no live price. Future offline tests inject explicitly
synthetic evidence and clocks through a test-only assembly. That assembly cannot
be configured as a public production executor. Unknown/missing real authority fails;
do not default to synthetic evidence, valuation quotes, adjusted candles or research.

## 14. Trusted time, sessions and durable commitment

| Time | Authority and role |
| --- | --- |
| Client time | Optional telemetry only; cannot select or backdate eligibility |
| Server receipt / persisted submitted_at | Trusted intake instant, distinct from queued processing and acceptance |
| accepted_at | Assigned after acquiring the admission write lock; never copied from browser or old retry arrival |
| Session date/open/close and cutoff | Versioned approved calendar/profile; preserve original snapshot and explicit configured durations |
| State changed_at | Server mutation instant, nonregressing against persisted history; equal times ordered by revision/operation sequence |
| Price event/provider observed/retrieved/validated/sealed | Separate roles; use the implemented `retrieved_at` for collector receipt, not provider `observed_at` |
| execution_at / posting time | Verified economic event versus actual later ledger recording; both retained |

No approved five-minute freeze or two-hour evidence deadline is supplied here.
Durations, clock authority/error bounds, commit safety margin, timezone-data profile
and calendar freshness require versioned approval. Replayed orders keep original
parameters. UNKNOWN coverage or source health never becomes a guessed session.

An accepted_at value sampled before COMMIT does not prove durable pre-cutoff
commitment. A clock-margin check alone cannot guarantee a stalled commit finishes
in time. A future protocol must record trustworthy commit-completion evidence whose
upper time bound precedes cutoff. Recommend retaining an immutable admission receipt
from a trusted post-commit observation, linked to the committed order/command digest;
the receipt may be persisted separately because it grants no financial effect by
itself. Its origin, clock uncertainty, crash recovery and persistence guarantees need
separate approval/tests. It is not a client boolean or backdated server field.

Until a receipt proves timing, the order is not eligible for execution even if its
pure snapshot is ACCEPTED/FROZEN. Lost/late/ambiguous receipt or clock regression
causes a safety hold, with reservation preserved pending an approved uniform
invalidation/release procedure. Current Phase 8.3c has no general ambiguous-commit
expiry reason, so do not invent one. This is a release blocker, not a claim that a
new receipt mechanism is already implemented or sufficient by itself.

Similarly, qualifying evidence must be durably sealed strictly before its explicit
deadline D; at/after D is too late. When the authoritative registry confirms no
qualified seal, supported deadline expiry is allowed at/after D. A pre-D seal retains
reservations for recovery after D. Sealing,
expiry, holds and evidence revisions must serialize in the same trusted decision
domain; empty state in an unavailable registry is UNKNOWN, not absence. A price
provider outage cannot become permission for a substitute price or later session.

## 15. Authorization dependency

PROPOSED internal caller context is derived by the trusted authentication boundary:
stable Beta actor/service identity, authorized portfolio/team/competition scope,
permitted action, grant revision, authorization time/decision reference and mode.
It is never deserialized directly from client roles, team names, UUID possession,
unsigned membership claims or frontend visibility. A Python dataclass alone grants
no capability. Missing authorization integration disables real order operations.

Reauthorize every read and mutation, including idempotent result lookup. The mutation
must serialize with membership revocation and competition holds, using authoritative
local grants or an equivalent approved fencing protocol. A stale remote pre-check
before BEGIN is insufficient. The identity vendor and grant persistence are later
dependencies; no auth tables or sessions are implemented here.

Before cutoff, authorized member cancellation/revocation rules release atomically;
after cutoff, accepted team commitments retain frozen authorization even if the
submitter's browser session expires. Settlement still checks current team/competition
safety. Emergency holds do not secretly cancel selected orders after prices are known.
Actor audit is distinct from authority; every worker also needs explicit scoped grants.

## 16. Owner/Beta isolation

Preserve separate Beta runtime identity, filesystem/volume, virtual database and
secret scope. No owner DB, `.env`, backups, account identifiers, Toss credentials or
home-directory mount belongs in that runtime. Use virtual identities and approved
Beta membership mappings only. A separate file on the owner's workstation is not
deployment isolation.

Future order modules should import only pure virtual contracts and their dedicated
storage/authorization interfaces. No `magi.storage.Database`, owner `Portfolio`,
portfolio CLI, broker/Toss, market-data client, agent or research writer dependency.
The existing `magi.virtual` initializer imports virtual storage but opens no DB;
preserve that import behavior. Settlement consumes approved durable evidence, not a
network client. Dedicated collectors have separate permissions and cannot submit
orders. Exclude direct synthetic writers and raw SQL from the public runtime surface.

No authentication, public API/UI or broker functionality is authorized by this design.
Even complete persistence and reservations would remain an internal offline capability
until authentication, time/price authority and deployment isolation gates pass.

## 17. Failure recovery and reconciliation

PROPOSED startup/recovery runs with provider calls disabled and mutations held until
schema, integrity, FK and reconstruction checks pass. Read a consistent snapshot:

1. Replay unchanged ledger events to C, Q, basis and realized P&L.
2. Validate immutable order snapshots/digests and replay contiguous revisions/legal
   state edges with pinned policy/calendar/evidence. Preserve timestamps and sequence.
3. Reconstruct reservation obligations from CREATE and exactly one terminal
   RELEASE/CONSUME, then compare current reservation and order projections.
4. Check C-R/Q-S nonnegative, command associations, one acceptance/reservation/order,
   no orphan or terminal ACTIVE reservation, and later settlement/fill/event links.
5. Resume same-session eligible work in deterministic order only after proof of
   current scope, time, evidence, holds and revision; never generate replacement IDs.

| Failure | Recovery rule |
| --- | --- |
| Worker crash before commit | SQLite rollback; replay the same command; no manual release |
| Lost response after commit | Return persisted outcome after authorization; exactly-once database effect, not exactly-once delivery |
| Pending order on restart | Recover its original session, cap, reservation and revisions; apply only still-valid legal steps |
| Session elapsed / missing evidence / outage | Do not roll forward. Resolve trusted deadline/seal status: supported no-seal expiry releases once; sealed cohort remains reserved; unknown status holds |
| Stale-looking reservation | Age alone does not release it. Validate order history and terminal/evidence conditions |
| Conflicting settlement attempts | Unique links and canonical plan reject conflict; no alternate price or second event |
| Corrupt projection / replay mismatch | Fail closed and quarantine affected scope. Preserve evidence; no silent projection overwrite or balance repair |
| Missing immutable event/evidence or cross-scope link | Integrity incident; stop affected mutations and final reporting |
| Backup checkpoint loses acknowledged operations | Hold and reconcile with retained acknowledgments/audit. Do not reuse lost keys, guess orders or declare a clean restore |

A separately authorized repair may rebuild a projection only from complete validated
immutable history, compare the result, retain a repair audit and obtain operator
review. It cannot invent missing intent, authorization, reservation or financial
events. Append-only constraints do not protect against a malicious DB administrator.

Backups must be SQLite-consistent, Beta-only and include every linked table plus
required retained evidence; keep secrets out. Test restore offline, including any
WAL and post-checkpoint acknowledgment gap. RPO/RTO, retention and incident staffing
remain the earlier architecture's unapproved operational decisions.

## 18. Proposed schema and explicit migration

Minimum reservation-stage extension: four tables, exact versioned manifest and local
constraints. No DDL is supplied or executed in this milestone.

| Proposed table | Keys, constraints and purpose |
| --- | --- |
| virtual_orders | Global order UUID PK; portfolio/instrument FKs to existing virtual identities; USD scope; immutable intent/snapshot/version/cap columns; unique submission_operation_id with composite (portfolio_id, submission_operation_id) command FK; unique (portfolio_id, acceptance_sequence), unique (portfolio_id, order_id); current state/revision projection |
| virtual_order_commands | Operation UUID PK; unique (portfolio_id, operation_id), unique (portfolio_id, actor_id, operation_kind, key), unique (portfolio_id, operation_sequence); canonical request/digest and immutable safe outcome; optional composite scoped order reference. Business denial may have no order |
| virtual_order_events | Unique global event UUID; PK (portfolio_id, order_id, revision); composite order and command FKs; from/to state, time/reason and reservation action/amount; update/delete denied |
| virtual_order_reservations | One row per scoped order; immutable kind/resource/amount and create-event revision; status ACTIVE/RELEASED/future CONSUMED plus closing-event revision; composite order/event FKs and instrument match |

Use side-dependent CHECKs for cash versus share fields, allowed states/actions,
positive revisions, closed-reservation metadata and USD. Reuse strict UUID/Decimal
validation in codecs; SQL checks alone do not validate exact Decimal arithmetic.
Composite FKs prevent a valid UUID from linking an event/reservation to a different
portfolio/order. Reject deleting parents; no cascading history deletion. Triggers
block immutable-column updates and ordinary history/command update/delete.

Partial unique indexes allow only one CREATE and one closing RELEASE-or-CONSUME
reservation effect per order. Current reservation amount/kind must equal immutable
intent and its event. A separate standalone reservation journal is unnecessary:
order events already carry the exact effect. Retain released rows for reconciliation.
Current-state-to-last-event and cyclic order/submission-command links can use deferred
composite FKs where insert order requires it; all links must validate at commit, with
tests proving no half-records. Submission-operation uniqueness prevents one successful
submission command from owning multiple orders; replay also verifies that its outcome
points back to that same order.

Indexes: pending orders by portfolio/session/state/acceptance sequence; active
reservations by portfolio/kind/instrument; events by scoped order/revision; command
lookup by its unique scope/key. No cash/holdings materialized table is needed initially.
Portfolio operation sequence is allocated under the writer lock from committed
commands; acceptance references its admission sequence, so gaps from other commands
are valid. Order revisions and ledger sequences retain their own distinct contracts.

CONDITIONAL later schema: immutable admission receipts, authoritative authorization/
safety records, canonical seals/cohort membership/plans and settlement links. For
settlement require unique scoped order, fill and ledger event; composite FK to
`virtual_events(portfolio_id, sequence)` plus event/fill identity consistency checks.
Missing ledger linkage or evidence is an integrity error. Do not add inert trust
booleans or a placeholder FILLED row in the initial schema. Reservation-stage allowed
states exclude FILLED/REJECTED until separately approved manifest/state extensions.
Initial reservation statuses are ACTIVE/RELEASED and actions CREATE/NONE/RELEASE;
CONSUMED/CONSUME require the later settlement migration and approved boundary.

Migration protocol, PROPOSED:

1. Explicit operator action on an independently configured virtual file, with all
   writers stopped/fenced and a verified consistent backup; no owner migration runner.
2. Validate private path/file identity, application ID, exact known source manifest,
   integrity/FKs and full v1 replay before DDL. Reject foreign, empty, tampered or
   unknown-future databases without modification.
3. In one explicit transaction add the approved objects and schema version; preserve
   every existing ledger/identity value and all accounting semantics/triggers. Do
   not create orders/fills from old events or invent historical reservations.
4. Verify target exact manifest, FK integrity, existing event replay/digests and
   empty new tables before committing. Fault at any step leaves valid old version.
5. Reopening an already migrated exact target is a verified no-op; old runtimes fail
   closed on the new version. Test v1 and new runtime compatibility separately.

Initial order-enabled v2 should be prepared in an isolated synthetic environment;
no active order writer is enabled in the schema/codec milestone. A future migration
of an actual Beta ledger requires explicit authorization and retention controls.
Rollback after new activity is not DROP TABLE or restoring an old backup over
acknowledged operations: stop, retain the new DB and use a reviewed forward repair
or reconciled restore. Only a proven unused target may revert under an approved
offline rollback procedure. No automatic downgrade or loss of command keys.

## 19. Objective future offline acceptance tests

All following tests are PROPOSED, not written here. Use synthetic schedules/evidence,
fake trusted clocks and temporary virtual DBs with sockets, owner credentials/storage
and real broker construction blocked. Prove behavior across independent processes as
well as threads; no production data or live price call is needed.

| Test area | Required observable assertion |
| --- | --- |
| One-time reservation | Same submission replay creates one order, one CREATE event and one active obligation; posted ledger bytes unchanged |
| Available cash | Multiple caps conserve A=C-R; failed excess cap changes neither money nor another order |
| Available shares | S<=Q by instrument under concurrent sellers; pending buys do not contribute; no margin/short/fractional fallback |
| Whole quantity / precision | Invalid sides, UUIDs, floats, nonfinite/oversized values and fractional cents/shares fail; equivalent decimal forms replay identically |
| Identical/conflicting submission | Same scope/key/body across workers returns one original outcome; changed quantity/cap/reference conflicts; different actor/portfolio stays isolated |
| Denied admission replay | Persisted insufficient-resource/session denial remains identical after resources/time change; malformed/unauthorized requests create no command |
| Concurrent BUY / SELL | Separate-process double-spend/oversell and mixed admission schedules never violate available resources |
| Cancellation | Before cutoff releases once; exactly/after cutoff cannot cancel even with an ACCEPTED row or stale worker |
| Duplicate cancellation/expiration | Same key returns original operation; allowed new-key no-op writes no extra state/reservation effect; conflicting terminal reason is rejected |
| Expiry and seals | Unknown/SEALED cannot release; no-seal deadline boundary works; post-opening ambiguous invalidation holds; no rollover |
| State/revision CAS | Stale competing commands cannot both advance; histories contiguous; terminal states never reopen |
| Crash before commit | Inject failure after each order/command/event/reservation write; no partial accepted order, release or key remains |
| Crash after commit | Lose response/kill worker, retry from new process with same identity; one effect and unchanged timestamps/session |
| Restart reconstruction | Exact reconstructed reservations/current state equal projections; no browser/cache dependency |
| Corruption/orphans | Missing history, wrong projection, cross-scope FK, duplicate closure or reservation mismatch blocks mutations; no automatic repair |
| Lock contention | Busy timeout leaves no effect; retry rechecks time after lock and cannot backdate admission |
| Settlement vs new admission | New admission sees consistent before/after state; no use of pending proceeds or another order's cap |
| Cancellation vs settlement | Timing gates, CAS and unique links prevent both terminal effects; no post-price cancellation |
| Expiration vs seal/settlement | Pre-D sealed cohort recovers after D without selective expiry; late seal and duplicate worker cannot revive terminal order |
| Future atomic settlement | Faults around ledger insert, consume/release, terminal event and link yield all-or-none outcome and exact conservation |
| Under/over-cap fill | Under-cap releases r-d; over-cap cannot fill/top-up/shrink; future approved rejection releases once with no ledger event |
| Fee boundary | Share-only reservation rejects unsupported fee profile; no use of BUY-reserved cash for a SELL fee |
| Trusted price / FILLED | OFFICIAL+FINAL alone, client prices, synthetic flags and arbitrary evidence IDs never authorize FILLED; reservation facade cannot call settlement |
| Time durability | Queue/lock delay, skew, clock regression, commit across cutoff and missing receipt never establish trusted commitment; posting time remains distinct from execution time |
| Corporate actions | Unknown action holds; no in-place rescaling; fractional entitlements never discarded; prior cohort fills prevent selective remaining expiry |
| Authorization | Forged team/portfolio, revoked/read-only member, guessed idempotency key and stale grant fail; transaction-time revocation race serialized |
| Migration | Known source preserved, explicit target verified, failure during/after DDL rolls back, unknown schema refused, old runtime refuses new DB |
| Legacy accounting/lifecycle | Existing weighted-cost, full-close, replay, key/fill uniqueness, clock, transition, cutoff, DST and FILLED-denial tests remain green |
| Isolation / restore | No owner reads, Toss/broker imports/initialization or network; restore all linked records offline, detect lost acknowledged commands, leave secrets out |

Reuse existing [virtual accounting tests](../tests/test_virtual_accounting.py),
[execution contract tests](../tests/test_virtual_execution_contracts.py) and
[order lifecycle tests](../tests/test_virtual_order_lifecycle.py). In particular,
`test_concurrent_buy_double_spend_prevented`, `test_concurrent_sells_prevent_overselling`,
`test_failure_after_insert_rolls_back_event_and_idempotency`,
`test_lock_contention_failure_no_mutation_and_safe_retry`,
`test_process_restart_preserves_idempotency_and_state`, schema/FK/immutable-history
tests, and the lifecycle cutoff/expiry/FILLED tests provide reusable scenarios.
They do not yet test reservations or composite settlement. Add those cases in future
authorized suites; do not weaken existing tests to accommodate a new manifest.

## 20. Small implementation milestones and required production changes

Each row requires a separate approved task; none begins under this document.
Names are work packages, not permission to advance the roadmap automatically.

| Package | Dependency and smallest scope | Exit gate |
| --- | --- | --- |
| A — Explicit virtual persistence foundation | Approve schema/serialization/key decisions; add exact versioned manifest and explicit migration with strict envelope codecs and scoped read/replay contracts | No admission/reservation writer or public endpoint; migration/codec/FK/old-version tests pass with new tables empty outside fixtures |
| B — Durable reservation accounting | A; add pure obligation reconstruction and private same-transaction acceptance/reservation primitive | Create order+reservation+outcome atomically in synthetic tests; conservation, denial/idempotency and independent-process races pass. No generic reservation endpoint |
| C — Atomic supported state operations | B plus explicit synthetic authorization/time/evidence assembly; add freeze, await, cancellation and supported expiry persistence | Existing pure graph unchanged; state/event/release all-or-none; duplicate/revocation/cutoff races pass. No FILLED or real deployment |
| D — Restart/reconciliation and migration recovery | B/C durable records | Process crashes, mismatches, restore/acknowledgment gaps and deterministic resume pass; no silent repair. Basic rollback/replay tests already mandatory in A–C |
| E — Trusted execution integration | A–D plus separately approved price/calendar/action/time authority, authorization, commit-proof protocol, canonical seals, terminal rejection/FILLED contracts and settlement schema | Private transaction-local accounting composition, immutable execution/posting-time envelope, over-cap rejection and sealed-cohort recovery pass independent review before real use |
| F — Independent security/concurrency audit | All intended internal components, plus deployment/auth dependencies if release is contemplated | Adversarial scope/import/permission/replay/process/restore tests, complete existing regression and explicit launch gate review; no public endpoints authorized by these packages |

Required future production changes are limited to new isolated order models/codecs,
repository/replay services and tests; explicit virtual schema/manifests/migrations;
and narrowly necessary private transaction extraction in `magi/virtual/storage.py`
when composition is introduced. Preserve v1 public behavior and monetary arithmetic.
Later trusted settlement/state/auth/time/price/action changes require their own
approval. No owner migration, research serializer, provider/broker or dependency
change is justified merely to implement reservations. Optional optimizations such
as materialized balances, leases, WAL and outbox notifications are deferred.

## 21. Critical release blockers

Persistence/reservations alone cannot remove any of these gates:

1. Actual authenticated team/portfolio authorization, revocation fencing and scoped
   reads/writes/result replay; no client assertion or developer default may stand in.
2. Approved calendar, listing/action/status coverage, explicit cutoff/deadline/fee
   rules, clock-health bounds and demonstrable durable commitment before cutoff.
3. Approved original official-price source/profile/rights, precise event semantics,
   immutable canonical seals and safe cohort correction/expiry recovery.
4. Exact virtual migration and atomic order/reservation/ledger integration, no direct
   synthetic-write bypass, independently tested crash/concurrency/restore behavior.
5. Approved FILLED and over-cap terminal rejection semantics, immutable settlement
   envelope and honest economic-versus-posting-time reporting.
6. Physical owner/Beta separation, no broker/Toss capability, safe logs/retention,
   operating staff, recovery objectives, competition/action/equity rules and release
   authorization. No public trading or definitive ranking claim before these pass.

These unresolved dependencies block release and dependent implementation, not review
of this documentation. No material defect requiring a current production edit was
confirmed. In particular, the existing SELL fee behavior, pure caller timestamps and
private transaction boundary are correct within their documented synthetic scope.

## 22. Open decisions, README maintenance and validation record

Review before dependent work: exact schema/codec versions and status constraints;
actor-within-portfolio idempotency scope and retention; accepted denials/no-op response
shape; trusted grant storage/fencing; calendar/mapping/profile authorities; cash-cap
precision and competition limits; zero-fee policy approval; cutoff/deadline and clock
margin/receipt protocol; post-opening invalidation and ambiguous-commit resolution;
over-cap rejection/FILLED extension; source rights/cohort correction; migration
operator process, durability profile, backup/RPO/RTO and fractional/action handling.
No source vendor, operating duration or new production dependency is approved here.

README was inspected and is unchanged. It still presents v1.2 and an early roadmap;
some research sections say agents are not connected, reflecting older milestones.
A separately authorized update should link the current research/evidence selection
and three-agent orchestration, Phase 8.2 virtual accounting, Phase 8.3b price/calendar
contracts, Phase 8.3c pure lifecycle and this **proposed** reservation design. State
that persistence/reservations remain unimplemented, distinguish owner bookkeeping
and read-only broker commands from isolated Beta work, and list authentication,
trusted execution, settlement and closed-Beta roadmap gaps. Do not claim deployable
Paper Trading, rename Project MAGI or imply MOIRAI branding approval.

Baseline branch/HEAD/clean tree/empty staging and target-file absence were verified.
The five architecture guides, seven requested virtual source/schema files, directly
relevant tests and README were reviewed. Twenty unchanged targeted accounting tests
were run to verify current reuse claims for basis/fees, scoped idempotency, clocks,
reopen/replay, concurrent spending/overselling/duplicates, rollback, busy handling,
schema rejection and immutable/FK history: **20 passed**. Guards recorded zero
network attempts, credential access, production DB access or broker imports. Only
isolated temporary test databases were used (161 connections); all were removed.
No Toss fixtures or live broker clients ran. Full regression was not rerun for this
documentation-only task; the prior 1,503-test regression is historical evidence,
not a claim that any proposed feature has been tested.

Only this document is added. No code/test/schema/dependency/README change, staging,
commit or push is authorized. Final validation checks its complete content, local
links, whitespace and secret patterns; unchanged tracked tree/HEAD/staging; `.env`
metadata without reading it; database absence; and workflow-owned caffeinate cleanup.
Recommended first implementation task is Package A with exact files and tests
separately approved, keeping admission, reservations and settlement disabled.
