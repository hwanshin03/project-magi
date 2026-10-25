# Phase 8.3b — Provider-neutral execution-price and calendar contracts

Implemented: pure immutable data contracts and synthetic calendar lookup in
[`magi.virtual.execution_contracts`](../magi/virtual/execution_contracts.py).
This is a subset of the approved [execution policy design](virtual-execution-price-policy.md).
**Phase 8.3 is not complete. There is no trusted provider, real calendar authority,
order selection, execution authorization, settlement or public trading interface.**
The [Phase 8.2 ledger](virtual-accounting-core.md), schema and behavior are unchanged.

## Separate roles

Existing `Quote`/`Candle` structures serve market estimates and adjusted history;
`PortfolioValuation` uses owner positions and can involve broker reconciliation.
Neither is imported by this contract layer or automatically converted into evidence.
A benchmark observation serves comparison only. Research is not price evidence.
`SyntheticFill` remains the separate internal accounting input, with no conversion
from these observations. No instrument registration, fill or ledger event is created.

`PriceClassification` distinguishes OFFICIAL_OPEN, INDICATIVE_OPEN, INTRADAY_QUOTE,
HISTORICAL_ADJUSTED, CLOSE, VALUATION_ESTIMATE and BENCHMARK. These are normalized
provider claims, not independently verified facts or permissions. CLOSE does not
implicitly mean official. A source may claim an official opening with an adjusted
value; the adjustment remains explicit and is never silently changed to UNADJUSTED.
The future opening policy must reject that candidate. An adjusted historical value
cannot be constructed as HISTORICAL_ADJUSTED with an unadjusted/unknown status.
No OHLC series, quote caching or benchmark computation is duplicated here.

## Price observation and provenance

| Field | Contract |
| --- | --- |
| `source` | Immutable `SourceReference`: provider, dataset, record_id, provenance_id; optional revision_id and supersedes_revision_id |
| `provider_instrument_id` | Bounded opaque provider identity, separate from internal identity |
| `instrument_id` | Optional canonical UUID; None means unresolved, never inferred from a ticker |
| `exchange` | Uppercase four-character MIC-shaped identifier; registry membership/listing truth is unverified |
| `currency`, `price` | Explicit USD, exact positive finite Decimal, at most 1,000,000 and eight decimal places, preserving input precision |
| `session_id` | Explicit opaque session reference, no weekday/session inference |
| `classification` | Explicit price role/event claim |
| `event_at`, `precision` | Aware event instant with SECOND/MILLISECOND/MICROSECOND precision, or None for SESSION_DATE/UNKNOWN |
| `observed_at` | Optional provider observation time, not collector retrieval or inferred publication time |
| `retrieved_at` | Required collector retrieval-completion time |
| `adjustment` | UNADJUSTED, ADJUSTED or UNKNOWN |
| `official_status` | Provider claim OFFICIAL, NONOFFICIAL or UNKNOWN |
| `finality` | Provider claim FINAL, PRELIMINARY or UNKNOWN |
| `corporate_action_ref` | Optional opaque metadata reference; None means unknown, not no actions |

Phase 8.3a calls collector retrieval completion `observed_at`; this implementation
calls it `retrieved_at` to keep the separately requested **provider** observation
instant distinct. Neither timestamp is an execution time or proof of publication.
No publication/finality-established timestamp is fabricated. Provider finality
attestation and independent validation receipts remain future work.

Source references allow 1–96 ASCII letters/digits/dot/underscore/hyphen, starting
with a letter/digit and excluding `..`. URLs, paths, queries, free text and raw
payloads are not accepted reference formats. A provider adapter will need explicit
normalization for unsupported source identifiers; there is no permissive fallback.
One nested provenance structure avoids conflicting duplicate provider/dataset fields.
Explicit supersession requires a distinct current revision; absent lineage stays
unknown. The contract checks structure, not whether the asserted relationship is true.
The UUID reference is not a credential or capability. Never put secrets in reference
fields; format validation cannot recognize every possible secret-shaped identifier.

Construction rejects floats (including automatic float-to-Decimal conversion),
booleans, zero, negatives, nonfinite values, unsupported precision, wrong enum types,
invalid UUIDs, malformed references and non-USD currencies. No FX is invented.
Reuse is limited to pure Phase 8.2 identity/USD/Decimal/error helpers; those helpers
are unchanged. Errors expose only the fixed `INVALID_INPUT` code, not input content.

## Time and consistency checks

Instants must be actual datetime values with a standard fixed-offset timezone or
IANA ZoneInfo; naive values and strings are rejected. Valid values normalize to UTC.
Nonexistent IANA wall times and ambiguous IANA wall times (even with a fold flag)
are rejected. Price callers with genuinely explicit disambiguated instants can use
UTC; calendar sessions additionally reject ambiguous exchange-local schedule times.
No clock tolerance, timestamp rounding or guessed event time is introduced.

When present: event_at <= provider observed_at <= retrieved_at; event_at must also
be <= retrieved_at when observed_at is absent. This observation contract describes
an already-observed price event, not a forecast. Declared event precision must agree
with the actual instant. SESSION_DATE/UNKNOWN cannot carry a fabricated datetime;
only the session reference remains, requiring a future source-specific interpretation.

`validate_observation_time(observation, validated_at=..., as_of=...)` checks
retrieved_at <= validated_at <= as_of. It does not read the clock, persist a receipt,
assert source approval or prove historical availability. A late retrieval cannot
pass with an earlier supplied as_of. All supplied times remain caller claims.
Real order commitment, eligible-session choice and time of finality establishment
require independent future evidence; a synthetic object cannot prove historical
executability. No order, freeze rule, automatic next-session selection, evidence
seal or deadline is implemented.

`validate_price_session` checks known scheduled coverage, exact exchange/session
identity and event containment in the declared regular interval. It deliberately
returns no eligibility/authorization value. It is **not an official-opening policy**:
matching valuation, delayed or halted observations may pass structural checks.
A future approved policy must additionally enforce role, unadjusted status, source
rights, instrument mapping, original finality/revisions, action/trading status,
commitment and every execution gate. Neither pure validation function grants trust.

## Calendar interface and synthetic fixtures

`ExchangeCalendar` is a provider-neutral Protocol with
`get_session(exchange, session_date) -> CalendarSession`. A session includes exchange,
local date, IANA trading_timezone, versioned SourceReference, KNOWN/UNKNOWN coverage,
status, optional session_id, regular_open/regular_close, early_close and delayed_open_at.

Statuses: REGULAR, CLOSED, EXCEPTIONAL_CLOSURE, DELAYED_OPEN, HALTED, SUSPENDED, UNKNOWN.
Closures and unknown coverage contain no invented hours or early-close boolean.
Scheduled days require ordered same-local-date regular hours and an explicit boolean.
Delayed opening retains scheduled hours separately; the actual delayed instant may
remain absent, or must lie strictly within those hours. Exchange-wide halt/suspension
representation is not a claim of instrument-level status coverage.

Schedule inputs must use the declared IANA zone or UTC; a different zone/fixed local
offset fails. UTC inputs must map to the declared exchange-local date and unambiguous
wall time. The contract checks internal consistency, not the correct real-world
MIC-to-timezone mapping. That mapping and timezone-data version must be approved by
a future authority/profile; no registry is downloaded or silently assumed.

`SyntheticCalendar` is an immutable tuple snapshot for **offline fixtures only**.
All entries share exact exchange/timezone/provenance/version. Duplicate dates,
conflicting versions and duplicate nonmissing session IDs fail. Missing dates return
UNKNOWN coverage/status, with no hours or session ID. An exchange mismatch raises
INVALID_INPUT; absence never means a weekend, holiday or open session. There is no
fallback, cache, provider client or calendar acquisition.

Permanent tests use the invented exchange XSIM and explicit fixture schedules.
Ordinary days, weekends, invented holidays, early/exceptional closes, both DST
transitions, delayed opening, halt/suspension, unknown/missing/conflicting coverage,
wrong identities/zones and nonexistent/ambiguous local times are tested. These
fixtures are not official historical schedules or real future trading hours.

## Isolation, validation and later work

The new module imports only standard-library utilities and pure virtual model
helpers. Importing the existing virtual package also imports its existing storage
module, but creates no connection; no existing initializer was changed. New tests
block DB connections and ledger construction, and verify a fresh-process import and
lookup with owner, broker, market-client, environment-loader and network access denied.
No Toss session or broker adapter is constructed by the new tests. Existing full
regression fixture-only Toss tests remain separate, with synthetic credentials and
in-memory MockTransport under the user's explicit authorization.

No .env reads/writes, DB creation, raw payload persistence, public routes, provider
calls, dependencies, source approvals or trading operations are added. Frozen values
are ordinary Python data contracts, not a security barrier against code that controls
the process. No `is_trusted`, execution capability or trust-promotion API exists.

Future separately authorized work: approve instrument/source/calendar/status and
licensing contracts; implement policy checks and order lifecycle as a separate pure
layer; then authorize adapter verification, atomic reservations/settlement and real
authentication. Actual price acquisition and live calendar verification remain
unsupported. No public launch or Phase 8.3 completion is claimed. Project MAGI retains
its name; MOIRAI remains proposed. README and the approved design remain unchanged.
