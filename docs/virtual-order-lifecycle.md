# Phase 8.3c — Pure session policy and virtual order lifecycle

Implemented in [session_policy.py](../magi/virtual/session_policy.py) and
[order_lifecycle.py](../magi/virtual/order_lifecycle.py), using the existing
[execution contracts](virtual-execution-contracts.md). This is the pure policy
subset of the [execution design](virtual-execution-price-policy.md).
**These are synthetic simulation contracts, not trusted order acceptance or Beta
readiness.** No real exchange calendar, price source or operating policy is approved.
The Phase 8.2 ledger, database schema, accounting and durable fill idempotency are unchanged.

## Explicit policy and selection

`SessionPolicy` requires a bounded opaque `version`, `freeze_before_open` and
`price_wait_after_close`. No argument has an operating default. The freeze interval
must be a positive timedelta shorter than a day; this is a structural restriction
for this contract, not an approved operating interval. The optional price-wait
interval must be positive, or explicitly `None` to disable deadline expiration.
Timestamp arithmetic must be representable. Five minutes and two hours appear only
as explicitly supplied synthetic test values. The version is a label, not approval
or a registry binding that label to parameter values. Transitions preserve both.

`select_session` accepts only the exact in-memory `SyntheticCalendar` type, an
exchange, acceptance instant, explicit policy and inclusive `through_date` horizon.
It does not invoke arbitrary calendar implementations or acquire data. From the
acceptance date in the declared trading timezone, it checks every date in order:

| Classification | Meaning and selection behavior |
| --- | --- |
| UNKNOWN | Missing or explicitly unknown coverage; stop with CALENDAR_UNAVAILABLE |
| INELIGIBLE | Confirmed CLOSED, EXCEPTIONAL_CLOSURE, DELAYED_OPEN, HALTED or SUSPENDED; skip before acceptance |
| ELAPSED | Known regular session whose admission cutoff has been reached; skip before acceptance |
| ELIGIBLE | Known REGULAR session with acceptance strictly before its cutoff; select it |

An early-close regular session remains eligible; its explicit close sets any
configured evidence deadline. `ELAPSED` describes admission eligibility, not whether
the exchange has already closed. Unknown days cannot be skipped to reach a later
convenient session. Weekends and holidays require explicit records. Unknown dates
after the first selected session are irrelevant to that selection. Exhausting a
fully known horizon yields NO_ELIGIBLE_SESSION, distinct from insufficient coverage.
There is no fallback, weekday inference, session synthesis or calendar-order bias.

The existing calendar contract rejects conflicting dates, session IDs, versions,
hours and timezone declarations. Exchange identity must match exactly; MIC-shaped
syntax is not registry verification. Instrument UUID to exchange/listing mapping
remains an unverified caller claim. XSIM fixtures are invented schedules, including
their holidays and DST examples, not actual NYSE history.

`SessionSelection` retains the complete immutable calendar session/provenance,
policy and acceptance instant. `cutoff_at = regular_open - freeze_before_open`;
the optional `deadline_at = regular_close + price_wait_after_close`. Constructing
this value directly checks eligibility but cannot prove it is the first eligible
session. `prepare_order` runs selection over the supplied calendar to establish
that deterministic simulation result. Neither constructor creates authority.

## Time and intent

`OrderIntent` contains canonical UUID references for order, portfolio, instrument
and idempotency key, a MIC-shaped exchange, the existing BUY/SELL `Side`, positive
whole-share Decimal quantity (maximum 1,000,000, as in the existing synthetic fill
contract) and submission instant. It contains no execution price or ownership flag.
There is no portfolio lookup, funding check, instrument registration or reservation.

`prepare_order` requires submission <= acceptance. Selection uses **acceptance**,
so an old submission timestamp cannot backdate a queued request past a cutoff.
At exactly the cutoff the current session is no longer eligible. A later session
may be selected only during initial preparation and only through explicit coverage.
An already accepted order never rolls forward automatically.

Submission, acceptance, local session date, opening, cutoff and transition instant
are separate values. Price event time remains in the separate price contract.
Existing Phase 8.3b timestamp validation normalizes aware datetimes to UTC and
rejects naive values, nonexistent IANA wall times and ambiguous IANA wall times
(even with a fold flag). Exchange-local dates use the declared IANA timezone,
not the client or UTC date. New York, Los Angeles and UTC representations of an
unambiguous instant agree; no fixed US UTC offset is assumed. No wall clock is read.

## Immutable lifecycle

`OrderSnapshot` retains intent, selection, state, changed_at and optional immutable
`ExpirationEvidence`. Construction checks structural/timing consistency; the pure
`validate_transition` function also checks the edge and unchanged intent/selection.
`transition_order` constructs and validates a new snapshot without changing its input.

| From | To | Additional condition |
| --- | --- | --- |
| ACCEPTED | FROZEN | Transition instant >= cutoff |
| ACCEPTED | CANCELLED | Transition instant < cutoff |
| FROZEN | AWAITING_PRICE | Transition instant >= regular opening |
| ACCEPTED, FROZEN, AWAITING_PRICE | EXPIRED | Explicit qualifying expiration evidence |
| CANCELLED, EXPIRED | None | Terminal; no reopening or repeat finalization |

ACCEPTED's timestamp is its acceptance instant. Every subsequent transition must
be at or after acceptance and must not regress relative to the preceding snapshot.
Distinct permitted edges can share an instant, allowing a delayed simulation to
freeze then await at opening. Duplicate edges, unknown states and backward edges
are rejected. An ACCEPTED snapshot that a worker has not updated still cannot be
cancelled at/after cutoff. There is no automatic timer, worker or reservation release.

`FILLED` is reserved enum vocabulary only. Snapshot construction and every transition
into FILLED fail with SETTLEMENT_REQUIRED. No price argument, trusted-fill shortcut,
Phase 8.2 ledger call or financial mutation exists. `REJECTED` is omitted from this
minimal subset: invalid preparation raises a fixed error and creates no order.
Future authenticated admission and financial rejection outcomes need separate design.

All intent fields and the entire accepted selection remain equal across validated
edges, including acceptance, policy parameters/version, session and provenance.
Frozen dataclasses are ordinary Python data values, not protection against hostile
code controlling the process. Direct snapshots and caller-supplied times are claims;
they cannot establish trusted history, receipt time or current state. A future
persistent service must prevent backdating, conflicting branches and stale updates.

## Cancellation and expiration boundaries

Cancellation is limited to ACCEPTED before cutoff. Frozen/awaiting and terminal
orders cannot cancel. No fill, cash/share change or reservation release occurs.
Future cancellation must atomically validate trusted time/current state and release
reservations in the same transaction.

Expiration requires an explicit `SealStatus.NO_SEAL` simulation claim. SEALED and
UNKNOWN fail with EVIDENCE_UNRESOLVED: worker delay must not destroy a qualified
canonical opening-price seal. A provider's FINAL flag is never mapped to a seal.
This phase creates, retrieves and verifies no seals.

- PRICE_DEADLINE: the configured deadline must exist and transition time must be
  at or after it. No default deadline, closing-price substitution or fabricated
  missing-price outcome is supplied.
- SESSION_INVALIDATED: an explicit known nonregular revised calendar session must
  match exchange, date, timezone, provider and dataset, have a different revision,
  and preserve the session ID when supplied. The transition must be no later than
  the original opening. The original selection is retained; revision evidence is
  stored only in the new immutable snapshot. Unknown coverage cannot invalidate it.

Day-level calendar status lacks effective-time history. A post-opening halt or
closure therefore cannot establish opening invalidity and fails unresolved, even
with a NO_SEAL claim. A different revision is not proof of newer publication,
supersession or authority. Verified revision ordering, effective-time evidence and
cohort-wide recovery remain future requirements. Deadline expiration is separately
available with its explicit inputs. These simulations do not authorize real expiry,
selective after-the-fact cancellation, rollover or release of reservations.

Errors use fixed `PolicyCode` values; structural failures reuse INVALID_INPUT.
They do not echo submitted references or payloads. Reference syntax cannot detect
every secret-shaped value; callers must never put credentials in references.

## Isolation and future work

The new modules import only standard-library helpers and existing pure virtual
contracts. Importing the existing virtual package also imports virtual storage,
but creates no connection. No existing production module or initializer is changed.
New tests block DB/ledger operations and verify fresh-process import, selection and
cancellation with owner, broker, market, credential-loader and network imports or
operations denied. They construct no Toss or broker adapter and create no DB.
Existing accounting regression uses isolated temporary DBs; existing fixture-only
Toss regression uses synthetic credentials and in-memory MockTransport separately.

There is no order persistence, durable submission idempotency, public endpoint,
authentication, team authorization, account ownership, reservation, settlement,
provider client, raw payload, cache, dependency change, portfolio mutation or trade.
Selecting a session never verifies PriceObservation or promotes official/finality
claims. Adjusted candles and valuation quotes cannot become fills through this API.

Future work requires separately approved operating cutoff/deadline, authoritative
calendar/status and price contracts, instrument mapping, authenticated acceptance,
portfolio-scoped durable order idempotency, atomic state/history concurrency,
reservations and verified settlement. Existing fill idempotency is not order
idempotency. This phase implements none of those services. Project MAGI remains the
repository name; MOIRAI remains proposed. README and prior design documents are
unchanged; their older implementation-status passages describe their own milestones.
