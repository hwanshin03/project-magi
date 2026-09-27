# Live portfolio valuation (Phase 6E)

`portfolio live` is the only new command that requests live data. It reads the
existing schema-v3 ledger using SQLite `mode=ro` and `query_only`; it never creates,
migrates, or writes a database. Existing offline portfolio commands are unchanged.
No schema migration, price persistence, trade execution, or order endpoint is added.

```sh
python main.py portfolio live
python main.py portfolio live --currency KRW
python main.py portfolio live --language ko --currency KRW
python main.py portfolio live --language en --currency USD
python main.py portfolio live --broker TOSS --account SAFE_REF --language ko --currency KRW
python main.py portfolio live --dust-threshold 5
python main.py portfolio live --dust-threshold 5 --hide-dust
```

The CLI defaults to Korean (`ko`) and preferred display currency KRW. Native
prices and amounts always remain visible; `--language en` and `--currency USD`
override these defaults independently. The valuation service itself still supports
native-only views when no display currency is requested.

Without account options the view includes all open ledger positions, with separate
account rows. Both account flags must be supplied together. References are masked
in ordinary output. An absent or incompatible database produces a read error;
valuation never initializes or upgrades it. An empty ledger makes no live requests.

## Sources and calculations

`magi.portfolio_valuation.PortfolioValuationService` accepts a portfolio reader,
MarketDataService, and optionally BrokerService. Its immutable PortfolioView
contains position valuations, separate native-currency totals, an optional
converted total, and reconciliation warnings. The CLI shares one in-memory Toss
session between market and broker services; it never persists OAuth credentials.

Quantity and cost basis always come from the MAGI ledger. Current prices come
from market quotes. Broker holdings are used only for reconciliation. Mismatches
and unavailable reconciliation are warned about, without replacing ledger values.
Manual accounts do not require broker reconciliation.

Arithmetic uses Decimal in an 80-digit context:

- market value = ledger quantity × native quote price
- unrealized P/L = market value − remaining book cost
- unrealized return = unrealized P/L / remaining book cost (unknown for zero basis)
- converted amount = native amount × directional FX rate

Native prices and amounts are never overwritten. USD→KRW and KRW→USD each request
the exact directional rate from MarketDataService; this layer does not implicitly
invert rates. Same-currency display uses identity conversion without an FX request.
Rates are reused per currency pair within a view, with source, timestamp, and stale
state attached. Quotes are reused per instrument within a view.

Quote failure leaves that position's live fields unavailable and retains its
ledger basis and history. FX failure preserves native valuation and leaves converted
fields unavailable. Native totals never mix currencies. A total requiring missing
quotes/FX is marked incomplete and its affected amounts are unavailable, rather
than silently reporting a partial sum. Stale values are retained and explicitly
marked; stale state propagates to totals. A converted total is an estimated display
value, not a ledger amount. No historical FX-based realized performance is inferred.

## History and presentation

Realized P/L and realized return refer only to recorded activity since tracking
began, for the displayed open positions. Closed-position performance is still
available through the existing offline portfolio commands; it is not added to the
open-position valuation totals. Opening balances preserve unknown original purchase
dates and unknown pre-tracking realized P/L. Tracking start is never relabeled as a
purchase date. Subsequent tracked sells contribute only known realized performance.

English and Korean render exactly the same valuation objects. Database values,
enums, and accounting identifiers remain English and language-independent. Asset
names use recorded/provider names; they are not automatically translated.
`gain_state` exposes POSITIVE, NEGATIVE, NEUTRAL, or UNKNOWN for future GUI use.
No ANSI colors are used. Monetary gains/losses retain explicit signs.

Display rounding only: USD two decimal places, KRW whole won, ROUND_HALF_UP;
percentages are ratios × 100 with two decimal places. Underlying Decimals are
unchanged. Very small USD values can round to $0.00 in display; ledger precision
is retained.

Dust classification is disabled by default. `--dust-threshold N` marks positions
whose native market value is less than N. The threshold is in each position's
native currency (e.g. 5 USD for USD positions, 5 KRW for KRW positions), so choose it
with care for a mixed-currency view. This is a presentation preference, not a risk
or trading rule. `--hide-dust` hides rows only; all totals and ledger events remain
unchanged. Unavailable valuations are never classified as dust.

## Safety and verification

Tests use synthetic quotes, synthetic broker identities, temporary databases, and
network tripwires. Production data is never a test fixture. Live verification is a
separate explicitly authorized step. Schema remains v3; ordinary read-only
valuation needs no backup because it cannot modify production data.

## Price provenance

`market_data_provider` identifies the quote source and `price_role` independently
identifies its purpose. Toss defaults to `BROKER_PRICE`; unknown providers default
to `UNSPECIFIED`, never automatically to a research/reference price. A future
caller can explicitly configure provider roles through `price_roles`, including
`REFERENCE_MARKET_PRICE`, and retain separate immutable valuation results without
overwriting broker prices. This adds no provider and fetches no additional quote.
Recorded BUY/SELL execution prices remain exclusively in transaction history.

FX output identifies the directional rate, provider, timestamp, and freshness.
Converted amounts are approximate display values, not claimed execution prices or
actual brokerage exchange rates. Signed zero P/L has a localized neutral label;
positive/negative values retain their signs independently of future GUI colors.
