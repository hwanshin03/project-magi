# Phase 6D: explicit opening balances

An **OPENING_BALANCE** means: at the snapshot's as-of time, MAGI starts tracking an
existing position whose earlier trade history is unknown. It is not a BUY, broker
order, historical execution, or recommendation from MAGI. No original purchase date,
execution price, fees, or prior trades are manufactured.

Only the explicit import command writes an opening event. Account discovery, holdings,
summary, reconciliation and import-preview never import anything. No new Toss endpoint
was added, and the remote adapter/transport still permits read-only data access plus
OAuth only. No live calls or production writes are made by automated tests.

## Schema v3 and event model

Schema version 3 adds `portfolio_opening_balances`. The existing v2 trade table and
analysis tables are not copied, deleted, rebuilt or reinterpreted. Keeping a separate
event table preserves the BUY/SELL constraint and execution-price/fee semantics of
old transactions. `Portfolio.get_transactions()` and history APIs return an ordered
ledger combining `Transaction` and immutable `OpeningBalance` objects.

The new table contains:

- sequence and unique transaction/event UUID;
- `as_of` and `recorded_at` UTC timestamps;
- broker provider, opaque safe account reference, symbol, asset name, market, currency;
- action `OPENING_BALANCE`, Decimal quantity, opening unit cost, opening book cost;
- source `BROKER_SNAPSHOT`, history indicator `OPENING_BALANCE_HISTORY`;
- notes and optional external reference.

The Python event exposes `as_of`; its common `timestamp` attribute aliases the tracking
time for ledger ordering. It has no execution price, fee or linked-analysis value
(those compatibility accessors return None). The separate SQL table has no such
columns. No current market valuation or raw broker payload is persisted.

Migration `003_opening_balances.sql` adds the table, uniqueness constraint and triggers
under the existing `BEGIN IMMEDIATE` transaction, then advances user_version to 3.
Version 1 chains through the account migration to version 3 in the same transaction.
Historical analyses, account identities and every old trade field are retained.
Failure rolls back all DDL/version changes; reopening v3 is idempotent; future versions
are rejected. Opening events reject UPDATE/DELETE, just like other ledger records.

Preview and reconciliation use read-only SQLite and require v3 when a database exists.
They never migrate an older database. Back up an existing ledger before deployment,
then run a local `portfolio accounts` command to migrate it. A missing database remains
missing during preview; an explicitly confirmed import can initialize a new v3 ledger.

## Accounting and history

Opening basis = broker quantity × broker average cost, computed with Decimal and the
existing 80-digit arithmetic context. It establishes held quantity and book basis;
it does not increase recorded BUY quantity or tracked BUY cash outlay. USD and KRW
remain distinct; there is no implicit FX or combined-currency portfolio value.
Fractional shares retain their original precision. Existing inputs support 28
significant digits and 18 decimal places; calculated opening book cost is stored as
Decimal TEXT without reducing it to those input limits or SQLite REAL.

Later BUYs add cost plus their actual recorded fees. SELLs release weighted-average
basis, including opening basis, and compute realized P/L **after tracking began**.
A full close releases all remaining basis. No pre-tracking realized return is inferred.
Account views and optional unified views use this same basis; unified views keep
individual accounts and their history-completeness indicators.

`PositionState` distinguishes:

| Field | Opening-initialized position |
| --- | --- |
| `history_completeness` | `OPENING_BALANCE_HISTORY` |
| `tracking_start_date` | Opening as-of time |
| `first_purchase_date` / `original_first_purchase_date` | None / UNKNOWN |
| `first_recorded_buy_date` | First later actual BUY, or None |
| `pre_tracking_realized_pnl` | None / UNKNOWN |
| `realized_pnl` | Only tracked SELL performance; zero until a tracked sale |

Existing trade-only positions retain `COMPLETE_HISTORY`, based on the user's ledger
history; this is not broker certification that all trades have been entered. Closing
and reopening an imported instrument does not make its lifetime history complete.
The CLI displays unknown original history and labels realized P/L as tracked performance.
English/Korean opening labels and unknown-history warnings live in presentation helpers.

## Preview and explicit import

These broker commands make live read-only requests when deliberately run by a user.
They are examples, not commands executed during implementation:

```sh
# Discover the safe opaque reference. Never use a full account number.
python main.py broker accounts

# Replace SAFE_REF with the exact ref_... value displayed by account discovery.
python main.py broker import-preview --account SAFE_REF
python main.py broker import-preview NVDA --account SAFE_REF --market US --currency USD

# Creates exactly one local opening event, after explicit confirmation.
python main.py broker import-position NVDA --account SAFE_REF --market US --currency USD --confirm

# Optional notes and Korean opening/history labels.
python main.py broker import-position NVDA --account SAFE_REF --confirm --note "Start MAGI tracking"
python main.py broker import-preview NVDA --account SAFE_REF --language ko
```

Existing read-only commands still use discovery indices (`broker reconcile --account 1`).
The new import commands require an **opaque safe reference**, never an index. `--provider`
defaults to TOSS; no other live broker adapter is added. `import-position` requires one
symbol and `--confirm`; there is no bulk write option. Omitting confirmation fails
before client creation, account discovery or database initialization.

Preview shows the account, instrument, asset name, quantity, observed average cost,
calculated opening book cost, current price/value/unrealized P/L when supplied, snapshot
time, and unknown-history warnings. All-position preview is read-only; an ineligible
position prints its rejection and makes the command exit nonzero. Duplicate ticker
identities require explicit market/currency selection.

The write command fetches and displays a current preview again, then imports that
snapshot. It does not persist or later replay a preview file. Data can change between
separate preview and import commands. A successful write prints the event ID; a second
attempt is rejected rather than duplicating shares.

## Import validation and deduplication

`BrokerPositionImporter` is provider-independent and performs no network calls. Its
input is a normalized `BrokerResult` and the expected `PortfolioAccountIdentity`.
It rejects missing/failed/stale data, future or more-than-60-second-old snapshot/holding
times, wrong provider/account, ambiguous instruments, zero/negative quantity, and
missing/invalid average cost. Known zero cost is valid; missing cost is not zero.
The command requires fresh account discovery, then one holdings snapshot. Snapshot
freshness is checked again before writing; this is a local fetch-time check, not a
claim that the exchange or broker source updates in real time.

Only a unique BROKER_ONLY instrument with **no existing ledger history** in that exact
provider/account/symbol/market/currency may initialize. Prior BUY/SELL activity, even
if fully closed, blocks opening initialization. Existing matching holdings produce
ALREADY_TRACKED; quantity or cost differences produce corresponding mismatch errors.
An import never repairs those differences or overwrites history. Manual and other
broker accounts are unaffected.

The final no-history check and insert occur inside `BEGIN IMMEDIATE`, so concurrent
imports cannot both initialize. A permanent SQL unique constraint enforces one opening
per account/instrument; triggers also reject openings over existing trades and trades
backdated before tracking starts. Same-time real trades follow the opening event.
There is no reversal/correction system, so a second opening after a full close is also
rejected. Known later trades can still be entered normally.

No stable Toss execution/position ID is inferred from a snapshot. The default external
reference is a deterministic local `opening_` hash of safe account/instrument identity;
the unique instrument constraint is authoritative, regardless of external reference.
This is initialization deduplication, not transaction-history import deduplication.

Reconciliation remains read-only. After initialization, equal quantity and comparable
average cost reconcile to MATCH; price moves do not change opening cost. Future broker
cost corrections, stock splits, fees or different cost conventions can still produce
mismatches, requiring explicit future workflow rather than silent changes.

## Manual-history alternative and live-review points

If actual historical trades are known, enter those real BUY/SELL records under the
correct broker/account instead of importing an opening balance. Do not create both
representations for the same holdings. Historical backfill before an opening is blocked;
there is no merge, deletion or correction wizard in this phase.

Before the separately authorized first live import, review the exact safe account,
market/currency, quantity, average-cost convention, and the irreversible append-only
initialization choice. Back up existing local data. Broker average cost is an observed
starting basis, not proof of original execution prices, fee treatment, tax basis or
historical performance. Confirm that no equivalent manual position would cause you to
double-count the same assets in a unified view. No automatic reassignment is performed.

The database stores only the selected opening event's business fields and safe account
reference. Tokens, full account numbers, internal provider account IDs and raw snapshots
are not serialized. Existing credential checks also cover notes/asset names. Arbitrary
unknown secrets in free text cannot be recognized with certainty; the local ledger is
not encrypted and is not a credential vault.
