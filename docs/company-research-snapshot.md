# Phase 7C: deterministic company research snapshots

`EvidencePack → build_snapshot → CompanyResearchSnapshot` is a pure, offline
transformation. It makes no HTTP/LLM calls and writes no files or database records.
The explicit research CLI obtains a pack through the existing provider, then calls
the same builder. Normal analysis, personas, debate, voting, explanation, portfolio,
broker, market and database schema are unchanged. No news or agent grounding is added.

## Schema and provenance

The frozen schema-version-1 snapshot contains identity, reporting_context,
income_statement, growth, profitability, balance_sheet, cash_flow, per_share,
evidence_references, sources, warnings, coverage, created_at and underlying_pack_id.
Creation time is the input pack's timestamp, not a new wall-clock reading, so the
same input and options produce the same snapshot. Provider identity is scoped to
SEC CIK or DART corp_code; ticker, company, market and currency remain explicit.
Issuer metadata must agree with the input pack and source CIK/corp_code fields.

A BaseMetric has a name, current MetricValue and prior MetricValue. Each value has
Decimal value (or None), unit, FinancialPeriod, evidence_ids, status and warnings.
Balance-sheet prior values are deliberately unavailable: prior fiscal-year-end
balances are not automatically year-over-year balances for an interim date.

A DerivedMetric has name, Decimal value (or None), ratio unit, period, calculation,
input_evidence_ids, status and warnings. Status is AVAILABLE, UNAVAILABLE,
CONFLICTED or NOT_MEANINGFUL. Missing data never becomes zero. Snapshot validation
checks citations, base values against evidence, derived calculations and coverage.

Selected EvidenceItems and ResearchSources are retained verbatim, including source
locators, original concept/account identity, accession/receipt, dates, units and
context. Conflict alternatives are reachable through the metric's evidence IDs.
The original pack is not modified or discarded. The snapshot records its pack ID.
The source list and reporting context identify the latest selected filing(s),
including publication dates and official URLs. Equal latest timestamps retain all
relevant source IDs rather than arbitrarily choosing a filing.

The hard bounds are 128 evidence items and 128 sources. Excessive conflicting
alternatives fail explicitly; they are not silently truncated or resolved by ID.
Most complete snapshots need around two dozen evidence references.

## Mapping policy

Mappings are explicit ordered policies in `snapshot_policy.py`, not fuzzy names.
No item is summed merely because its label resembles another account.

| Metric | SEC US-GAAP examples | DART account IDs |
|---|---|---|
| Revenue | RevenueFromContractWithCustomerExcludingAssessedTax, Revenues, SalesRevenueNet | ifrs-full_Revenue, ifrs_Revenue |
| Operating income | OperatingIncomeLoss | dart_OperatingIncomeLoss |
| Net income | NetIncomeLoss, ProfitLoss | ifrs-full_ProfitLoss, ifrs_ProfitLoss |
| Basic/diluted EPS | EarningsPerShareBasic / EarningsPerShareDiluted | ifrs-full_BasicEarningsLossPerShare / DilutedEarningsLossPerShare |
| Assets/liabilities | Assets / Liabilities | ifrs-full_Assets / Liabilities (legacy ifrs aliases also supported) |
| Equity | StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest, StockholdersEquity | ifrs-full_Equity, ifrs_Equity |
| Cash | CashAndCashEquivalentsAtCarryingValue | ifrs-full_CashAndCashEquivalents |
| Debt | DebtCurrentAndNoncurrent only | Unavailable until a reliable total-debt mapping is approved |
| Operating/investing/financing cash flow | NetCashProvidedByUsedIn…Activities | ifrs-full_CashFlowsFromUsedIn…Activities |
| PP&E capex | PaymentsToAcquirePropertyPlantAndEquipment | ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities |

Equity definitions (including/excluding noncontrolling interests) remain in original
concept metadata. PP&E capex is labeled narrowly; it is not all investment spending
and does not include intangible purchases. No debt aggregation or free-cash-flow
inference is performed. SEC non-US-GAAP/custom concepts are not mapped in v1.
DART names are provenance, not a substitute for a recognized account ID. DART
statement type and consolidated/standalone division are checked. SCE/member-specific
rows are not substituted for BS totals. Unrecognized account-detail contexts are
excluded rather than added to consolidated figures.

Reporting currency must be unambiguous or supplied explicitly. There is no FX
conversion. SEC EPS requires currency/shares units; a currency-only SEC observation
cannot masquerade as EPS. DART's financial endpoint supplies currency for EPS too;
stable EPS account IDs justify the normalized currency/shares unit, while original
unit and account identity remain in evidence.

## Period selection

SEC annual observations require annual forms (10-K/20-F/40-F, including amendments),
FY where fiscal-period metadata is supplied, and 330–380-day durations. Quarterly
observations require 70–105-day durations from 10-Q or explicit quarter observations
in 10-K. No Q4 is derived by subtracting annual and nine-month values. Quarter facts
with YTD durations are excluded. Calendar frame strings and filing fiscal-year
labels do not override actual start/end dates: comparative facts can carry the
current filing's fiscal year. Fiscal-year metadata is retained and validated, but
not mistaken for the observation year.

The latest completed end date (at or before pack creation) is selected. Different
start dates for that end date are rejected as ambiguous, not blended across metrics.
All duration metrics use exactly that interval. Instant balance-sheet values must
match its end date and have an appropriate filing form. If only instant facts exist,
a real filing report_date may supply the end, without an invented duration start.

Prior SEC values must represent the same kind of interval one year earlier:
start/end differences of 350–380 days and duration-length difference at most ten days
allow 52/53-week fiscal calendars. Multiple possible prior intervals are left
unavailable with INCOMPARABLE_PERIOD. A preceding quarter is not a YoY comparator.

DART uses explicit business_year, report_code and statement_division. Latest available
business year in the input pack is selected, or an explicit year can be required.
The CLI requires a year and does not discover or fetch fallback years automatically.
Dates are only preserved when provided; year labels never become invented Dec 31
ends or publication dates. Explicit comparison dates must also be compatible.

| DART report | Basis | Income/EPS current / prior fields |
|---|---|---|
| annual / 11011 | Annual | thstrm_amount / frmtrm_amount |
| q1 / 11013 | Quarter | thstrm_amount / frmtrm_q_amount |
| half / 11012 | Half-year cumulative | thstrm_add_amount / frmtrm_add_amount |
| q3 / 11014 | Nine-month cumulative | thstrm_add_amount / frmtrm_add_amount |

Interim cash-flow `thstrm_amount` is cumulative. It is never mixed into a single Q2/Q3
income period. Interim cash-flow prior comparisons remain unavailable without a
separate policy for their comparative fields. Balance sheets use current instant
amounts. Missing cumulative income fields do not fall back to three-month amounts.
This conservative policy can leave some interim EPS/financial metrics unavailable.

## Ranking, revisions and conflicts

After issuer, period, statement context and units pass, selection prefers the latest
official publication timestamp, then the ordered concept policy. Identical facts
are not double counted. Same-ranked, same-valued observations retain their citations.
Different values or explicit intervals at equal rank produce CONFLICTED with all
alternatives; no tie is broken by evidence ID. Newer revisions take preference and
emit RESTATED_VALUE when an amendment/correction is explicit or an older available
value differs. This warning identifies a revision, not an assertion that an earlier
filing was legally erroneous. Original packs remain unchanged.

## Calculations and availability

- YoY fraction: `(current - prior) / abs(prior)` for compatible periods/units and
  positive prior values.
- Zero prior: NOT_MEANINGFUL, ZERO_DENOMINATOR.
- Negative prior: NOT_MEANINGFUL, NEGATIVE_PRIOR_VALUE; a turnaround is not rendered
  as a conventional growth percentage. Inputs remain cited for later review.
- Missing prior: UNAVAILABLE, MISSING_PRIOR_PERIOD.
- Operating/net margin: corresponding income divided by revenue for the same
  interval and unit. Zero or negative revenue is NOT_MEANINGFUL. Negative income
  with positive revenue is a valid negative margin.
- Conflicting inputs propagate CONFLICTED; incompatible contexts are unavailable.

Calculations use Decimal with precision 34 and half-even rounding. Stored ratios are
fractions; presentation multiplies by 100 and shows two decimal places. There are no
LLM interpretations, valuation judgments, recommendations, or trading decisions.

Coverage reports COMPLETE/PARTIAL/UNAVAILABLE for income statement, balance sheet,
cash flow and per-share sections. Warnings include missing metrics, missing prior,
conflicting official facts, incomparable periods, restated values, absent period
dates and stale filings. Staleness checks the input pack's source-age threshold and
period ends older than 550 days for annual / 200 days for interim data. These are
explicit heuristics, not promises that a newer filing exists.

## Presentation, serialization and CLI

`render_snapshot` defaults to Korean, supports English, and has a 32,000-character
bound. Each displayed metric includes evidence aliases; the evidence/source appendix
resolves them to full IDs, concepts, numbers, filing identifiers, URLs and dates.
Warnings and identifiers remain language-neutral. `render_snapshot_context` uses
separate trusted instructions and UNTRUSTED RESEARCH DATA with a 40,000-character
bound. It is not connected to any agent. Official titles and evidence text remain
untrusted even when included in a deterministic summary.

The existing tagged `dumps`/`loads` serialization supports snapshots and base/derived
metrics, preserving Decimal, dates, aware timestamps, references, enums and ordering.
The additive research envelope remains version 1 and the snapshot has its own schema
version 1. Unknown versions and invalid/uncited numeric values are rejected. No
secret/configuration value is copied into identity, evidence or serialized output.

```sh
python main.py research sec snapshot NVDA
python main.py research sec snapshot NVDA --period quarter --language en
python main.py research dart snapshot 005930 --year 2025 --report annual
python main.py research dart snapshot 005930 --year 2025 --report half --language en
```

SEC snapshot commands default to the bounded mapping's concept set; existing
`--concept` and `--max-facts` options can narrow it. `--currency USD` (or KRW) resolves
explicit currency selection, without converting any amount. DART supports `--division
CFS` (default) or OFS. Invoking these commands can use the provider network; automated
implementation tests use MockTransport and existing fixture copies only.

## Before live verification

Review NVDA concept coverage, the resulting fiscal intervals and any revision/conflict
warnings. Review Samsung's statement/account-detail coverage and missing exact dates,
particularly before interpreting interim cumulative versus quarter amounts. Check
that cited input definitions match the intended equity/net-income scope. Cold DART
directory parsing remains a known provider cost. No live snapshot verification is
performed during implementation; no production database, `.env` or agent changes
are required. Broader mapping, news, reports and agent grounding are later phases.
