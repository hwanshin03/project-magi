# Phase 6C: account-aware ledger and reconciliation

The local ledger records trades that already happened. Broker snapshots are read-only
observations. Phase 6C does not import snapshots, synchronize trades, correct history,
place orders, or implement Kiwoom connectivity.

## Identity and privacy

`PortfolioAccountIdentity` is immutable and contains `provider`, `account_ref`, and
optional `display_label`, `account_type`, `base_currency`. Only provider/reference
are persisted with a transaction; descriptive metadata is not a matching key.

Legacy and new unqualified writes use **MANUAL / DEFAULT**. Broker references use
`ref_` plus SHA-256 of the uppercase provider, a NUL separator, and the broker layer's
internal account reference. For Toss the input is normalized `accountSeq`, never
`accountNo`. This mapping is deterministic and independent of discovery list order.
Provider names remain language-neutral; English/Korean labels are presentation only.

`broker accounts` displays the opaque local reference to copy to Portfolio CLI.
That broker command remains an explicit live request; portfolio commands never
resolve accounts over the network. Portfolio CLI accepts opaque broker references,
not raw numbers or discovery indices. Local `portfolio accounts` lists references
already represented in the ledger, without contacting a broker. Manual references
may be uppercase alphabetic labels with underscores, such as DEFAULT or PAPER.

The digest is a pseudonymous identifier, **not encryption or proof of ownership**.
A manually entered reference is syntax-checked, not verified with Toss. Small internal
IDs could be guessed by hashing; keep local references private. Configured credentials
and common token patterns are rejected by the existing persistence checks. No broker
payload, token, account number, or account discovery response is persisted by this
feature. Arbitrary free-text notes remain user input, not a general-purpose sensitive
data detector; do not paste account numbers or unknown secrets into them.

## Schema v2 and migration

`PRAGMA user_version` is now **2**. Fresh databases use `magi/schema.sql`.
Version 1 migrates through `magi/migrations/002_accounts.sql`, adding two non-null TEXT
columns with defaults `broker_provider = MANUAL`, `broker_account_ref = DEFAULT`.
Account/instrument and account/external-reference indexes are added. Existing UUIDs,
sequence numbers, timestamps, decimal text, notes, external references, linked analyses,
analysis records, and append-only triggers are retained. Nothing is inferred about
which broker owned an old manual trade.

Initialization takes `BEGIN IMMEDIATE` before reading the version. Each SQL statement,
including DDL and the version bump, runs inside that transaction. It deliberately
does not use `executescript`, which can implicitly commit. A failure rolls back the
entire migration and returns a sanitized error. Foreign keys are checked before
commit. Reopening v2 is idempotent; unknown versions and nonempty unversioned schemas
are rejected. Back up an existing database before a deployment migration; no automatic
backup copy containing private financial data is generated.

Normal `Database` initialization migrates v1. Broker reconciliation uses its existing
read-only connection and **refuses v1 instead of migrating it**. Run a local portfolio
command such as `portfolio accounts` to initialize/migrate first. Migration never
reassigns old manual trades to a broker, and append-only trades cannot be retagged.

## Account-scoped and unified views

Position identity is `(provider, account_ref, symbol, market, currency)`. Buys, sells,
backdated replay, and oversell validation stay within that exact identity. Positions
with the same ticker in different accounts remain distinct.

```python
scope = dict(broker_provider="TOSS", broker_account_ref=safe_ref)
position = portfolio.get_position("NVDA", market="US", currency="USD", **scope)
history = portfolio.get_position_history("NVDA", market="US", currency="USD", **scope)
positions = portfolio.get_positions_by_account("TOSS", safe_ref)
open_positions = portfolio.get_open_positions_by_account("TOSS", safe_ref)
accounts = portfolio.get_accounts_with_positions()
unified = portfolio.get_unified_position("NVDA", market="US", currency="USD")
```

Read APIs without account filters return account-separated rows. Single-position and
history reads reject ambiguity across accounts, markets or currencies. Both account
filter fields must be supplied together. Account summaries include open/closed counts.

The optional Python unified view sums held quantity and remaining book cost only for
one symbol/market/currency, then divides total book cost by total quantity (zero for
an entirely closed position). It retains every underlying account position, including
closed positions, in `accounts`. It performs no FX or implicit currency/market merge.
No grand portfolio value is invented. Market valuation also accepts the account pair
and retains it when revaluing with a quote.

Decimal quantities such as `0.000311` and `4.063741` are stored exactly as TEXT and
never rounded to whole shares. Existing limits remain 28 significant input digits,
18 decimal places, and an 80-digit arithmetic context; repeating cost divisions have
that finite precision. Weighted-average book cost includes ledger fees and is not
broker execution cost or tax-lot accounting.

`external_reference` remains unchanged. Its index is scoped by provider/account as a
foundation for future execution-ID deduplication. It is intentionally non-unique:
no import or deduplication policy is implemented in this phase.

## CLI

```sh
# Existing usage remains MANUAL / DEFAULT.
python main.py portfolio buy NVDA 10 180 --market US
python main.py portfolio show NVDA --market US

# Replace SAFE_REF with the opaque reference previously obtained from broker accounts.
python main.py portfolio buy NVDA 4.063741 180 --market US --currency USD --broker TOSS --account SAFE_REF
python main.py portfolio show NVDA --market US --broker TOSS --account SAFE_REF
python main.py portfolio history NVDA --market US --broker TOSS --account SAFE_REF
python main.py portfolio list --broker TOSS --account SAFE_REF
python main.py portfolio recent --broker TOSS --account SAFE_REF
python main.py portfolio accounts
python main.py portfolio list --all-accounts
```

BUY/SELL, show, and history default to MANUAL/DEFAULT. List and recent default to all
accounts with separately labeled rows; `--all-accounts` makes this explicit. It cannot
be combined with account selectors. `--closed` lists closed positions. Existing USD
currency defaults remain unchanged. Portfolio commands perform local bookkeeping only.

## Reconciliation

The selected snapshot's provider/internal reference maps to the same safe identity.
The CLI queries only that local account, and the engine independently filters the
supplied ledger positions by the snapshot identity before comparing instruments.
Manual, other Toss accounts, and other brokers cannot produce MAGI_ONLY discrepancies
for this account. A valid account with no local entries can produce BROKER_ONLY rows;
missing/unreadable databases remain UNAVAILABLE rather than a fabricated empty ledger.

MATCH, QUANTITY_MISMATCH, COST_MISMATCH, BROKER_ONLY, MAGI_ONLY, AMBIGUOUS and UNAVAILABLE
are preserved. Stale/failed snapshots cannot produce a valid comparison. Unknown
markets, duplicates and inconsistent holding identities remain ambiguous.

Each BROKER_ONLY row has an immutable `BrokerOnlyImportPreview`: safe account identity,
symbol, market, currency, observed quantity, observed average cost and snapshot time.
`execution_price` and `executed_at` remain None. Observed average cost is not asserted
to be a historical purchase price, and snapshot time is not a purchase date. Previews
are in memory only; no transaction, database write or inferred execution is created.

## Before the next live reconciliation

Verify that Toss internal references are stable for the intended account and credential
scope, and that the selected local opaque reference is correct. Existing manual rows
will intentionally be ignored; do not duplicate them as broker trades merely to make
a reconciliation match. A future explicit migration/import/correction policy must
address allocation of legacy manual holdings, execution-ID uniqueness and transfers.
Future providers must define a stable, appropriately namespaced internal account ID
before using this mapping. Account aliases and Kiwoom connectivity remain future work.

Tests use temporary v1 fixtures, compare every historical field, simulate rollback
after DDL, check idempotence/future-version rejection, and exercise account scoping,
fractions, unified cost, CLI and reconciliation without live requests.
