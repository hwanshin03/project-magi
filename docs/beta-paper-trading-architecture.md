# Phase 8.1 — Closed Beta Architecture and Virtual Accounting Design

Status: proposed design, ready for review; **not implemented and not beta-ready**.
Design date: 2026-10-24 (America/Los_Angeles).
Reviewed baseline: `main`, `1b6c71680f29640d2cb132c2f80cbbe78725ba98`.
Only this document is authorized by Phase 8.1. No production changes, schemas,
migrations, tests, README edits, deployment, provider calls or commits are authorized.

## 1. Executive decision

Build a separate virtual-only beta application, with its own deployment identity,
storage, secrets and entry point. Never expose the owner's existing CLI or database
as a beta service. Reuse immutable research contracts and tested accounting principles;
do not use historical bookkeeping as a simulator.

Proposed initial scope: approximately 13–15 invited users with equal research/report
capabilities; three competition teams of 3/3/4 members; USD 100,000 once per team;
6–8 weeks; biweekly meetings typically at 17:00 America/Los_Angeles. Exact dates
and memberships require confirmation. Time zones are IANA zones, never fixed UTC
offsets. Team membership grants access to a team's resources, not a superior product tier.

Recommend **team competition only initially**, with all users able to research,
author private reports and explicitly share reports. Nonmembers cannot trade until
assigned to a team; this experience limitation must be disclosed. Personal portfolios
are deferred, not silently included. If hands-on paper trading for every invitee is
a launch requirement, this scope decision must be revisited before implementation.

Recommend **next eligible regular-session official opening price**, conditional on
verification of a suitable independently licensed data source. After-hours decisions
must never receive the preceding close. No provider is selected or verified here.
No acceptable source means no simulated fills and no paper-trading launch.

## 2. Current implementation and reuse boundaries

Classifications below describe how existing components may be used in future work.
They are not authorization to modify them. The actual class is `Portfolio`, not
`PortfolioService`. Sources use repository-relative links for portability.

| Existing component and evidence | Classification | Reason / boundary |
| --- | --- | --- |
| [Portfolio](../magi/portfolio.py), `record_transaction`, transaction dataclasses | DO NOT REUSE FOR BETA | Caller-priced/backdated historical records, owner DB default, broker account fields, no cash or order idempotency. Do not call this writer for virtual fills. |
| `Portfolio._calculate` accounting principles; [tests](../tests/test_portfolio.py) | REUSE WITH ISOLATED ADAPTER | Preserve weighted-average/full-close principles and reference vectors. Implement explicit virtual accounting separately; do not depend on a private function coupled to legacy Transaction/PositionState. Differential tests cover overlapping semantics, with documented cents rounding differences. |
| [Portfolio CLI](../magi/portfolio_cli.py), `main.py` dispatcher | DO NOT REUSE FOR BETA | Commands expose manual price/date and owner-side routes. New beta entry point only; no shell-command gateway. |
| [Account identity](../magi/accounts.py), `PortfolioAccountIdentity` | DO NOT REUSE FOR BETA | Opaque broker references and manual labels are not ownership or authorization. Use independent beta IDs and membership records. |
| [SQLite infrastructure](../magi/storage.py), `Database` | DO NOT REUSE FOR BETA | Defaults initialize owner database/schema and secret checks read local `.env`. Reuse transaction/foreign-key/private-file principles in independently configured beta storage. |
| [Schema](../magi/schema.sql), [v2 migration](../magi/migrations/002_accounts.sql), [v3 migration](../magi/migrations/003_opening_balances.sql) | NOT APPLICABLE | Historical ledger schema is preserved. Separate beta migration history; never migrate/copy owner data. Existing rollback tests are useful examples. |
| [MarketDataService](../magi/market/service.py), Quote/Candle models | REUSE WITH ISOLATED ADAPTER | Useful estimate/cache conventions, but missing mandatory execution session, adjustment/finality and eligibility metadata. New execution price contract must reject stale/incorrect prices independently. |
| [PortfolioValuationService](../magi/portfolio_valuation.py), market valuation helper | DO NOT REUSE FOR BETA | Owner-ledger positions and optional broker reconciliation; totals exclude cash. Reuse formulas and test scenarios in a separate virtual-equity component. |
| [BrokerProvider](../magi/broker/base.py), [TossSession](../magi/toss.py) | DO NOT REUSE FOR BETA | Read-only today, but unnecessary private-account capability and owner credential dependencies. Exclude from virtual execution package and runtime. |
| [Research orchestration](../magi/analysis/orchestrator.py), `analyze` | REUSE WITH ISOLATED ADAPTER | Inject beta-only services/agents, authorize requests and bound cost before invocation. Never share mutable agent instances across users; orchestration temporarily sets historical context. |
| [AnalysisResult](../magi/analysis/models.py), `dumps/loads`; research serialization | REUSE AS-IS | Keep public identities, strict validation, provenance and schema unchanged. Authorization/persistence belongs to an external beta envelope. These serializers do not themselves persist data. |
| [AgentResult](../magi/decision.py), [VotingEngine](../magi/voting.py) | REUSE AS-IS | Preserve unavailable/no-vote and deterministic aggregation semantics. Analysis never submits orders. |
| [AnalysisMemory](../magi/memory.py) | DO NOT REUSE FOR BETA | Owner DB default, no authors/ACLs, legacy voting snapshots rather than complete scoped AnalysisResult artifacts. |
| [Research context](../magi/research/context.py), selected evidence | REUSE WITH ISOLATED ADAPTER | Keep trust separation and bounded context; supply only authorized beta history, never owner's history. |
| User/team/report persistence | REQUIRES SAFE EXTENSION | New beta-owned models/services are needed, outside existing research contracts. No implementation found at baseline. |

Existing tests show decimal positions, fees, partial/full sales, account separation,
atomic oversell rejection, migration rollback, stale/missing valuation and analysis
round trips. They do not establish virtual cash, multiuser security or fill fairness.
See [account-aware ledger limitations](account-aware-portfolio.md),
[memory/portfolio](memory-portfolio.md), [market data](market-data.md),
[broker read-only](broker-read-only.md) and [analysis orchestration](analysis-orchestration.md).

Observed limitations to address only in future beta code:

- `record_transaction` generates a fresh UUID per call; the external-reference index
  is nonunique. Replays can create duplicate business transactions.
- `MarketDataService._freshness` checks old/missing quote timestamps but does not
  explicitly reject future quote timestamps. A dedicated execution validator must.
- Market candles request `adjusted=true`; no adjustment-version contract makes them
  suitable as unadjusted fills. Do not reinterpret them silently.
- `AnalysisResult.warnings` always includes `MARKET_PROVENANCE_DEFERRED`. Research
  artifacts are never fill-price evidence.
- Account omission can query multiple accounts. Labels/hashes are not access checks.

## 3. Isolation architecture and trust boundaries

Recommended minimum deployment: separate beta environment (separate service identity,
filesystem/volume, database and secret scope), one API process and one bounded worker
on a single host initially. Worker roles have distinct capabilities: the virtual
settlement path receives validated normalized prices and database access, not model
or brokerage clients. No microservices or distributed database are required for 15
users. Hosting vendor/framework remain undecided; no deployment configuration changes now.

```text
Untrusted invited browser
        | TLS, authenticated session, CSRF protection, strict request models
        v
Beta API -- server authorization --> beta-only storage
        |                                ^
        +--> bounded research jobs -------+  scoped artifact envelopes
        |       beta research/model credentials only
        +--> virtual order service -------+  reservations, append-only audit
                ^
                | normalized immutable price records
        scheduled price collector
                | approved market-data-only endpoints/credentials
                v
        independently approved price source

Owner environment / magi.db / .env / Toss credentials: NO mount, route or import capability
```

Physical separation protects against mistakenly broad queries and owner-default paths.
Logical authorization is still required among beta users/teams. A separate database
on the owner's workstation alone is insufficient for deployed beta isolation.

Required runtime controls:

1. Do not copy owner database, `.env`, backups or home directory into the beta image.
   Use dedicated beta API keys with spend caps; never inherit the developer environment.
2. A beta storage constructor must require an explicit beta path, reject legacy owner
   paths, and have no fallback to `DEFAULT_DB_PATH`. Missing configuration fails startup.
3. Do not expose `main.py`, portfolio/broker CLI, generic Python evaluation, arbitrary
   file exports or arbitrary URL-fetching endpoints. Broker/Toss modules are excluded
   from the virtual package/import closure. Deny brokerage hosts at egress as defense
   in depth; a domain-only rule is not the primary protection.
4. Research workers may use only approved collector/provider paths and beta secrets.
   The order service has no external request capability. The price collector is not
   a generic proxy and cannot discover accounts or holdings.
5. Database/object-store queries always carry server-derived scope. Every download,
   search, list, report link and background job must reauthorize its audience.
6. Secrets stay server-side. Safe logs contain codes, correlation IDs and necessary
   identifiers, not prompts, raw provider bodies, cookies, credentials or private reports.
7. No private data in shared cache keys, error pages or cross-user model history.
   Artifact fingerprints prove content identity, not permission to read it.

Threat assumptions: the server operator can administer the beta environment; an
ordinary beta user cannot access its host filesystem. Backend compromise is mitigated
by owner isolation and scoped credentials, not claimed impossible. Admin access to
private beta content must be documented and audited; do not promise operator-blind encryption.

## 4. Invite-only authentication and authorization

Proposed roles (not existing roles): `BETA_USER` and `BETA_ADMIN`. Admin is an
operational privilege, not a richer research product. No OWNER role in beta: the
owner's private financial capabilities remain in a separate environment.

Use an established session/identity implementation rather than custom password
cryptography. Provider choice is open. Require verified identity bound to a stable
internal user ID; email is an invitation address, not a mutable authorization key.

Invitation lifecycle: PENDING -> ACCEPTED, EXPIRED or REVOKED. Admin creates a
single-use, randomly generated invitation, stored as a digest, bound to verified
email, expiring after a proposed 72 hours. Consume invitation and create membership
atomically. No public signup or invitation-based automatic admin promotion.

Session proposal: opaque revocable server session; Secure/HttpOnly/SameSite cookie,
CSRF tokens and Origin validation for mutations; rotate on login; 30-minute idle
and 12-hour absolute expiration, subject to review. Logout, user suspension and
credential compromise revoke sessions. Admin requires MFA/recent reauthentication
for invitations, membership changes, funding/lifecycle and recovery actions. Rate-limit
login/invitation redemption; do not leak whether arbitrary email addresses exist.

`PortfolioMembership(user_id, portfolio_id, can_trade, active_from, active_until)`
is the minimum authorization record. Competition team membership is its source;
there are no client-supplied roles. Require one active competition team per user.
Membership history is retained. All current team members can view their team ledger;
`can_trade` is explicitly granted and checked server-side. No team-admin role or
per-order approval workflow initially; admin manages membership. Record the acting
member on every order. Do not allow admin to impersonate a member silently.

| Resource/action | Backend rule |
| --- | --- |
| Research request and private report | Active invited user; own resource; normal quotas apply equally to friends/club |
| Team positions/orders/history | Active membership in that team; guessed IDs do not grant access |
| Submit/cancel pending order | Active membership + can_trade; cancel allowed for any authorized team trader before cutoff, with actor audit |
| Portfolio balance edits/funding/reset | No user API; only validated lifecycle events, admin, reason and idempotency |
| Report sharing | Author explicitly shares a revision with a team or all invited users; no public links initially |
| Report reference on team order | Referenced revision must be readable by the team; private-only content must not be copied silently |
| Membership changes | Admin, audited; cannot self-grant through general profile endpoints |
| Cross-team comparison | Competition aggregate equity/return only, not private holdings, orders or notes |
| Private artifacts by ID | Authorized envelope lookup before content decode/download; inaccessible and absent return the same 404 |

A departing member loses reads and future actions immediately. Their historical
orders remain team property. Unfrozen orders submitted by that member are cancelled
on departure/suspension or removal of trading permission, with reservations released
atomically. Cutoff has legal effect in the application even if the freeze worker
has not yet updated the stored state; a delayed worker cannot extend cancellation. Frozen orders remain
team commitments; departure cannot be used as a post-price cancellation trick.
An emergency admin halt applies a recorded competition-wide rule, never selective
secret cancellation after observing which trades would lose money.

## 5. Conceptual virtual data model

All models below are new, proposed beta concepts. No SQL or migrations are created.
Opaque IDs are generated by the server. Monetary JSON values are decimal strings.

| Concept | Minimum content / ownership |
| --- | --- |
| Competition | ID, rule version, USD initial capital, start/end instants, eligible sessions/instruments, price policy, calendar version, state |
| VirtualPortfolio | ID, competition ID, team owner ID, currency, state, ledger sequence/version |
| PortfolioMembership | User/portfolio IDs, permissions, validity interval, admin actor and reason |
| VirtualCashAccount | Portfolio/USD, posted cash and reserved cash projections, sequence; reconstructible |
| VirtualPosition | Portfolio + stable instrument ID, quantity, reserved quantity, remaining cost basis, realized P&L; reconstructible |
| VirtualOrder | ID, actor, portfolio, instrument, side, integer quantity, BUY spend cap, server acceptance time/sequence, target session, cutoff, state, policy version, rationale references |
| VirtualFill | Unique order ID, price-record ID, quantity, price, gross, fee, cash/basis effects, effective_at, recorded_at, policy version |
| VirtualTransaction | Immutable event ID, portfolio sequence, event type, cash/position deltas, order/fill/action reference, actor, recorded/effective time |
| IdempotencyKey | Actor + portfolio + operation + opaque key, canonical request digest, stored outcome/order ID; unique scope |
| PriceRecord | Immutable normalized price/provenance contract in section 8; unique source revision/session/instrument identity |
| ResearchArtifactEnvelope | Resource ID, owner/audience, original completion time, AnalysisResult artifact ID/selection ID/as_of, validated serialization, retention metadata |
| ReportRevision | Author, report/revision IDs, company/instrument, content, created_at, audience, immutable revision hash |

Events: INITIAL_FUNDING, ORDER_ACCEPTED/RESERVED, CANCELLED/REJECTED/EXPIRED,
FILL, corporate-action adjustments, and explicitly versioned correction events.
Order state and balance projections may update transactionally; historical events
and fills are append-only. Projections are never the only source of truth.
No actual account numbers, broker refs, bank transfers or owner ledger IDs exist here.

Database recommendation: dedicated SQLite on persistent local storage for a single
beta host/API and bounded worker, subject to concurrency/restore tests. Foreign keys,
unique constraints, fixed decimal representation and BEGIN IMMEDIATE protect atomic
writes. Do not put SQLite on a shared network filesystem or run multiple replicas
against copied databases. If hosting requires that model, select a transactional
server database before implementation; retain identical invariants. This recommendation
is not a deployment decision or a change to the owner's SQLite schema.

## 6. Decimal accounting and invariants

Proposed v1 currency is USD only. User orders use positive whole shares of a small
eligible liquid-equity universe. No leverage, shorting, fractional user orders,
options, transfers or withdrawals. Prices must be positive finite Decimal values,
with source precision preserved up to eight decimal places; unsupported precision
fails rather than silently truncating. Reject booleans, binary floats, NaN/infinity,
excessively large values and unknown fields. Store cash and book cost as exact cents;
use a fixed Decimal precision of 80 for intermediate arithmetic and ROUND_HALF_EVEN
for gross/fee/basis allocation to cents. Validate numeric bounds before arithmetic;
proposed limits are 1,000,000 shares per user order, USD 1,000,000 per share and
USD 1,000,000,000,000 for monetary aggregates, with explicit overflow rejection.
All are simulation safety caps subject to approval, not trading recommendations.
Persist the policy version and computed
amounts. A trade whose rounded gross is zero is invalid.

Initial fees: explicitly zero under version `virtual-zero-fee-v1`, not an estimate
of real broker costs. Preserve a fee field for audit; changing fees requires a new
future rule version and tests, never a silent mid-competition change.

Let C = posted cash, R = reserved BUY cash, Q = held shares, S = reserved SELL
shares, B = remaining basis, q = fill quantity, p = accepted price, f = fee and
G = round_cent(q*p).

- Available cash = C - R >= 0. Available shares = Q - S >= 0.
- Funding creates C = 100000.00 once per portfolio/competition, protected by a unique
  INITIAL_FUNDING constraint. An admin retry returns the existing event.
- BUY requires G+f <= the order's reserved spend cap. Atomically C -= G+f,
  Q += q, B += G+f; release the whole reservation. No partial fill.
- SELL requires q <= its reserved shares. Released basis A = B for a full close,
  otherwise round_cent(B*q/Q), bounded to [0,B]. Atomically C += G-f,
  Q -= q, B -= A; realized P&L += G-f-A; release reserved shares.
- Average cost is B/Q for display/calculation, never a rounded value fed back into
  the authoritative basis. Q=0 implies B=0; final close absorbs rounding residue.
- Unrealized P&L = sum(round_cent(Q*valuation_price) - B) for fully valued positions.
- Equity = posted cash + marked holdings + earned unpaid dividend receivables. Reserved
  cash remains part of posted cash; do not subtract it twice from equity.
- For trading-only replay: C + sum(B) = initial funding + cumulative realized P&L.
  Dividends add paid cash/receivables to the corresponding conservation equation;
  splits preserve basis. Every administrative event has a separately checked equation.
- No failed order changes posted cash, holdings or realized P&L. Reservation release
  and rejection audit are the only balance-related changes on failure.

Example: funding 100000.00; buy 10 at 100.00 -> C=99000.00, Q=10, B=1000.00.
Sell 4 at 120.00 -> C=99480.00, Q=6, B=600.00, realized=80.00.
At valuation 110.00, unrealized=60.00 and equity=100140.00.
This is a synthetic design example, not a trade or persisted test record.

No spending proceeds from another pending order: BUY reservations use only current
available cash; SELL reservations use only posted holdings. This prevents processing
order from creating intrabatch purchasing power. Users wanting to reinvest sale
proceeds wait until that sale has posted and submit for a later eligible opening.

## 7. Submission, execution and recovery transaction boundaries

Submission is not a fill. In one short DB transaction: authenticate/authorize via
current user/membership state; validate request shape and resolve an existing
idempotency outcome before checking new-order competition/cutoff eligibility; for a
new request validate competition, instrument and cutoff, then reserve available cash/shares; assign acceptance
sequence and target session; insert order, audit event and idempotent outcome; commit.
Never hold a database write lock while fetching prices or calling models.

For BUY, user specifies desired integer quantity and maximum total USD debit. This
cap reserves funds and protects solvency; it is not a user-supplied fill price or
an intraday limit-order service. At the scheduled opening, fill all shares at the
common trusted opening price if within the cap, otherwise reject the entire order
with PRICE_EXCEEDS_RESERVED_CASH. No partial fill, quote substitution or repricing.
For SELL, reserve exact held quantity. No cross-order netting.

Idempotency scope: (actor, portfolio, operation, key), with a canonical digest of
side, instrument, quantity, spend cap and immutable research/rationale refs. Same key
and digest returns the original outcome; different digest returns IDEMPOTENCY_CONFLICT.
Concurrent identical requests resolve through a unique constraint, not a check-then-
insert race. Never deduplicate different members' intentional orders by similar
payload alone. Show the team pending-order list to reduce accidental human duplicates.
Retain keys for the entire competition plus its audit retention period; authorization
is rechecked before returning any cached result. Expired sessions never bypass auth.

Freeze at cutoff transactionally. Once frozen, payload, target session and cancellation
rights cannot change. Persist authorization-at-commit and rule/calendar versions.
Prepare prices outside DB transactions; choose one validated canonical record per
instrument/session for every portfolio, using deterministic revision/finality rules.
Persist that choice before settlement. Source acceptance must specify a stable
original official-opening record/version; corrections are distinct records. If the
source exposes only an ambiguously revised value, settlement is blocked. Do not let
later teams receive a corrected price while earlier teams retain an original price.
In a short settlement transaction, check
order still eligible/frozen, competition not under a safety hold, no prior fill,
price eligibility, reservation and sequence, then write fill + cash/position effects
+ reservation release + terminal state + audit. Unique order-to-fill constraint
makes restart/retry exactly-once in database effect, not exactly-once worker delivery.

Workers process target sessions in order, then acceptance sequence. Later orders
cannot consume unresolved historical holdings; block later settlement for that
portfolio until earlier session outcomes are terminal. A frozen order can move to
AWAITING_PRICE without becoming cancellable. Retrying it does not choose a new session.
A crashed worker resumes from persistent order state and unique fill identity.

State machine:

- New request -> PENDING (reserved), or terminal REJECTED (no reservation).
- PENDING -> CANCELLED before cutoff; PENDING -> FROZEN at cutoff.
- FROZEN -> FILLED, REJECTED, or AWAITING_PRICE.
- AWAITING_PRICE -> FILLED/REJECTED before the recorded recovery deadline, otherwise
  EXPIRED with reservation release. No automatic rollover.
- Terminal states cannot return to PENDING. Corrections are new audited events.
- Unauthorized/expired-session requests create no order or reservation; only bounded
  security telemetry. Invalid format has no business effect.

## 8. Execution-price policy and market-data contract

All three candidates require independently verified data rights and semantics.
No live price lookup was performed for this design.

| Candidate | Fairness / time rules | Data and operations | Main risks / user experience |
| --- | --- | --- | --- |
| Next regular-session opening | Unknown price at submission; coherent for after-hours meetings; pre-open freeze | Official unadjusted opening price with instrument/session identity and publication/finality status; schedule-aware batch | Opening gap may exceed reservation; auction/open definition and halts must be explicit; fills appear after verified publication |
| Next regular-session closing | Unknown close if locked before session; common price for all | Official unadjusted close, early-close calendar, fixed cutoff | Longer exposure to pending orders; cancellation after observing trading would be unfair; meeting decisions wait through next session |
| Trusted contemporaneous delayed quote | Simple-looking immediate action | Known delay, tradability and event/retrieval clocks, fresh independent quote service | Users may know a newer price, allowing exploitation of delayed quotes; closed-session quotes are not fills; continuous service/freshness complexity |

Recommendation: next eligible regular-session opening, policy `next-open-v1`.
Use the first session whose freeze time is strictly after server acceptance. Proposed
freeze = scheduled open minus five minutes, from a versioned exchange calendar. An
order arriving at/after freeze targets a later session, never that opening. This
also means in-session requests target a future opening. Display target session and
cutoff before confirmation. Server assigns them again at acceptance to resolve races.
No client execution timestamps. Meeting time does not determine a fill time.

An exchange calendar must represent weekends, holidays, early closes, DST and
unscheduled closures. Do not hardcode weekdays or convert Pacific times by a fixed
offset. Exact exchange opening definitions/calendar authority require verification
before Phase 8.3 goes live. All persisted instants are aware UTC, with exchange
session date and IANA zone retained. Date-only prices cannot masquerade as exact
opening timestamps; allow a session-date identifier plus explicit precision only
if the provider contract unambiguously identifies the official opening event.

An order is recorded at acceptance; a fill becomes economically effective at the
verified target opening event and is recorded when processed. Late recording is not
user backdating: accepted_at < freeze_at < effective_at <= recorded_at, and an
immutable pre-event commitment is required. Until posting, UI says pending, never
spendable hypothetical proceeds. Historical reports preserve both economic time and
what the application had actually recorded/known at each time.

PriceRecord minimum fields:

- price_record_id, provider identity, licensed dataset/endpoint identifier, immutable
  provider record/revision identifier, normalized-record digest and validation policy;
- stable internal instrument ID, provider symbol, exchange/MIC, USD currency;
- session ID/local session date, calendar version, event kind OFFICIAL_OPEN,
  market timestamp or explicit session precision, publication timestamp if available,
  retrieved_at and validated_at;
- positive exact Decimal price, adjustment status UNADJUSTED, provider finality,
  trading/suspension status, corrections lineage and provenance reference;
- eligibility/freshness classification for this exact session, not generic cache TTL.

The contract must establish the correct opening definition, regular-session scope,
publication delay, finality and corrections behavior. A newly retrieved old record
is not fresh merely because retrieved_at is recent. A future event/retrieval timestamp,
wrong instrument/exchange/currency, missing event identity, ambiguous adjustment,
nonfinal data or unsupported precision fails eligibility. Canonical record selection
must not depend on which team's worker ran first.

Retain only normalized audit fields and provider record IDs, not raw HTTP bodies,
credentials or complete downloaded datasets. Provider terms must permit retaining
price evidence through competition + proposed 90-day review period, backup use,
derived equity/rankings and display to invited users. Verify historical access,
latency, corporate actions, nonprofessional/student eligibility, redistribution and
cost limits separately. If necessary audit retention is prohibited, reject that
source or revise the product with explicit approval; do not erase needed provenance.
No dependence on owner/Toss credentials is acceptable. Existing Toss adapters are
not the selected beta source.

Execution price, current valuation estimate, historical adjusted series and benchmark
price are four distinct roles. They may come from different approved datasets, but
must have explicit role metadata and cannot be substituted for each other. Valuation
can display a labeled stale estimate; settlement cannot use it. Never use a research
excerpt, model output or AnalysisResult price as a fill input.

| Condition | Deterministic outcome |
| --- | --- |
| Closed market, weekend, holiday | Record/reserve for next eligible calendar session; never preceding close |
| Unknown/unavailable calendar | Reject admission with SESSION_UNAVAILABLE; no guessed date |
| Missing/nonfinal/stale opening record | AWAITING_PRICE; no fill; bounded retries for the same target session |
| Recovery deadline reached | EXPIRED; release reservations; require a fresh user order for a future session |
| Suspended security / opening unavailable | No synthetic price; reject or expire under recorded reason |
| Duplicate submission | Original authorized outcome; conflicting payload rejected |
| Currency/instrument/adjustment mismatch | PRICE_INVALID; no balance change; alert operator |
| Unexpected market closure | Expire affected target-session orders; do not silently move them to another day |
| Provider outage | Pause affected processing; no last-known-price fills; apply same deadline to all teams |

Proposed missing-price deadline: scheduled target-session close plus two hours.
Actual provider publication SLA must be verified to accept that value. Later source
corrections never silently rewrite fills: suspend affected reporting, append an
explicit correction/restatement with provenance after review, and retain old versions.
A correction that would break cash/holdings invariants freezes the affected portfolio
and ranking until a reviewed replay plan is approved; never force negative balances.

## 9. Corporate actions and competition accounting

Eligibility restriction alone is insufficient: actions can arise during 6–8 weeks.
Require authoritative action records and a documented admin review procedure before
launch. No automated generic corporate-action engine is proposed initially.

| Event | Minimum policy |
| --- | --- |
| Cash dividend | Capture entitlement from effective holdings under verified ex-date/session rules; record receivable once at entitlement (not merely declaration), then transfer receivable to cash once on payment date. Selling after entitlement does not remove it. No inferred entitlement from current holdings or payable cash before payment. |
| Forward split | Apply exact ratio to held quantities, preserve total basis, and adjust instrument reference at the effective boundary. Cancel pending affected orders before freeze; already frozen affected orders go to safety hold, not an unadjusted fill. |
| Reverse split / fractional entitlement | Halt affected instrument and mark rankings provisional; verified cash-in-lieu terms or reviewed exact adjustment required. No silent round-down or invented liquidation price. |
| Merger / spinoff | Halt affected trading; manual versioned entitlement/conversion resolution with evidence, independent review and reconstruction tests. |
| Delisting | Block new orders; retain holdings and explicit valuation-unavailable state pending verified settlement/worthlessness terms. Do not assume last quote or zero. |
| Symbol change | Preserve stable instrument identity and basis; add versioned symbol mapping, validate exchange and source continuity. |

Admin cannot submit arbitrary balance numbers. A supported adjustment uses a typed
corporate-action event, unique source action ID, effective time and constrained
formula. Material/manual resolutions require a second designated reviewer before
posting; this is a narrow operational approval, not a new product tier. Until
resolved, publish no definitive ranking for affected comparisons. Disclose and log
all rule applications to competition participants without exposing private research.
If staffing/data cannot support this policy, the competition cannot launch merely
by promising to ignore corporate actions.

Competition states: DRAFT -> SCHEDULED -> ACTIVE -> CLOSED -> FINALIZED, with
PAUSED safety state and audited restoration of prior state. Funding occurs exactly
once at activation. Dates, policy, eligible instruments and benchmark are frozen
before activation. Three independent portfolios start with equal cash and no holdings.
No discretionary deposits/withdrawals or top-ups during the competition.

Late joiner: inherits team visibility/trading permission, not new funding. Leaver:
loses access as section 4 specifies. No trades: equity remains cash (plus any explicit
eligible events), return zero, not an error. Concurrent members: serialize reservation
checks; insufficient funds/shares rejects only the losing request, not another member's
committed order. Identical key returns one order; different keys are distinct intents.

Competition end: reject submissions whose target opening would be after the last
eligible session. Stop new orders at the recorded end; settle only already committed
eligible final-session orders within their deadline. Cancel/expire remaining orders
and release reservations before finalization. Do not liquidate automatically at an
invented closing price. End reporting values holdings at a verified common final
session close, includes paid dividends and outstanding entitlements, and retains
positions. Missing prices/actions keep the competition CLOSED/provisional, not FINALIZED.

Return = (equity / 100000.00 - 1) * 100, with no external cash flows. Rank by complete
unrounded-equivalent cents equity at the same valuation session; equal equity is a
tie, not submission-time priority. Display rounded percentages separately. Missing or
stale estimates may be shown with age, but cannot silently yield an official rank.
Comparison exposes aggregates only. Preserve valuation input IDs and report versions.

Resets never delete history: a prelaunch mistaken setup can be voided with audit;
after activation start a new competition ID and new portfolios, archive the old one.
Only an explicit admin action with reason and new one-time funding can start a reset.

Benchmark: select one fixed approved USD total-return benchmark and start/end rule
before launch, ideally including distributions consistently with team equity. Record
source/method and timestamps. If only a price-return series is licensed, label that
mismatch explicitly and treat comparison as informational; no fabricated benchmark
or mixing adjusted benchmark prices into fills. Benchmark availability is an open
decision, not a claim of current integration.

## 10. Research integration and feature equality

Store existing AnalysisResult serialization in a beta-owned immutable envelope with
owner/audience, server completion time, artifact identity, selection identity, request
as_of, instrument and schema version. `as_of` is an analysis cutoff, not completion
time or a trade timestamp. Content identity is not a permission token or execution ID.

At submission, authorize all references and freeze the report revision, thesis text
revision/hash and analysis envelope ID. Analysis must be completed before order
acceptance and have as_of <= acceptance; the referenced instrument must match or
carry an explicit authorized broader-scope rationale. A later regenerated analysis
gets a new envelope/revision and cannot replace the original rationale. Historical
links retain their original timestamps and citations. Deleting private display access
must not erase team order audit content already explicitly shared to that team;
disclose retention consent before linking. Never silently share a private report.

Keep three separately labeled fields: user-authored opinion, MAGI-generated analysis,
and third-party source evidence. Research failures are shown as unavailable; they
never fabricate votes or block unrelated manual research notes. Analysis cannot
place or approve a virtual order. Existing VotingEngine/AgentResult semantics remain
unchanged; a person submits each order.

| Scope option | Benefit | Added work / limitation |
| --- | --- | --- |
| A: team competition only (recommended first) | One lifecycle, three portfolios, bounded operational load | Friends without a team can research/report but cannot practice trading; team eligibility must be transparent |
| B: personal portfolios + teams | Every invitee can practice; same feature independent of team participation | Personal funding/reset rules, more authorization cases, extra equity/usage/testing; no competition ranking across unequal lifecycles |

Option A is a resource-membership restriction applied equally to all invitees, not
club-only premium access. Do not invent a fourth competition team without approval.
If users require personal practice, authorize Option B explicitly and give identical
personal portfolio capabilities to all users, including club members.

## 11. Proposed API boundaries and request models

Paths are illustrative future interfaces, not implemented endpoints. All payloads
are bounded strict schemas; reject unknown fields. Do not trust user_id, role,
team_id ownership assertions, balances, fill price, execution time or provider URLs
supplied by a client. IDs are resolved under the authenticated principal.

| Boundary | Conceptual request -> response | Authorization / effects |
| --- | --- | --- |
| Invitation/session | Redeem token + verified identity -> session/user; logout -> revoked | Server invitation lifecycle, rate limit, CSRF on mutation |
| POST research requests | instrument ID, bounded question, allowed families -> scoped job ID/status | Active user, cost reservation; backend sets allowed cutoff/config; no arbitrary models/providers |
| GET research artifacts | artifact envelope ID -> authorized result | Owner or explicit audience; same checks on export |
| GET portfolios | authorized scope -> cash/available/reserved, positions, equity status/version | Server-scoped membership; no default all-owner view |
| POST portfolios/{id}/orders | idempotency key, side, instrument, integer quantity, BUY spend cap, rationale revision refs -> order ID/state/target/cutoff | Membership, active competition, reserves atomically; no execution-price field |
| POST orders/{id}/cancel | idempotency key -> cancellation/state | Team trader, before cutoff only; releases reservation atomically |
| GET orders/history | portfolio ID, bounded pagination -> authorized events | Audit actor/time; no foreign-team details |
| POST reports/revisions/share | content, instrument, explicit audience -> immutable revision/visibility record | Author; validate team membership, audience and retention consent |
| GET competition summary | competition ID -> common-time aggregate equity/ranks | Invited participants; no holdings/rationale disclosure |
| Admin setup/membership/lifecycle | typed change, reason, idempotency key -> audit/result | Admin, reauthentication, allowed state transition, no arbitrary SQL/balance edit |

Safe error envelope: code, correlation ID, retriable flag and optional authorized
order ID; never raw provider/SQL exceptions. Codes include AUTH_REQUIRED,
RESOURCE_NOT_FOUND, VALIDATION_FAILED, COMPETITION_CLOSED, TRADING_PAUSED,
INSUFFICIENT_CASH, INSUFFICIENT_SHARES, IDEMPOTENCY_CONFLICT, ORDER_FROZEN,
SESSION_UNAVAILABLE, PRICE_INVALID, PRICE_UNAVAILABLE, PRICE_EXCEEDS_RESERVED_CASH,
STORAGE_UNAVAILABLE and USAGE_LIMIT. Expired auth returns 401; inaccessible resources
return uniform 404; invalid payload 400; state conflicts 409; limits 429; transient
service failure 503. No unauthorized lookup may disclose whether an order exists.

Usage: proposed one active research job per user and two globally, bounded queue,
per-user/day requests, per-team visibility and global daily monetary ceiling. Exact
cost/request amounts require approval before launch. Reserve worst-case bounded
provider cost before work; account for retries; settle measured usage afterward.
Missing usage data retains a conservative charge/reservation until reconciled.
Provider timeout must not cause unbounded automatic retries or duplicate paid jobs.
API keys and quotas belong to the beta deployment, not the owner's environment.

## 12. Failure handling, persistence and recovery

A failed price lookup has no ledger mutation except bounded order-status audit.
Insufficient funds/shares rejects admission without reservation. If price exceeds
a reservation, reject and release it without partial trade. Concurrent membership
revocation/submission and cancellation/freeze must be serialized, not authorized only
in the browser or from a stale cache. Failed DB transactions roll back every event,
reservation and projection change together. Commit succeeded but HTTP response lost:
retry with the same key returns the existing outcome.

Server restart: read committed order states, reconcile reservations, resume only
eligible same-session processing; never reconstruct pending orders from browser state.
Partial research-provider failure preserves existing unavailable semantics and cannot
mutate virtual money. Price outages do not fall back to broker credentials or model
estimates. Admin safety pause blocks new commitments/settlement; record how frozen
orders expire under the same rule for all affected teams.

Separate beta migrations have their own schema/version manifest and explicit beta
path. No auto-initialization of the owner schema, no owner migration reuse, no copying
private records. Migration tests must cover repeat application, failure after DDL,
foreign keys, old-beta row preservation and rejection of unknown future versions.

Proposed recovery objectives: daily encrypted snapshots plus a snapshot after each
settlement batch and before any migration; off-host beta-only backup access, restore
check before launch, target RPO <=24 hours and RTO <=4 hours subject to operator
approval. If those losses are unacceptable, tighten before launch; do not imply zero
loss from local durability. Backup method must be SQLite-consistent, including WAL
if enabled; never a casual copy of a live database file. Backups exclude API secrets.

Restore into an isolated environment with all outbound providers disabled. Verify
schema/integrity/foreign keys, contiguous event sequences, one funding per portfolio,
one fill per order, idempotency keys, reservation reconstruction, cash/basis/quantity
replay and artifact hashes. Reconcile the recovered checkpoint with audit records
before reopening. If acknowledged post-checkpoint actions cannot be recovered,
freeze the competition and disclose the recovery gap; do not silently replay guessed
orders or reopen idempotency keys. Offline restore tests must simulate this case.
Financial corrections require explicit append-only records and report versioning.

## 13. Threat model and required denial tests

| Threat / surface | Enforcement and acceptance evidence |
| --- | --- |
| Cross-user private research via guessed IDs/search/export | Scope every query and download by principal/audience; user B receives no data from A, including cached/queued results |
| Cross-team writes / forged portfolio IDs | Membership + can_trade inside mutation transaction; forged path/body IDs cause no ledger/reservation changes |
| Owner data leakage | No owner mounts, keys or default DB paths; startup rejects owner paths; filesystem/egress/import isolation tests |
| Unauthorized direct API calls | Same auth/CSRF/schema rules as UI; no reliance on hidden buttons or CORS alone |
| Replay / duplicate orders | Unique scoped idempotency + request digest; lost-response and racing retries create one order/fill |
| Concurrent spending or overselling | Atomic reservation/fill transactions and uniqueness; randomized concurrent schedules preserve nonnegative availability |
| Malicious inputs / mass assignment / injection | Bounded strict models, parameterized SQL, escaped report rendering, no raw HTML/script or arbitrary URLs; unexpected fields denied |
| Credential disclosure via errors/logs/model output | Scoped secrets, safe error codes, output validation/redaction and log tests; never raw provider responses |
| Price/time manipulation | No client fill price/date; verified session/provenance/finality, future-date rejection, immutable commitments before price event |
| Post-price cancellation / membership gaming | Cutoff checked against server clock even if freeze worker is late; frozen team orders survive ordinary member departure |
| Broker invocation | No import/call route or credentials in virtual runtime; offline constructor/network canaries fail build tests |
| Prompt injection / unauthorized history | Existing trust-separated context + authorized history envelope; model output never drives orders or ACLs |
| Resource/cost exhaustion | Bounded payloads/jobs, admission reservation and global cost cap; retries included |
| Administrative abuse / correction bias | Typed audited operations, no arbitrary balance endpoint, second review of material resolutions and visible competition report versions |

## 14. Future offline test matrix and acceptance criteria

No new tests are created in this phase. All future suites must use fake clocks,
synthetic normalized data, temporary beta databases, no owner `.env`, and guards
blocking sockets, actual broker constructors and production DB access.

| Test area | Required assertions | Existing reference |
| --- | --- | --- |
| One-time funding | Concurrent/repeated activation produces exactly one 100000.00 funding event per team; no cross-team effect | `test_opening_balances.py` concurrent/duplicate opening patterns, not broker import reuse |
| Cash conservation | Replay cash/basis/realized equations after every event; deposits only via authorized funding | New virtual-core tests required |
| Weighted cost / partial/full sales | Exact cents allocation, remaining basis, zero basis after close, fees and losses | `test_portfolio.py`: weighted-average, partial sell, full close, fractional precision vectors |
| Insufficient cash / oversell | No negative available cash/shares, no partial mutation; pending orders reserve resources | Existing atomic/concurrent oversell tests plus new cash tests |
| Idempotency | Same key same body once; conflict rejected; two members distinct; post-crash retry once | New submission/unique-fill tests |
| Concurrency | Simultaneous buys/sells/cancel/freeze/revocation cannot double spend; deterministic replay | `test_portfolio.py::test_concurrent_sells_cannot_both_consume_same_shares` |
| Price provenance | Reject wrong instrument, currency, session, adjustment, precision, stale/future/nonfinal record; same canonical price for all teams | `test_portfolio_valuation.py` unavailable/stale/currency cases; new execution contract tests |
| No backdating / look-ahead | Supplied fill date/price rejected; arrival at cutoff targets later session; late retrieval cannot admit post-event orders | New fake-clock/session tests; research temporal tests are not fill tests |
| Calendar | Weekend/holiday/DST/early-close/unscheduled-closure, clock skew/unknown calendar fail safely | New synthetic calendar fixtures |
| Corporate actions | Dividend entitlement/pay-date once; split preserves basis; unresolved merger/delisting suppresses definitive ranking | New action fixtures and frozen-order cases |
| Team authorization | No member, departed member, view-only member and foreign-team mutations denied; admins cannot silently impersonate | New backend permission tests |
| Owner/broker separation | Owner path inaccessible, no owner secrets, broker imports/constructors denied even on error/fallback | New packaging/runtime tests; static read-only broker evidence is insufficient |
| Reconstruction / serialization | Event replay equals all materialized cash/position/reservation views; byte-stable versioned serialization | `test_analysis.py::test_roundtrip`, existing ledger reconstruction scenarios |
| Migration safety | Existing beta history unchanged, rollback on failure, idempotence and unknown-version refusal | `test_accounts.py` and `test_opening_balances.py` migration patterns |
| Recovery | Crash before/after commit, duplicate worker delivery, corrupt/incomplete backup, lost acknowledged requests | New fault injection and isolated restore tests |
| Research linkage | Immutable revision/artifact/time, forbidden private-to-team link denied, later analysis cannot rewrite original rationale | `test_agent_research_context.py`, `test_analysis_finalization.py`, new scoped envelope tests |
| Equity / competition | Cash included; reserved cash not double-subtracted; common-time marks; ties; missing-price provisional status; no-trade return zero | Existing valuation tests plus new cash/lifecycle tests |
| Usage/security | Unauthorized calls consume no model quota; atomic budgets cap concurrent jobs; log scans contain no synthetic secrets | New API/worker quota and disclosure tests |

Acceptance is invariants under failure and concurrency, not an arbitrary completion
percentage. Existing regression must remain green; all new financial/security tests
must pass with zero live requests and zero unexpected network attempts. No test may
initialize actual Toss/broker clients or access production storage.

## 15. Release-blocking gates

All are mandatory before inviting users into a deployed trading beta:

1. Invite-only identity/session lifecycle, revocation and admin protection tested.
2. Owner DB/files/secrets physically inaccessible; configuration has no owner defaults.
3. No beta virtual path can call live broker trading or private-account interfaces.
4. Independent team cash/holdings and exactly-once funding demonstrated.
5. Licensed, independently scoped price source and exact opening semantics verified.
6. Auditable timestamps, session/calendar, adjustment/finality and canonical price records.
7. Insufficient cash and price-over-cap rejection without partial updates.
8. Oversell and reserved-share protections under concurrent requests.
9. Idempotent submission and one fill per order through retry/restart.
10. Atomic accounting and deterministic reconstruction under fault injection.
11. Cash-inclusive equity, corporate-action handling and missing-price ranking policy.
12. Server membership/ownership checks on every read, write, export and background job.
13. Backup/restore drill meets approved RPO/RTO; recovery gaps fail closed.
14. Per-user and global usage/cost limits include retries and concurrent requests.
15. Critical financial tests and full existing offline regression pass.
16. Security tests prove cross-user/team isolation, safe logs and broker exclusion.
17. Competition rules, retention consent, admin resolution procedure and operating owner approved.
18. Product decision on non-team friends acknowledged; no misleading promise of personal portfolios.

Balthasar live response reliability remains a separate outstanding issue from the
previous verification. Preserve fail-closed behavior and disclose unavailable agents.
Set an explicit service-availability expectation before beta; do not label one past
successful terminal AnalysisResult as three-agent reliability verification.

## 16. Proposed implementation milestones and contracts affected

Names and module paths are provisional. **No production module is authorized for
modification by this design document.** Each milestone needs a separate task approving
its precise files, tests and any documentation changes. Existing modules should remain
unchanged unless that task establishes a narrow technical necessity.

| Phase | Objective / proposed scope and modules | Dependencies | Tests / security / exit criteria |
| --- | --- | --- | --- |
| 8.2 Isolated virtual-accounting core | New `magi/beta/virtual/{models,accounting,storage}.py`, independent beta migration namespace and offline tests; funding, cash, basis, reservations, idempotency, replay. Existing production modules authorized now: none. | Approved identity/scope contracts in this design | Core invariants, crash/concurrency/replay and owner-path rejection; no network dependencies or runnable public endpoint; exit: tested isolated core with no legacy writes |
| 8.3 Trusted execution-price policy | New beta price contract, calendar adapter, order-state/settlement worker modules; approved source adapter only under later explicit authorization | 8.2; source licensing/semantics/calendar decision | Synthetic opening/cutoff/outage/correction cases and unique fills; exit: deterministic policy plus separately authorized provider verification before deployment |
| 8.4 Authentication and team authorization | New beta identity/session/invitation/membership services and scoped repository facade; chosen identity library reviewed separately | Resource/ownership contracts; 8.2 integration | Cross-user/team direct API and transaction-time revocation tests; exit: complete enforcement before any user-facing access |
| 8.5 Competition and equity | New beta lifecycle, corporate-action, valuation and ranking modules | 8.2–8.4; valuation/action source policy | Three portfolios, funding/reset, late member/end rules, common-time equity and action/recovery tests; exit: complete synthetic competition |
| 8.6 Research and report integration | New beta artifact envelopes, report revisions/audiences, authorized research-job adapter | 8.4 authorization; 8.5 order links; existing immutable research contracts | Frozen rationale/as_of/completion/ACL/serialization and unavailable-agent behavior; exit: traceable research-to-order links, no automated trading |
| 8.7 Beta API, limits and operations | New beta-specific entry point/API, bounded job/cost control, deployment and recovery documents/config only when explicitly authorized | All previous gates; approved hosting/auth/data vendors | End-to-end offline/security/load/restore tests, budget exhaustion, operator runbook; exit: signed release gates and separately authorized deployment |

Implementation order is 8.2 -> 8.3 -> 8.4 -> 8.5 -> 8.6 -> 8.7 for local offline
work. Authorization interface design precedes core code, and 8.4 may be brought
forward if integration needs it. There is **no interim deployment** of 8.2/8.3 to users.
No need to change providers, voting, AgentResult, AnalysisResult, original portfolio
or original migrations merely to add these independent modules.

Technical necessity / affected contracts for every proposed modification:

- New accounting/storage: cash/order semantics absent in legacy model. Must not change
  legacy Transaction, PositionState, default DB paths or decimal behavior; new rounded
  virtual policy is explicitly versioned.
- New price/calendar modules: execution eligibility absent in Quote/Candle. Adapt
  read-only market concepts without widening Toss or changing shared provider defaults.
- New auth/scoped repositories: current account IDs cannot authorize users. No retrofit
  of broker references into team identities; introduce beta-only scopes.
- New lifecycle/equity/action modules: funding, competitions and receivables are absent.
  Do not reinterpret existing broker opening balances as virtual funding.
- New research envelope/job adapter: persistence and ACLs absent from AnalysisResult.
  Keep serializer identities and voting unchanged; inject isolated agent history and
  beta credentials. If imports/config require compatibility changes, propose them
  separately with public-contract tests before editing shared modules.
- New API/operations: CLI is not a multiuser boundary. New entry point must not expose
  or alter `main.py` routing. Dependencies, deployment and README edits require explicit
  future approval; no existing configuration is implicitly authorized.

## 17. Documentation maintenance and open decisions

Future separately authorized README update: replace stale version/roadmap status,
link current research integration, distinguish historical bookkeeping/read-only
broker access from proposed virtual trading, and disclose beta isolation/availability
limits. Phase documents should identify historical non-integration claims clearly.
Do not advertise simulated trading before release gates pass.

Decisions requiring review before implementation/deployment:

- Team-only scope versus equal personal practice portfolios for all invitees.
- Eligible instrument universe, competition dates, membership/trading grants and whether
  the proposed all-traders/no-per-order-approval policy is acceptable.
- Official opening dataset, exchange calendar, source finality/corrections, licensing,
  independent credentials, action coverage and proposed recovery deadline.
- Five-minute freeze, whole-share/zero-fee/cents-rounding policy and BUY spend caps.
- Authentication/session vendor and invitation/session durations.
- Hosting/storage choice consistent with single-host SQLite constraints, operator
  staffing/second review, retention/backup objectives and incident procedures.
- Benchmark methodology/data and complete valuation requirements for rankings.
- Numerical request/cost limits and three-agent availability expectations.

These are product/vendor acceptance decisions, not reasons to pretend integration
is complete. Missing mandatory source/security/operations decisions block launch.
Recommended first implementation task: separately authorize Phase 8.2's pure,
virtual-only accounting and independent storage, including future authorization
scope inputs, with no network, owner paths or public endpoints.

## 18. Phase 8.1 validation record

Baseline branch/HEAD/staging/clean tree verified; target document did not exist.
Current code, schema/migrations, CLI, market/broker interfaces, orchestration,
serialization and relevant tests were inspected. No provider constructors or live
requests were used. This is documentation-only design: no new tests or schemas and
no claims that proposed gates have passed. Phase 8.0's 430 passing focused tests are
prior audit evidence, not a Phase 8.1 rerun. Existing test cases were reviewed here;
no rerun is needed to claim unchanged implementation behavior.

Final review must verify that only this untracked document exists as a change,
tracked diff and staging are empty, whitespace passes including this untracked file,
HEAD is unchanged, `.env` metadata is unchanged without reading it, production DB
remains absent and workflow-owned caffeine has been stopped. No commit or push.
