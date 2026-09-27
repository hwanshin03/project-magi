# Portfolio CLI (Phase 5.5)

These commands **record trades that have already occurred**. They do not submit orders,
contact brokers, fetch market prices, or act on MAGI recommendations. The existing
append-only ledger and weighted-average accounting remain authoritative.

Run from the project directory with the project environment activated. `python main.py`
with no arguments still launches normal MAGI analysis. Portfolio commands do not create
AI clients or require valid API keys.

## Command syntax

```text
python main.py portfolio buy SYMBOL QUANTITY PRICE [trade options]
python main.py portfolio sell SYMBOL QUANTITY PRICE [trade options]
python main.py portfolio show SYMBOL [--currency CODE] [--market MARKET] [--current-price PRICE]
python main.py portfolio list [--closed]
python main.py portfolio history SYMBOL [--currency CODE] [--market MARKET]
python main.py portfolio recent [--limit N]
```

Both trade commands support:

```text
--date YYYY-MM-DD
--fees AMOUNT
--currency CODE
--market MARKET
--asset-name NAME
--note TEXT
--run-id ANALYSIS_RUN_ID
```

Use `python main.py portfolio --help` or append `--help` to any subcommand.

- Currency defaults to **USD** on buy, sell, show, and history. Currency codes are
  three letters; USD and KRW work, and other codes are not artificially excluded.
- List and recent include all currencies and markets. There are no cross-currency totals.
- Market is never inferred. Trade entry without `--market` records an unset market.
  Use the same `--market` on later sells. Show/history without a market filter are
  permitted only when the symbol/currency identifies one instrument; otherwise specify
  `--market`. Use `--market ""` on show/history to select the unset-market instrument.
- Symbols are uppercased by the service. Numeric tickers such as `005930` retain their
  leading zeroes. Currency is never inferred from a numeric ticker.
- `--date` requires a valid calendar date in exactly YYYY-MM-DD form and is stored as
  midnight UTC. Omitting it uses the service's current UTC timestamp.
- Fees default to zero. Numbers are parsed directly into Decimal, with all service
  precision limits and accounting rules retained. Amount displays do not truncate
  Decimal precision. Percentage returns are rounded to two decimal places for display
  only. Repeating average costs may therefore be long.
- `--current-price` is available only on show. It is a caller-supplied price in the
  position currency; it is neither fetched nor saved as a market-price observation.
- Recent means most recent **execution timestamp**, not import/recording time. Default
  limit is 10; any positive integer is accepted. Equal execution times use reverse
  append order. History uses forward execution order.

## Worked example

The following excerpts use one initially empty USD/US position. Transaction IDs are
represented by placeholders; actual output uses immutable UUIDs.

### Record a buy

```sh
python main.py portfolio buy NVDA 10 180 --date 2026-09-10 --fees 1.25 --currency USD --market US --asset-name Nvidia --note "Initial position"
```

```text
=== RECORDED TRANSACTION ===
BUY 10 NVDA @ 180 USD
Date: 2026-09-10T00:00:00.000000+00:00
Fees: 1.25 USD
Market: US
Transaction ID: <buy-id>
Asset name: Nvidia
Note: Initial position

=== CURRENT POSITION ===
Symbol: NVDA
Shares remaining: 10
Average book cost: 180.125
Remaining book cost: 1801.25
```

The complete position display also includes dates, totals, realized performance, and
explicitly unavailable market-value fields.

### Record a partial sale

```sh
python main.py portfolio sell NVDA 4 210 --date 2026-09-20 --fees 1 --market US
```

```text
=== RECORDED TRANSACTION ===
SELL 4 NVDA @ 210 USD
Date: 2026-09-20T00:00:00.000000+00:00
Fees: 1 USD
Market: US
Transaction ID: <sell-id>

=== CURRENT POSITION ===
Shares remaining: 6
Average book cost: 180.125
Remaining book cost: 1080.75

=== REALIZED PERFORMANCE ===
Realized P/L: +118.5 USD
Realized return: +16.45%
```

### Show the position with a manual price

```sh
python main.py portfolio show NVDA --market US --current-price 202.50
```

```text
Symbol: NVDA
Asset name: Nvidia
Market: US
Currency: USD
Shares remaining: 6
Average book cost: 180.125
Remaining book cost: 1080.75
Realized P/L: +118.5 USD
Realized return: +16.45%
Current price: 202.5
Market value: 1215
Unrealized P/L: +134.25
Unrealized return: +12.42%
```

Without `--current-price`, the same command shows:

```text
Current price: NOT PROVIDED
Market value: N/A
Unrealized P/L: N/A
Unrealized return: N/A
```

### List positions

```sh
python main.py portfolio list
```

```text
=== OPEN POSITIONS ===
SYMBOL  SHARES  AVG COST  CURRENCY  MARKET  REALIZED P/L
NVDA    6       180.125   USD       US      +118.5
```

`list --closed` shows closed positions instead. Neither list mode fetches prices or
adds a combined money total.

### Show execution history

```sh
python main.py portfolio history NVDA --market US
```

```text
=== NVDA TRANSACTION HISTORY ===
BUY 10 NVDA @ 180 USD
Date: 2026-09-10T00:00:00.000000+00:00
Fees: 1.25 USD
Market: US
Transaction ID: <buy-id>
Asset name: Nvidia
Note: Initial position

SELL 4 NVDA @ 210 USD
Date: 2026-09-20T00:00:00.000000+00:00
Fees: 1 USD
Market: US
Transaction ID: <sell-id>
```

History and recent also display linked analysis IDs whenever present.

## Linking to an analysis

```sh
python main.py portfolio buy NVDA 10 180 --market US --run-id YOUR_EXISTING_RUN_ID
```

The run must already exist. A linked trade prints its run ID, deterministic final
MAGI action, and each agent's position/availability. The link is optional, and a trade
is not required to agree with that analysis; this is a factual bookkeeping relationship,
not authorization or execution logic. Missing IDs produce `Analysis run ... was not
found.` and insert nothing. No historic analysis or trade is updated.

## Errors and operational behavior

Invalid argument syntax/values exit with status 2 and a concise argparse error.
Service validation, unknown positions, and unavailable storage exit with status 1.
Successful commands, empty lists/history, and help exit with status 0.

Overselling is rejected by the existing service, including backdated sales that would
invalidate later holdings. The message describes an execution-order shortfall rather
than misleadingly quoting today's shares as the shares available on an earlier date.

If a transaction commits but reading its resulting position fails, the transaction ID
is still printed along with `Transaction recorded; position display is currently
unavailable. Do not re-record this trade.` The command exits successfully because the
write succeeded. Unexpected programming errors are not hidden by a broad exception
handler.

Database location, append-only rules, Decimal accounting, and security checks are
unchanged. See [memory and portfolio documentation](memory-portfolio.md). Weighted-average
analytics are **not tax-lot accounting or tax reporting**.

## Verification and decisions before Phase 6

Unrestricted pytest collection and execution are offline-safe: the legacy connectivity
scripts run only when explicitly executed as scripts. All CLI tests use temporary
SQLite databases; no production trades or databases are created by tests.

Before adding market data, review the USD default on read commands, exact Decimal versus
shortened display preferences, UTC date conventions, execution-time meaning of recent,
and explicit market selection. Live prices must remain a separate service; this CLI
contains no quote retrieval or trading functionality.
