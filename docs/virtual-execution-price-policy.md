# Phase 8.3a — Trusted execution price and virtual fill policy design

Status: **PROPOSED — design ready for review; no execution implementation or provider approval.**
Design date: 2026-10-24 (America/Los_Angeles).
Reviewed branch/HEAD: `main`, `58af8c8883aa4d17b00693ba74154f3b9249f5e3`.
This document is the only Phase 8.3a repository change. It authorizes no future
source, test, schema, dependency, deployment or README changes by itself.

## 1. Executive summary and status vocabulary

Recommend a conditional **next eligible regular-session official opening** policy
for whole-share, long-only USD simulation. Require a verified primary-listing
exchange opening event, explicit unadjusted price, finality, stable source revision,
calendar identity and licensed immutable audit evidence. No approved execution
provider or calendar authority exists in the repository today. Failure to obtain
them blocks simulated execution; it does not justify substituting another price.

Propose a five-minute pre-open freeze, full-quantity fills subject to a reserved BUY
spend cap, exact SELL share reservations, zero execution fees, and no automatic
rollover. These are simulation rules for review, not exchange rules or guarantees
of executable real-world prices. Delay settlement until evidence is verified.

Initial context: about 10 OCC investment-club members plus 3–5 invited friends,
three fixed teams, USD 100,000 initial virtual cash per team, and a 6–8 week
competition. Meetings approximately every two weeks at 17:00 America/Los_Angeles
motivate a future-opening policy; they do not set exchange hours or fill timestamps.
All invitees receive equal research/report capabilities. Team trading requires
future authenticated membership. Friends without a team have no implied personal
portfolio; that product decision remains open.

This is an **internal accounting/execution design, not a deployable Paper Trading
application**. The existing synthetic fill boundary proves neither authentication
nor trusted price provenance. No public endpoint may expose it.

| Label | Meaning in this document |
| --- | --- |
| IMPLEMENTED | Present in the inspected code at the reviewed HEAD; limited to its current contract |
| PROPOSED | A concrete future rule or implementation requirement awaiting approval |
| CONDITIONAL | A recommendation valid only after specified dependencies/gates pass |
| UNVERIFIED | External semantics, source quality, licensing or operational behavior not established here |

The [Phase 8.1 architecture](beta-paper-trading-architecture.md) remains the broader
baseline. The [Phase 8.2 core](virtual-accounting-core.md) is the implemented subset.
This design refines the earlier proposal explicitly: ACCEPTED names its proposed
PENDING state; initial execution requires an actual event timestamp; and the price
deadline is separated from recovery of already authorized database settlement.
Those refinements require design approval before implementation.

## 2. Existing implementation and responsibility boundaries

| Component inspected | IMPLEMENTED capability | Execution boundary / necessary future work |
| --- | --- | --- |
| [Virtual models](../magi/virtual/models.py), [accounting](../magi/virtual/accounting.py) | Immutable synthetic fills/events, UUID identities, USD/Decimal bounds, weighted-average basis, cash and realized-P&L replay | No order, reservation, price-evidence, membership or execution-authorization contract; reuse arithmetic, not synthetic provenance as proof |
| [Virtual storage](../magi/virtual/storage.py), [schema](../magi/virtual/schema.sql) | Explicit private virtual file, exact schema validation, foreign keys, one-time funding, durable key/fill uniqueness, locked writes and immutable history | Each public operation owns its connection/commit; schema rejects additional objects; no atomic transaction spanning future orders/reservations; see section 10 |
| [Market models](../magi/market/models.py) | Frozen Quote/Candle/CandleSeries/FXRate, provider and instrument labels, source/fetch timestamps, Decimal fields | No exchange MIC, official-event definition, finality, adjustment/revision lineage or execution eligibility; frozen dataclass does not itself certify source truth |
| [MarketDataService](../magi/market/service.py) | Injected provider, bounded in-memory cache; quote TTL 15 seconds, daily candles 300 seconds, minute candles 15 seconds, FX 30 seconds; stale fallback with coded error | Estimate cache is not immutable execution evidence. Quote source age over 60 seconds or missing timestamp is stale; future quote/fetch timestamps are not explicitly rejected |
| [Toss market adapter](../magi/market/toss.py), [session](../magi/toss.py) | US/KR quote normalization, currency matching, adjusted daily/minute candles, pagination and directional FX; synthetic fixtures test wire behavior | Only concrete market provider found. Depends on TossSession and optional owner `.env` loading; excluded from the future execution runtime regardless of being read-only |
| [Market valuation](../magi/market/valuation.py), [portfolio valuation](../magi/portfolio_valuation.py) | Read-only owner-position estimates, stale/unavailable markers, quote/FX provenance; optional broker reconciliation in the latter | Owner-account scope and broker imports prevent direct reuse as a beta execution service; price-role labels do not grant execution authority |
| [Broker interface](../magi/broker/base.py) | Read-only accounts, holdings, summaries; unsupported cash balances are distinct from zero | No execution-price authority and no permitted beta broker dependency |
| [AnalysisResult](../magi/analysis/models.py) | Research result with `MARKET_PROVENANCE_DEFERRED` warning | Research/model evidence, news, recommendations and confidence cannot supply or authorize a fill price |

Toss normalization uses `lastPrice`; optional Quote.open is not populated by that
adapter. Candles explicitly request `adjusted=true`. The local
[market-data guide](market-data.md) describes daily timestamps as local midnight
and minute timestamps as candle-end times; these are existing documentation claims,
not independently reverified provider guarantees. Neither can be relabeled as the
official opening auction event. Symbol + country market + currency does not resolve
listing/exchange ambiguity. Kiwoom is mentioned as future work, not implemented.

Six distinct future responsibilities:

1. **Accounting:** deterministic arithmetic and posted ledger reconstruction only.
2. **Price acquisition:** an independently scoped collector normalizes approved
   source records; it cannot accept orders or write money.
3. **Order acceptance:** authenticated server resolves team scope, validates intent,
   assigns session and reserves resources atomically; it never accepts fill prices.
4. **Scheduling:** calendar-driven freeze, evidence deadlines and recovery from
   committed state; it cannot invent sessions or change an order's target.
5. **Fill authorization:** validates frozen commitment, canonical evidence, safety
   status and reservations, then permits exactly one internal settlement.
6. **Settlement:** one database transaction posts the event, releases reservations,
   records the fill/evidence link and makes the order terminal.

Observed gap register, with no changes made here:

- `MarketDataService._freshness`: future Quote.timestamp/fetched_at can avoid a stale
  flag. Do not reuse it as an execution validator. The smallest future execution
  correction is independent timestamp bounds in the new price contract. Any change
  to owner estimate behavior requires a separately scoped patch and focused tests.
- Virtual storage's private transaction and exact-schema contract, plus its timestamp
  equality constraint, intentionally serve synthetic accounting. They are integration
  blockers, not evidence that live-price settlement is already supported.
- No new material accounting defect was identified by this source review. Existing
  tests and prior regression are evidence for the current core, not future execution.

## 3. Execution-policy alternatives

All alternatives need independently approved data, rights and server authorization.
This comparison concerns simulation fairness, not a promise that an actual order
would fill at the same price or volume.

| Criterion | A: next eligible official open | B: next eligible official close | C: contemporaneous trusted quote |
| --- | --- | --- | --- |
| Participant fairness | One canonical opening record for all orders in an instrument/session; commitment before the event | Common closing record; fair only with a common advance lock | Request latency and feed access can advantage faster participants; buy/sell quote-side policy needed |
| Look-ahead | Future event, no preceding close, no post-freeze cancellation | Must lock before a declared cutoff; recommend pre-open lock for a fair comparison, not arbitrary near-close cancellation | Delayed quotes permit trading on already-known newer prices; last trade is not a contemporaneous executable quote |
| Data availability | Official primary-exchange opening event and finality may be difficult to license/obtain | Daily close may be easier to obtain, but adjusted or generic OHLC close is still insufficient | Requires verified real-time entitlement, bid/ask or explicit trade-price methodology, delay and sequence handling |
| Manipulation | Clients choose quantity/cap only; common source and immutable evidence reduce price selection | Same protections; no client choice among closing definitions | Stale, crossed, illiquid or out-of-order quotes require substantially more controls |
| Sessions | Next opening handles after-hours decisions; no reopening/extended-session substitution | Early closes and closing-auction definitions matter; commitment waits through a session | Live tradability, session transitions and halts must be monitored continuously |
| Timestamp precision | Actual event time plus exact session/calendar identity | Actual closing event plus session identity | Fine event/receipt ordering, clock synchronization and bounded transmission latency essential |
| Execution delay | Wait until next eligible open, then source finality/publication | Usually longer after an evening meeting: next eligible close and finality | Short apparent delay only with a genuinely current approved feed |
| Provenance | Official, unadjusted, finalized opening record/revision | Official, unadjusted, finalized closing record/revision | Quote condition, side, venue/consolidation, sizes, event sequence and freshness |
| Operational complexity | Bounded session collection and deterministic settlement | Similar batch operations, longer reservations | Continuous availability, quote validation, adversarial latency cases and market microstructure rules |
| Corporate actions | Validate action state before the target open | Must account for actions/status changes through the target close | Immediate action/status synchronization required |
| Beta usability | Clear next-session expectation; opening gaps can reject BUY caps | Long waiting period and capital lock for a biweekly club | Responsive experience, but misleading if based on delayed data |
| Tests | Calendar, cutoff, opening definition, gaps, finality, revision/recovery | Add early-close/close cutoff and longer-lived commitments | Add quote sequencing, bid/ask/spread, latency races, stale-price exploitation and continuous halt checks |

**CONDITIONAL recommendation: A.** It fits the meeting cadence and small number of
teams with less operational complexity than C. B is a separate policy candidate if
opening evidence proves unavailable, requiring explicit review before competition;
it is never an automatic outage fallback. Failure to license A means no launch
under A. No vendor is selected by this recommendation.

## 4. Recommended MVP policy and time rules

PROPOSED policy family `next-open-v1`, paired with `virtual-zero-fee-v1` and a pinned
calendar/source-profile version. Freeze these choices before competition activation.

- Long-only, USD, approved US-listed instruments with verified listing identities,
  whole shares, one complete fill per order, fees exactly zero under this policy.
- For each authoritative session s, let O(s) be scheduled regular open, C(s) close,
  and F(s) = O(s) minus five minutes. Target the first eligible session whose F(s)
  is strictly after the order's server acceptance instant. At F(s), that session is
  already unavailable to new orders and cancellations. In-session orders target a
  later opening. Competition start/end bounds also constrain eligible sessions.
- Five minutes is a proposed operational buffer, not a claim that all opening
  information is unknowable. Zero buffer increases clock/commit races; a much
  longer buffer locks teams unnecessarily. Retain five minutes provisionally only
  with measured clock/transaction bounds and a verified non-indicative opening
  dataset. Changing the buffer requires a versioned future rule, not a per-team override.
- BUY specifies whole-share quantity and a maximum total USD debit. Reserve the
  entire cap, fill all shares if rounded gross plus fee fits, otherwise reject all.
  This is a solvency guard for one scheduled event, not a standing limit order.
  SELL reserves exact existing shares. No partial fills or pending-sale financing.
- Cancel only before F(s), without changing the original record. Amendments are
  cancel plus a new request; a new order receives a newly computed target session.
- Missing verified price never rolls an existing order to another session.

Store server request-receipt time only as telemetry. `accepted_at` is server-owned and tied
to the serialized admission decision, not the browser clock or arrival at a queue.
Admission must prove a durable pre-cutoff commitment; a request merely received
before cutoff but processed later targets a later session. Keep acceptance transactions
short and free of network work, check authoritative time under the write lock, and
use a bounded clock-error/commit margin. If commit timing straddles the cutoff or is
ambiguous after a crash, do not execute that order for the disputed session: retain
the attempt, release/expire safely and require a fresh eligible intent. Do not forge
an earlier accepted_at. The implementation must demonstrate this behavior under
delayed commits; the margin/clock authority is an unresolved operational gate.

For an eligible fill: `accepted_at < F(s) < O(s) <= execution_at <= observed_at <=
validated_at <= posted_at`, subject to the timestamp precision declared by the
source profile. Publication time, if present, must not precede execution or follow
the trusted observation beyond the explicitly approved clock tolerance. No tolerance
permits eligibility before the event. An absent publication time stays absent and
requires an approved alternate finality attestation; it is never invented.

PROPOSED evidence deadline D(s) = C(s) plus two hours, including early-close sessions.
This value is conditional on verified source latency/SLA and operator capacity.
Evidence must be validated and the canonical batch selection durably sealed strictly
before D(s); at D(s) the seal is too late. If no valid seal exists, affected unfilled
orders expire and reservations release. Newly fetched evidence after D(s) cannot
resurrect them, even if its price event was historical.

**Recovery refinement:** D(s) is an evidence-eligibility deadline, not permission to
expire whichever team a slow worker reaches last. A valid pre-deadline seal fixes
the eligible order cohort and price; remaining settlement can resume after D(s)
under the same rule. Reservations remain and affected reporting is provisional
during recovery. No new orders join the cohort. Recovery has bounded attempts per
worker run, alerting and an operator incident procedure; it is not endless polling.
If reliable recovery cannot be completed, pause and review the entire affected
cohort; do not selectively cancel losing unfilled orders. This explicitly refines
the earlier generic close-plus-two-hours expiry wording for crash fairness.

## 5. Trusted price provenance and immutable execution evidence

PROPOSED a separate execution contract; do not add an `is_trusted` boolean to Quote
and treat that as sufficient. A typed object or hash proves neither licensing nor
truth. Trust comes from the approved source path, validation and persisted evidence.

| Required field/group | Meaning and validation |
| --- | --- |
| Identity/version | Internal immutable price-record ID; contract schema, normalization and execution-policy versions |
| Provider/dataset | Approved provider identity, dataset/product ID, fixed endpoint-profile ID and rights-approval reference; no arbitrary URL or credential in evidence |
| Source record | Stable original event/record ID, revision ID, source ordering/finality definition and explicit correction-of references; retain unknown lineage as unknown |
| Instrument | Stable virtual instrument UUID, provider instrument ID/symbol, listing exchange MIC, mapping revision/effective interval; match all fields, not ticker alone |
| Currency/value | USD and positive finite exact Decimal string; retain source precision up to eight decimal places and Phase 8.2 numeric bounds; no binary-float or FX conversion |
| Role/event | EXECUTION / OFFICIAL_REGULAR_OPEN, primary-listing event definition and event-condition code; reject indicative, premarket, consolidated first trade or reopening substitutions |
| Session | Exchange session ID and local date, IANA zone, calendar snapshot/version, scheduled open/close and official calendar authority reference |
| Price timestamp | Actual timezone-aware event timestamp and precision. Initial policy rejects date-only or candle-boundary timestamps; no manufactured 09:30 timestamp. Phase 8.1's conditional session-only option is deferred pending a separate source-specific policy |
| Observation times | `observed_at` is trusted collector retrieval-completion time; also retain validated_at and optional source published_at, with uncertainty/tolerance metadata. A new retrieval does not make an old event eligible |
| Official/finality status | Explicit OFFICIAL and FINAL under the approved profile; PRELIMINARY, INDICATIVE, UNKNOWN or merely HTTP 200 is insufficient |
| Adjustment/action status | UNADJUSTED; action-status snapshot/reference, mapping continuity and verified trading/halt status for the event; UNKNOWN blocks settlement |
| Eligibility | Validator result/reason codes for this instrument/session and policy, source health/status checks, evidence-deadline qualification; not the general cache `is_stale` flag |
| Immutable evidence | Canonical normalized fields/digest, source references, normalization profile, retrieval record, validation result and durable seal reference; enough permitted evidence for offline replay |

Source-event timestamp freshness is session-specific. An immutable correct opening
record may be hours old when posted and still qualify if observed/validated/sealed
within D(s). A newly retrieved previous-session price, future event, unspecified
revision, stale status/action snapshot or price sealed after D(s) does not qualify.
Profile-specific status freshness and time tolerances must be approved and explicit;
missing configuration fails closed. Do not inherit 15/60-second estimate thresholds.

Four distinct price roles, enforced in future validators:

| Role | Permitted use | Forbidden substitution |
| --- | --- | --- |
| Execution price | Settlement against a frozen order and approved official event | A latest quote or user input cannot replace the chosen event |
| Valuation estimate | Labeled marked holdings; stale/unavailable remains visible | Cannot authorize a fill or definitive ranking when incomplete |
| Historical adjusted price | Research/comparable history under an adjustment methodology | Cannot be applied as an unadjusted share execution price |
| Benchmark price | Separately approved price/total-return performance comparison | Cannot supply execution or silently mix dividend adjustments into holdings |

### Canonical selection and corrections

Before any settlement, the price collector supplies validated immutable evidence.
An internal execution coordinator creates a **single canonical seal** for
`(competition price-policy version, stable instrument, exchange, session,
OFFICIAL_REGULAR_OPEN)`, shared across all teams. It persists the order cohort and
a stable per-portfolio processing order with the seal. The collector has no order
or reservation write authority. No per-team provider queries.

The source profile must identify an immutable original finalized official opening
record, independently of response order, collection time and which worker asks
first. Fetching an endpoint that supplies only an ambiguously revised current value
is insufficient. Select that identified original record, never first-N/first-seen,
latest-by-retrieval, cheapest/best price, or the team's preferred source. Require
source revision/conflict checks defined by the approved profile before sealing.
Equivalent duplicate records are harmless; conflicting originals or a known
pre-seal correction block the instrument/session cohort pending review, with no
automatic corrected-price choice. Without a qualified seal before D(s), expire it.

Once sealed, its evidence and price cannot change. A later correction is a new
record linked to the original, triggers a cohort safety hold and provisional
reporting, and never rewrites already posted fills. Pending affected settlement
pauses too. Resolution needs a versioned, reviewed cohort-wide correction plan and
preserved original history. It must account for subsequent trades and cash/basis
effects; an amendment cannot simply force negative cash. No correction writer is
implemented or authorized here. If the source cannot distinguish original/final
records and revisions reliably, it is unsuitable for this policy.

Retain normalized evidence, allowed source identifiers and digests, not raw HTTP
bodies, model responses, API tokens, authorization headers or credential-bearing
URLs. A digest without sufficient retained normalized evidence is inadequate.
Rights must permit competition duration plus the proposed 90-day review period,
backups, participant display and derived reports. If rights prohibit necessary
audit retention, reject the source or obtain approval to revise the product.

## 6. Provider suitability and outstanding data rights

These are code-inspection classifications, not a live provider evaluation. No
external documentation, endpoints or terms were fetched in Phase 8.3a.

| Requirement | Classification | Evidence / decision |
| --- | --- | --- |
| Decimal quote/OHLC normalization and aware timestamps | SUPPORTED BY EXISTING CODE | Market models/adapters and synthetic tests; does not establish official-event truth |
| Currency validation and explicit US/KR labels | SUPPORTED BY EXISTING CODE | Toss currency checks; insufficient for exchange-level identity |
| Bounded requests, pagination, cache and coded outage patterns | SUPPORTED BY EXISTING CODE | Useful design references; no broker-dependent execution reuse |
| General source/fetch times and stale estimate fallback | SUPPORTED BY EXISTING CODE | MarketDataService; fallback is unsuitable for settlement |
| Official original opening, finality and correction lineage | MISSING | No fields/methods or source attestation in current contract |
| Stable listing identity, MIC, session and action metadata | MISSING | No approved instrument-master/calendar/action provider integration |
| Explicit unadjusted official execution evidence | MISSING | Existing candle requests use adjusted data; Quote.open is unpopulated by Toss |
| Immutable canonical evidence, retention and batch selection | MISSING | Existing market cache is in-memory and nonauthoritative |
| Reuse of provider-neutral normalization/error ideas | CONDITIONAL | New isolated contracts must establish stricter semantics; do not widen Toss interfaces |
| Any independent source's exact opening event, latency, availability and revisions | REQUIRES EXTERNAL VERIFICATION | Vendor documentation, sample metadata and separately authorized controlled verification required |
| Calendar coverage, unscheduled closures and instrument status | REQUIRES EXTERNAL VERIFICATION | No selected authority or freshness/update SLA |
| Internal/non-display use, commercial/student eligibility and beta display rights | REQUIRES EXTERNAL VERIFICATION | No assumption that invited friends/club members or noncommercial use are exempt |
| Redistribution, derived equity/rankings, immutable retention and backups | REQUIRES EXTERNAL VERIFICATION | Terms must cover the entire proposed use and review period |

The present Toss path is **excluded**, not a conditionally approved beta provider:
it violates the required runtime dependency/credential boundary. Its read-only
nature does not cure that. No SEC/OpenDART/Marketaux/news/research source is a price
substitute. An independently scoped data vendor or exchange-licensed dataset is
only a candidate category; no named vendor is approved here.

Future approval dossier must record dataset/event definition, listing coverage,
normal/open versus delayed/reopening behavior, finality/revision IDs, publication
and historical availability, action/calendar/status dependencies, authentication
scope, request limits/cost, outage SLA, timestamp precision and every data right.
Use dedicated beta credentials and fixed market-data-only endpoint profiles. Source
terms and functionality may change; review them under separately authorized work.

## 7. Exchange calendar, clocks and exceptional sessions

PROPOSED a versioned calendar contract sourced from a verified authority for each
eligible listing exchange, with session dates, IANA exchange zone, aware UTC
open/close, holidays, early closes, exceptional closures, revision/publication time,
coverage horizon and integrity reference. Record the snapshot used at acceptance.
No simple weekday check, fixed UTC offset, manually invented holiday list or meeting
timezone conversion can determine a trading session. A calendar library can normalize
data but is not, by itself, proof of authority or timely exceptional updates.

- Weekends/holidays are skipped only when the authoritative calendar marks them
  closed. Missing coverage is SESSION_UNAVAILABLE, not an assumed open day.
- DST conversion uses the recorded IANA zone and tested timezone-data version.
  Pacific meeting times and exchange-local dates stay distinct. Persist UTC instants
  plus session identity; never derive a session by UTC date alone.
- Early closes affect C(s) and D(s); they do not imply a different open unless the
  calendar explicitly says so. Session start/end inputs are never hardcoded.
- Unknown/outdated calendar/status blocks new admission; unavailable safety data
  prevents sealing/settlement until verified. Refresh policy and maximum status age
  must be approved before live use; a cached calendar is not proof of no new closure.
- A newly confirmed closure or schedule revision invalidating the frozen target
  causes uniform system expiry for affected unfilled orders, with reservation release
  and recorded reason. No automatic retargeting or reopening of cancellation rights.
  Append revisions; never overwrite the calendar snapshot used for earlier decisions.
  If any affected fill already posted, use a cohort incident hold and reviewed
  append-only resolution instead of expiring only the remaining teams' orders.
- Calendar openness does not establish instrument tradability. Halts/suspensions
  require a separate verified status source. A confirmed halt at the opening or a
  delayed/reopening event makes that instrument/session ineligible under initial
  policy. Expire its affected orders; do not substitute a later first trade.
- A halt after an otherwise verified normal official opening does not retroactively
  turn that event into a different price. Safety/action review may pause processing;
  release requires proof that the original event remains eligible, never a new quote.
- Missing/unknown opening status waits only until D(s); no price or timestamp is
  fabricated. Source-profile definitions must distinguish ordinary auction timestamp
  precision from a delayed opening; unsupported ambiguity blocks the source.

Clock service health and measured skew bounds are admission/settlement gates.
Clock regression or uncertainty spanning a cutoff pauses affected operations.
Monotonic time can bound retry duration; it cannot supply an exchange event date.

## 8. Minimal order lifecycle and admission

PROPOSED durable states: **ACCEPTED, FROZEN, AWAITING_PRICE, FILLED, REJECTED,
CANCELLED, EXPIRED**. SUBMITTED is request receipt telemetry, not a durable promise
of funds or session eligibility. ACCEPTED replaces the Phase 8.1 term PENDING.
Safety holds are separately audited competition/instrument/cohort flags, not an
extra state that silently grants cancellation rights.

| From | To | Preconditions / atomic effects |
| --- | --- | --- |
| Valid authorized new request | ACCEPTED | Active scope/rules, known eligible session, before cutoff, funds/shares available; record immutable intent, acceptance sequence, reservation and key outcome together |
| Valid authorized new request | REJECTED | Business rejection such as insufficient resources; durable reason/key outcome, no reservation |
| ACCEPTED | CANCELLED | Current authorized team trader, time strictly before F(s); audit actor and release reservation together |
| ACCEPTED | FROZEN | At or after F(s); seal immutable payload/session/rule/calendar/authorization-at-commit; no member cancellation |
| FROZEN | AWAITING_PRICE | Event due but no qualified canonical seal yet; reservation remains, bounded same-session retries |
| FROZEN or AWAITING_PRICE | FILLED | Valid canonical seal/cohort, authorization and reservation checks, whole-quantity price within cap; one atomic settlement |
| FROZEN or AWAITING_PRICE | REJECTED | Deterministic execution rejection such as price exceeding cap; reason and full reservation release, no fill |
| Any nonterminal state | EXPIRED | Objective target/session invalidation or missing qualified seal at D(s); release once, no rollover; post-seal incident resolution follows cohort review rules |

An ACCEPTED row left by a delayed freeze worker is already logically frozen at F(s).
Cancellation/admission enforce time directly under the transaction lock; worker
lateness cannot extend rights. Before processing its price, persist the missing
freeze transition. Terminal states never reopen. Rejection, cancellation and expiry
retain immutable intent and audit; a later retry cannot turn them into a new order.

Unauthorized/invalid-shape requests create no business order, key outcome or
reservation, only bounded security telemetry. An authorized business rejection
retains its idempotent result. A retry still requires current access before returning
stored information; authorization cannot be bypassed by knowing a key.

Future request permits side, eligible instrument reference, integer quantity, BUY
total-debit cap, scoped idempotency key and optional authorized rationale revision.
Reject unknown fields, user-specified role/team ownership, price, fee, execution
timestamp, target session, provider URL or evidence ID. Server computes those.
Displayed preview is advisory; acceptance recomputes and returns the authoritative
target. No personal portfolio or extra funding is implied by joining a team.

Member departure/permission removal cancels their unfrozen orders atomically with
reservation release. Frozen orders remain team commitments under the recorded
authorization-at-commit; departure cannot cancel after observing prices. Settlement
checks current team/competition safety status and the valid frozen authorization,
not an expired browser session. Emergency suspension applies an audited common rule,
not a selective price-aware cancellation. Team membership is not implemented yet.

## 9. Cash/share reservations and exact invariants

PROPOSED reservations are durable obligations, not posted trades. For portfolio p,
instrument i, let C be posted cash, R the sum of outstanding BUY caps, Q_i posted
shares and S_i the sum of outstanding SELL quantities:

`C >= 0; 0 <= R <= C; available_cash = C - R`

`Q_i >= 0; 0 <= S_i <= Q_i; available_shares_i = Q_i - S_i`.

No fees, costs or share positions change on reservation. Reservations are not
subtracted twice from equity. Reconstruct R and S from committed reservation audit;
materialized projections must equal that reconstruction.

- BUY acceptance of cap r requires positive exact USD cents, `r <= C-R`; atomically
  add r to R with the order. Do not reserve against pending SELL proceeds, predicted
  prices, future funding, borrowing or another order's unused cap.
- SELL acceptance of q requires `q <= Q_i-S_i`; reserve q. Pending BUY shares cannot
  fund SELL reservations. Multiple pending orders compete only for current availability.
- For a BUY fill with debit d, require `d <= r`, then in one transaction set
  `C' = C-d`, `R' = R-r`, and update Q/B by the existing arithmetic. Thus
  `C'-R' = (C-R)+(r-d) >= 0`. Release unused cap immediately with the fill.
- For a SELL fill q, require its reservation q and `Q_i >= q`; atomically set
  `Q_i' = Q_i-q`, `S_i' = S_i-q`, and `C' = C+gross-fee`. Initial fees are zero;
  future fee changes require a separately versioned solvency check.
- Cancellation, rejection or expiry releases only that order's reservation once.
  A failed database transaction releases nothing; retry uses the same order/key.
- If the opening gaps above a BUY cap, reject all with PRICE_EXCEEDS_RESERVED_CASH;
  do not shrink quantity, borrow another order's reservation or spend newly posted
  proceeds to rescue it. A new decision can target a later session after funds post.
- Recheck global invariants inside every mutation. Unexpected insufficient total
  cash/shares despite valid reservations is an integrity incident: roll back and
  pause the affected portfolio, not a fabricated balance repair.

Example (synthetic): C=100000.00, reserve BUY caps 60000.00 and 30000.00; available
cash=10000.00, so a third 20000.00 cap fails. If the first fills for 55000.00,
C=45000.00, R=30000.00, available=15000.00. Cancelling the second returns R to zero
without changing C or creating a ledger BUY/SELL. No cash is double-counted.

## 10. Fill-accounting integration and narrowly necessary future changes

**The Phase 8.2 public interface cannot support the complete policy unchanged.**
Wrapping `apply_synthetic_fill` in another service transaction is unsafe: it opens
its own SQLite connection and commits independently. Separate order and ledger
databases would also lose the required atomic boundary. Merely writing an outbox
does not make reservation release, order finality and ledger effects atomic.

Recommend one dedicated beta SQLite transaction domain for orders, reservations,
price evidence, settlement links and virtual ledger events. Network collection and
normalization occur before the transaction. Inside the settlement transaction:

1. Load order under server-derived competition/team/portfolio scope; verify frozen
   commitment, membership evidence at freeze, current safety flags and policy.
2. Resolve prior settlement by order identity before constructing a new fill. If
   already committed, return its stored authorized result without obtaining a new price.
3. Verify canonical price/cohort, instrument/MIC/mapping, USD, finality, unadjusted
   status, session, action/status evidence, timing, reservation and expected sequence.
4. Use the order's durable server-assigned fill UUID and separate settlement key;
   calculate exact full-quantity gross/fee/basis with the existing pure arithmetic.
5. Atomically append the accounting event, immutable settlement/evidence link,
   computed amounts and audit, release the reservation, and mark FILLED. One commit
   or complete rollback. No calls to providers, models, owner services or brokers.

### Two clocks without rewriting the synthetic ledger contract

Current Event.effective_at and recorded_at are always equal, enforced by replay and
SQL. Injecting the old opening time through `clock` would falsify recording time and
may violate monotonic history. Do not do that or globally relax the v1 constraint.

PROPOSED minimal alternative: preserve the v1 accounting event timestamps as posting
times and store verified **execution_at**, precision, price-record ID and policy in
an immutable settlement envelope linked one-to-one to the ledger event. Its
`posted_at` must match the ledger's recorded_at. The envelope is authoritative for
economic execution time; ledger effective_at must never be presented as the official
opening time. Preserve both economic-time and known/posted-time report semantics.
Future reports require the join and must not show late-posted sale proceeds as
available to orders accepted earlier. This is an explicit proposed integration
choice for review; an alternative event-v2 temporal model would require separate
approval and versioned replay, not silent reinterpretation of historical v1 rows.

| Future component/change | Technical necessity / affected contract | Required proof before approval |
| --- | --- | --- |
| New isolated price/calendar/order/reservation/settlement contracts | Current models lack these concepts; use beta identities only | Strict role/provenance/session validation; no owner/broker imports |
| Narrow transaction composition in `magi/virtual/storage.py` | Internal append must participate in the caller's same verified virtual transaction without its own commit; current synthetic API can retain its behavior | Rollback before/after event insert, order update and reservation release; existing 68 virtual tests unchanged and green |
| Explicit virtual schema v2/migration and schema-manifest handling | Exact v1 object map rejects new tables/indexes; add order/key/reservation/evidence/settlement constraints and authenticated scope linkage in the same file | Explicit migration only, no owner migration reuse; v1 rows preserved, rollback on failure, unknown versions rejected; old runtime must fail on v2 |
| New immutable settlement envelope | Link ledger event to order, canonical price, verified execution time, posting time, rules, actor/authorization evidence and computed financial amounts | Unique order/fill/event linkage, foreign-key scope checks, time-role tests and immutable provenance; legacy synthetic events never upgraded to trusted by assertion |
| New execution-only transaction facade | Prevent bypassing reservations/auth via generic direct synthetic writes in deployed runtime | Only authorized settlement reaches financial mutation; direct fill/fund/CLI access excluded; no raw connection offered to public handlers |
| Versioned instrument mapping metadata | Existing immutable symbol row has no MIC/provider ID/effective mapping interval | Stable virtual identity preserved through verified symbol changes; no in-place legacy rename or duplicate holding identity |
| Later typed action/correction support | Current event kinds are only FUNDING/BUY/SELL and cannot represent dividends/splits/restatements faithfully | Separately approved financial-event semantics, reconstruction and review controls; never emulate actions with fabricated trades |

No changes to existing Decimal arithmetic, owner portfolio modules, Toss, research,
AgentResult, AnalysisResult or voting are justified by this policy. No schema or
module change in this table is authorized now. No generic transaction framework,
distributed coordinator or new dependency is assumed necessary for three teams.

## 11. Idempotency, concurrency and recovery ordering

PROPOSED admission key scope `(actor_id, portfolio_id, operation, client_key)` with
a canonical digest of permitted intent fields and immutable rationale references.
Persist the resulting order/rejection with the reservation in the same transaction.
Same key/digest returns the original result after current access checks; changed
digest conflicts. Distinct team members' deliberate orders remain distinct. Do not
use price or headline similarity for deduplication. Cancellation has its own scoped
key and stores an idempotent terminal outcome.

Settlement uses a separate server-generated key namespace in the existing
portfolio-scoped accounting key domain. Assign/store fill ID with the order or an
atomic immutable execution plan; never generate a new fill ID on each worker retry.
Unique order-to-fill and ledger-event-to-settlement constraints prevent a new key
from filling one order twice. Identity scope includes competition/portfolio so no
cross-team replay is possible. Keep keys through competition plus audit retention.

Serialize submission/reservation, cancellation/freeze and membership-change races
under database transactions; never check a cached balance and write later. Worker
leases may reduce duplicate work but uniqueness and transactions are the correctness
boundary. Serialize accepted order outcomes by target session then portfolio
acceptance sequence. A portfolio's earlier unresolved session blocks later-session
settlement; safe local expiry/rejection can terminate earlier orders without prices.
No same-batch netting or spending pending proceeds. Canonical prices and admission
reservations make worker order irrelevant to fill price and resource entitlement.

After crash, reconcile committed orders/reservations/settlements and ledger sequence.
Before-commit crash leaves no financial effect; after-commit response loss returns
the stored result; do not fetch a replacement price or generate a new key. Sealed
pre-deadline cohorts resume without changing prices even after D(s), subject to
revision/action safety holds. Unsealed cohorts expire at D(s). Corrupt/missing
evidence or mismatched reservation replay freezes the portfolio and reporting.
Do not infer missing state from browser history, model output or current balances.

Restore only beta-owned consistent backups with network disabled; reconcile ledger,
orders, reservations, canonical seals and keys together. Missing acknowledged
post-checkpoint operations are an incident requiring reviewed recovery, not permission
to reuse their keys or fabricate trades. Operational recovery targets remain subject
to the broader architecture's approval; this document does not implement backups.

## 12. Corporate actions and trading restrictions

An approved small universe reduces operational scope but cannot eliminate actions
during 6–8 weeks. Require verified action/status coverage, effective boundaries,
source revisions and a designated review procedure before launch. UNKNOWN action
state blocks execution and definitive equity comparisons.

| Event | Orders | Holdings/equity and release condition |
| --- | --- | --- |
| Forward split | Before freeze cancel affected pending orders under a recorded system rule; frozen affected orders receive a safety hold and expire for that target if unresolved. Do not auto-scale an old order | Future typed action applies exact ratio, preserves basis and records evidence. No adjusted-price workaround |
| Reverse split / fractional entitlement | Reject new orders and hold/expire pending affected orders; do not round user entitlement | Reviewed fractional/cash-in-lieu treatment required; whole-share MVP does not authorize discarding fractions |
| Cash dividend | Ordinary verified dividend need not cancel orders if entitlement/trading status is supported; otherwise safety hold prevents inconsistent execution | Entitlement and receivable/payment processing must reflect verified ex-date rules and execution-time holdings, not posting time or current shares. No fabricated funding event. Rankings provisional until supported |
| Halt / suspension | Halt at open or delayed/reopening event: no initial-policy fill; expire uniformly. Unknown status waits only to D(s) | Keep positions; missing valuation is not zero. Verified eligible normal opening before a later halt may be used only after safety review |
| Merger / spinoff | Block new and freeze affected commitments for reviewed expiry/resolution | Verified consideration, conversion, cash and basis treatment; current BUY/SELL core cannot represent this generally |
| Symbol/exchange change | Hold while mapping continuity is unknown; no arbitrary ticker substitution | Preserve stable instrument/basis, version source/listing mapping; conflicting identity needs review |
| Delisting | Reject new orders; expire unfilled commitments under common reason | Retain holdings, mark value unavailable; neither last quote nor zero is an automatic settlement value |

An opening event and action effective boundary must agree under the source profile.
Backfilled fills may change dividend entitlement; reports must distinguish later
knowledge and recompute only through audited versioned resolutions. No arbitrary
balance edits, repeated initial funding or fake BUY/SELL events can implement actions.
If an invalidating action/status is learned after some cohort fills posted, hold
remaining settlement and review the whole cohort; do not apply a cancellation or
expiry rule only to those the worker has not reached. Preserve all original fills.
Typed action support and material correction review are later dependencies. If
operator staffing, data or supported entitlement handling is absent, real competition
launch remains blocked rather than silently ignoring economically material events.

## 13. Threat model and server-owned trust boundaries

Untrusted parties include browser clients, shared reports/model output, supplied
identifiers and network payloads. A licensed provider is an input requiring validation,
not blanket trust in every field. The server operator is trusted to administer the
beta; ordinary users have no host/database access. Immutable SQL history is not a
claim of protection from a malicious database administrator.

Future beta deployment must have separate filesystem, database and credential scope;
no owner `.env`, home directory, production `magi.db`, brokerage mounts or Toss
dependencies. Settlement has no network/provider capability. A separate approved
collector has market-data-only credentials and fixed HTTPS endpoints with certificate/
hostname verification, bounded responses/timeouts, no arbitrary redirects or URL input.
Those controls are requirements, not implemented network functionality in this phase.

Server derives actor/team/portfolio from authenticated grants, validates them on all
reads and mutations, and rechecks transaction-time authorization. Unknown/foreign
resource IDs do not disclose team balances, orders or key outcomes. Apply future
CSRF/session/revocation controls equally to direct requests and UI actions. The
auth facade must fail closed when missing, never default to a developer/team admin.

Provenance IDs, hashes, AnalysisResult fingerprints, `confirmed=True`, a private
Python method name and knowledge of an order UUID are not capabilities. Only the
internal authorized service may resolve a sealed price and mutate its scoped order.
No raw-price/fill endpoint, CLI gateway, generic SQL or caller-provided provider URL.
Research may be linked as an authorized immutable rationale revision; it cannot
trigger orders, grant permission or replace financial evidence. All invited users
retain equal research features; portfolio grants alone govern team trading.

Safe logs contain bounded reason codes, authorized correlation/order references and
necessary audit metadata. Exclude credentials, raw responses, prompts, full reports,
owner identities and unrestricted exception strings. Audit admin actions and apply
the same safety rule to all affected teams; no retrospective winner selection.

## 14. Failure semantics

| Condition | Deterministic result / recovery |
| --- | --- |
| Provider unavailable, rate-limited, missing/nonfinal official opening | AWAITING_PRICE with reservations; bounded same-session retry. No seal by D(s): EXPIRED, release once |
| Previous-session/stale, future, adjusted, indicative or wrong-event price | Reject candidate with safe PRICE_INVALID reason; no accounting effect. Await a qualifying record only within D(s) |
| Wrong currency or ambiguous instrument/listing | Reject evidence; quarantine mapping/profile and pause affected cohort, never convert USD or guess a symbol |
| Unknown calendar or status | Reject new admission; existing orders remain frozen/awaiting under common hold until valid evidence or objective expiry |
| Confirmed unexpected closure/halt at opening | Before any affected fill: uniform target invalidation/expiry and reservation release. After a fill: cohort incident hold/review, not selective expiry. No automatic rollover |
| Duplicate order request or fill delivery | Original authorized result, one order/fill/event; conflicting body/key is IDEMPOTENCY_CONFLICT |
| Competing pending orders | First serialized valid reservations succeed; insufficient availability rejects later intent, without altering the earlier order |
| BUY price over cap | Whole-order REJECTED, release cap; no partial shares, top-up or alternative price |
| Insufficient shares/cash at admission | REJECTED, no reservation; duplicate returns original rejection |
| Broken reservation invariant at settlement | Roll back and hold affected portfolio; typed integrity error, operator reconciliation required |
| Database busy/write failure before commit | No partial event/state/release; bounded retry with same identities, reservation remains |
| Commit outcome unknown / worker crash | Read committed settlement/key state before retry; exact-once database effect, not guaranteed single delivery |
| Crash after price seal but before all teams settle | Resume sealed cohort after recovery, including after D(s); no selective time-based expiry or price reselection |
| Provider revises price after seal | New correction evidence, cohort safety hold and provisional reports; reviewed append-only resolution, no original overwrite |
| Competition/member authorization change | Apply pre-freeze cancellation versus frozen commitment policy; no cross-team reads or hidden price-aware overrides |
| Backup gap or missing audit evidence | Stop settlement/report finalization, disclose incident and reconcile; never reconstruct guessed fills |

Order state, safety status, reason and retriable flag must be independently clear to
future callers. Outage is not zero price; rejected candidate is not rejected order
until a defined terminal rule fires. Terminal rows never silently return to pending.

## 15. Future offline test matrix and acceptance conditions

No repository tests are added or run for this documentation-only phase. Source
inspection establishes the reuse boundaries; no new runtime behavior is claimed.
Reviewed existing tests include market timestamp/currency/adjusted-candle/cache
cases, valuation stale/unavailable/price-role cases and virtual concurrency,
rollback, replay, filesystem and isolated-import tests. The prior 68 virtual tests
and 1,386-test regression are historical Phase 8.2 evidence, not an 8.3a rerun.

Future tests use synthetic calendars, exact Decimal prices, fake clocks and temporary
beta databases. Deny sockets, owner credential reads, owner DB access and real broker
construction. Any broader regression exception for existing synthetic Toss fixtures
must remain explicit and separate; the new execution package gets no such dependency.

| Category | Objective acceptance condition |
| --- | --- |
| Next-session choice / no look-ahead | Strict F(s) inequality; before/at/after cutoff cases, in-session and after-hours cases target the correct fixture session; no preceding close; queue delay cannot backdate acceptance |
| Holidays/weekends/missing coverage | Only authority-marked sessions selected; unknown coverage rejects without an invented weekday rule |
| DST/time zones | UTC result follows fixture IANA transitions and exchange-local date, independent of Pacific meeting time/host timezone |
| Early close / calendar revisions | Deadline uses actual fixture close; revised invalid target expires uniformly without rollover or altered historical snapshot |
| Clock/commit races | Skew, regression and commit delay across cutoff never yield an executable disputed order; no late cancellation even with delayed freeze worker |
| Halts/suspensions | Unknown status blocks; opening halt/reopening record never becomes normal opening; no fake price or zero valuation |
| Price role and timestamp | Wrong instrument/MIC/currency/session, future/date-only/candle time or research/valuation/benchmark role rejected without ledger effect |
| Finality/adjustment | PRELIMINARY/INDICATIVE/UNKNOWN/ADJUSTED rejected; FINAL alone insufficient without approved official-event/profile identity |
| Provenance/canonical revision | Input/provider-worker order permutations yield identical source record/digest; conflicting originals and known pre-seal corrections block; all teams reference the same seal |
| Deadline boundary | Seal just before D qualifies, at/after D does not; a new retrieval of old evidence cannot resurrect expired orders |
| Order transitions | Every legal edge has required audit/effects; every illegal terminal reopening or client amendment denied |
| Cancellation and membership | Before F cancellation releases once; at/after F denied; revocation races serialize; frozen member departure cannot evade execution |
| Cash reservation | Multiple concurrent caps preserve `0<=R<=C`; pending proceeds/unused foreign caps cannot rescue an order; over-cap gap rejects the whole fill |
| Share reservation | Multiple sells preserve `0<=S<=Q`; pending BUY shares unavailable; cancellation/expiry releases exactly its own reservation |
| Atomic settlement | Fault injection after each insert/update leaves either all fill/event/release/state/audit changes committed or none; no public synthetic-write bypass |
| Scoped idempotency | Same key/same request yields one result through race/restart; changed request conflicts; another actor/portfolio gains no access; one fill per order even with different delivery keys |
| Concurrency/order independence | Duplicate workers and cancel/freeze races preserve invariants; canonical price and entitlement unaffected by worker ordering; no later-session settlement ahead of unresolved earlier session |
| Crash/deadline recovery | Crash before/after seal and commit; qualified pre-D cohort resumes after D with original price/IDs, unsealed cohort expires; no team selectively skipped by processing delay |
| Two-clock reporting | Original v1 timestamp equality preserved; settlement execution_at joins actual source event; cash known at an earlier acceptance time never includes later postings |
| Corporate actions | Split basis conservation, dividend entitlement/payment once, late-posting entitlement review, reverse fractions and unresolved merger/delisting hold; no synthetic funding/trade workaround |
| Corrections and restore | Original fills remain immutable; missing checkpoint actions freeze reconciliation; corrected data cannot silently replace prior prices or create negative balances |
| Authorization and owner isolation | Cross-team/revoked/view-only requests mutate/read nothing; auth missing fails; owner mounts/paths/credentials inaccessible; no Toss/broker import or fallback |
| Schema migration / legacy compatibility | Explicit virtual migration preserves v1 data/replay, rolls back failed DDL, rejects future versions, maintains exact manifest and foreign keys; owner schema untouched |
| Security/retention | No raw secrets/payloads in logs/evidence; normalization deterministic; licensed normalized evidence suffices to reproduce decisions after restart |

All critical tests must pass; no acceptance percentage substitutes for financial
and security invariants. Full existing regression must remain green after any future
authorized production change. Source/licensing approval and deployment isolation
need separate evidence; synthetic tests cannot certify those facts.

## 16. MVP exclusions and limits

Defer partial fills, standing limit orders, advanced order types, intraday execution,
multi-currency, FX trading, margin, shorts, options, transfers, discretionary top-ups,
automatic research-to-trade actions and live broker orders. A BUY debit cap does not
add a limit-order book. Zero-fee fills and official opens are a simplified simulation;
no liquidity, slippage or guaranteed real execution claim is made.

Authentication, team management, public API/UI, hosting, rankings/valuation and
corporate-action implementation are not delivered by this design. Action safety
and complete equity treatment remain launch gates even if their implementations
belong to later milestones. An approved stock universe cannot waive those gates.

## 17. Future implementation sequence and dependencies

Proposed work packages are sequencing guidance, not authorization or new phase names.
Each requires an explicit task specifying files, contracts, tests and any dependency
changes. No interim public endpoint is permitted.

| Order | Work package | Dependencies and exit gate |
| --- | --- | --- |
| A | Pure execution-price/provenance and instrument-mapping contracts | Approve this design and strict status/role definitions; synthetic normalization only, no provider constructors; rejection matrix passes |
| B | Calendar/session contract and deterministic policy | A identity conventions; synthetic authoritative calendars, cutoff/deadline/clock tests; real authority still unverified |
| C | Pure order state machine and canonical cohort rules | A/B; legal transitions, idempotent outcomes, revisions and same-session expiry/recovery semantics; no financial writes |
| D | Reservation accounting and explicit virtual schema/transaction extension | A–C; separately authorized minimal storage changes, migration preservation, concurrency and rollback tests; posted core semantics unchanged |
| E | Internal trusted execution adapter and settlement envelope | D plus canonical evidence/authorization interface; same-transaction order/fill/event/release, two-clock reporting and no synthetic bypass |
| F | Offline end-to-end fill validation | A–E; synthetic source/calendar/status plus fault/concurrency matrix, no network; financial/security invariants pass |
| G | Recovery, audit, correction/restore procedures | Durable artifacts from D/E; crash/deadline/canonical-seal/backup-gap tests; deterministic replay and operational ownership |
| H | Real authentication and team authorization integration | Interface designed before C/D; verified identity/grants/revocation before any user access; transactions enforce ownership and frozen-commitment policy |

External provider/calendar/action/licensing due diligence is a parallel approval
dependency, not implied permission for live requests. An approved source adapter
and separately authorized controlled verification come only after the dossier and
scope are approved. Corporate-action/equity support and recovery operating policy
must pass before competition launch. Source rejection cannot be hidden by changing
the execution algorithm or defaulting to Toss.

## 18. Critical release gates and open decisions

Launch is blocked until all of the following are evidenced:

1. An approved original official-opening dataset, finality/revision semantics, precise
   event timestamps, independently scoped credentials and necessary data rights.
2. Authoritative calendar plus instrument/action/status coverage, update SLA and
   verified clock/cutoff margins; no invented sessions or eligibility.
3. Atomic order/reservation/settlement/ledger storage, explicit safe migration,
   durable unique identities and reconstructed invariants under failure/concurrency.
4. Actual authentication/team authorization, revocation semantics and physical owner
   isolation; broker/Toss runtime exclusion and no public direct-fill capability.
5. Canonical evidence retention, uniform cohort correction/expiry policy and tested
   recovery/restore; qualified pre-deadline batches cannot disadvantage late workers.
6. Supported economically material corporate actions and complete/provisional equity
   rules; no ranking that silently omits dividends, missing values or unresolved actions.
7. Approved competition rules, instrument universe, start/end dates, team grants,
   operational staffing and participant disclosure of simulation limitations.
8. Passing future offline matrix, existing regression and separately authorized
   source/integration verification. No deployment authorization is supplied here.

Review decisions still open: opening versus separately approved closing policy if
no suitable source; five-minute buffer and measured commit margin; close-plus-two-
hours evidence deadline and recovery operating limits; actual source/calendar/status/
action authorities; exact auction/delayed-open definitions; rights/costs/retention;
settlement-envelope versus future event-v2 time model; supported action procedures
and correction reviewers; personal practice for friends versus team-only trading.
The recommendation is concrete but CONDITIONAL; no provider or legal entitlement
is presented as verified. Unresolved launch decisions do not prevent reviewing the
design, but must be resolved before their dependent implementation or release.

## 19. Documentation maintenance and Phase 8.3a validation record

README was reviewed only. A later authorized documentation milestone should:

- Link the implemented Phase 8.2 core and this proposed policy with clear status;
  distinguish historical owner bookkeeping, read-only brokerage and virtual execution.
- Replace stale roadmap/version and early research non-integration statements with
  current Phase 7E/7F links, without implying research can authorize trades or prices.
- Describe beta roadmap, owner/beta isolation, independent credentials and required
  authentication; remove any unsupported inference of deployability or verified data rights.
- Preserve Project MAGI names. MOIRAI remains a proposed public platform brand,
  not approval to rename the repository, packages or identifiers.

Baseline branch, HEAD, clean working tree, empty staging and target-file absence
were checked before work. The complete Phase 8.1 architecture, Phase 8.2 guide,
virtual modules/schema, market/service/provider models, valuation, broker interface,
README and relevant existing tests were inspected as source text. No application
module was imported or initialized for this review. No tests needed rerunning to
establish these unchanged-code boundaries; future behavior remains PROPOSED.

Only this design document is added. No code, tests, schemas, migrations, dependencies,
README or `.env` are changed; no production DB is opened; no provider/broker is
initialized. No external network/API request, staging, commit or push is performed.
File metadata checks verify `.env` stability without reading credentials; database
absence checks use filesystem metadata only. Workflow-owned `caffeinate -i` PID
12120 was maintained during review, then terminated; assertion removal was verified.
No permanent macOS power settings or unrelated processes were changed. No temporary
diagnostic files were created.

Recommended next action: review and approve the policy/time/recovery decisions and
the narrowly scoped integration plan; then separately authorize pure contract and
synthetic calendar work plus any external provider-rights due diligence. Do not
implement execution or begin the next work package during Phase 8.3a.
