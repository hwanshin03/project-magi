# Phase 6B: broker read-only visibility

Broker snapshots describe current external account state. MAGI's append-only ledger
records historical transactions. Neither replaces the other. This layer never
creates transactions, adjusts average costs, imports executions, writes snapshots
into analysis memory, or places/modifies/cancels orders.

## Supported Toss capabilities and limits

The adapter follows the official [OpenAPI specification](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
(version 1.2.17) and [API guide](https://developers.tossinvest.com/). It implements only:

| Endpoint | Purpose |
| --- | --- |
| `POST /oauth2/token` | Shared OAuth client-credentials authentication |
| `GET /api/v1/accounts` | Account discovery |
| `GET /api/v1/holdings` | Holdings and native-currency holdings summaries |

Holdings use the necessary `X-Tossinvest-Account` reference header. Currently Toss
returns brokerage accounts and KR/US equities; this is not a complete statement of
all possible assets. No standalone cash-balance endpoint exists in these read-only
account/asset groups. Buying power belongs to the order-info surface and is deliberately
excluded, as are sellable quantity and commissions. Accordingly `broker balances`
returns UNSUPPORTED without contacting Toss. **Unsupported cash is not zero cash.**

Account summary means the holdings endpoint's currency-separated book cost, gross
market value, gross unrealized P/L, and returned holding count. It is not a net-worth
or cash-inclusive account total. USD and KRW are never added together, and the
provider's combined KRW-based return is deliberately not presented as a native return.

The market adapter retains its market-only endpoint allowlist; the broker adapter
permits only accounts/holdings. The shared transport additionally restricts all
requests to the fixed official HTTPS base and the union of these read-only endpoints,
plus OAuth. No order URL, trading method, or arbitrary public HTTP request API exists.

## Provider contract and models

`BrokerProvider` exposes `get_accounts() -> AccountList`,
`get_holdings(account) -> HoldingsSnapshot`,
`get_cash_balances(account) -> CashSnapshot`, and
`get_account_summary(account) -> AccountSummary`. Unsupported capabilities raise
`BrokerError(UNSUPPORTED)`. Cash support defaults to false via `supports_cash_balances`.
`summary_from_holdings` indicates that a summary can be derived from the same snapshot;
Toss enables this so BrokerService reuses its holdings cache.

Adapters must validate their wire data, normalize symbols and currencies, return
immutable dataclasses, and raise sanitized coded BrokerError for expected failures.
Business logic never sees provider field names. Programming errors propagate.
A future KiwoomBrokerProvider implements this interface, supplies its own authentication
and parsing, and enables cash support only when actually available. It must preserve
native currencies and explicit market identities rather than infer them from tickers.

Frozen data models:

- **BrokerAccount:** account_id (internal safe provider reference, excluded from repr),
  provider, fetched_at, optional account_name/account_type/base_currency, is_stale.
- **AccountList:** accounts tuple, fetched_at, is_stale.
- **Holding:** symbol, market, currency, quantity, provider, internal account_id,
  fetched_at, is_stale; optional asset_name, available_quantity, average_cost,
  current_price, book_cost, market_value, unrealized_pnl, unrealized_return.
- **HoldingsSnapshot:** holdings tuple, per-currency totals tuple, provider,
  internal account_id, fetched_at, is_stale.
- **CurrencySummary:** currency, optional total_book_cost, total_market_value,
  total_unrealized_pnl.
- **CashBalance:** currency, provider, internal account_id, fetched_at, is_stale,
  optional cash_balance/available_cash/buying_power. **Toss does not fabricate rows.**
- **CashSnapshot:** balances tuple, fetched_at, is_stale. Other providers can return
  USD/KRW independently; an empty tuple is not interpreted as zero money.
- **AccountSummary:** native totals, asset_count, provider, internal account_id,
  fetched_at, is_stale.
- **BrokerResult:** data and/or BrokerErrorCode. Expected failures return no data
  and an error, or explicitly stale cached data and an error.

Money, quantities and returns use Decimal. Returns are ratios: `0.25` means 25%.
Negative P/L/returns are valid. Nonfinite numbers, binary floats, malformed structures,
and inconsistent Toss market/currency pairs are rejected. Missing optional values
stay None. Native holdings are mapped from `marketCountry` and `currency` explicitly;
alphabetic symbols become uppercase and numeric Korean symbols retain leading zeroes.
Gross values are used consistently; after-cost metrics are not substituted silently.
Available quantity is None because retrieving it would require an excluded endpoint.

## Authentication reuse and privacy

`magi.toss.TossSession` holds the existing OAuth, timeout, bounded-retry, and rate-limit
implementation. Credentials remain `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET`; environment
values take precedence over repository `.env`, which is never modified.
Construction and imports make no network calls. Tokens stay in memory only.

Expiry and one-time 401 reauthentication behavior are retained, as are three-attempt
transient retries and 10-second transport timeouts. Requests serialize through a lock.
Account requests are paced at at most one per second; other existing paths retain
the conservative two-per-second ceiling. Provider retry/reset headers still apply.

For combined broker and market views, share a single session explicitly:

```python
from magi.toss import TossSession
from magi.market.toss import TossProvider
from magi.market.service import MarketDataService
from magi.broker.toss import TossBrokerProvider
from magi.broker.service import BrokerService

with TossSession() as session:
    market = MarketDataService(TossProvider(session=session))
    broker = BrokerService(TossBrokerProvider(session=session))
    # Calling service methods here is an explicit live operation.
```

Adapters close only sessions they own. The outer owner closes shared sessions.
Separate processes do not share tokens: Toss token issuance can invalidate the token
held by another process with the same credentials. Avoid simultaneous independent
issuers. No refresh token is persisted and no broker credentials enter SQLite.

Toss accountNo is discarded during normalization; even its last digits are not retained.
Only accountSeq is kept in memory as the necessary internal account_id. Its dataclass
repr is hidden. Do not serialize internal account references for public presentation.
CLI output uses `Account 1 [identifier masked]`, not accountNo or accountSeq.
Account discovery also displays a derived opaque local portfolio reference. Types
are provider enums; account names and base currencies remain None when absent.
No raw broker responses, authorization headers, tokens, or full account numbers are
logged. Holdings themselves are sensitive financial information: the explicit CLI
prints normalized holdings to the terminal, not a log or database.

## Cache and failure behavior

BrokerService maintains a bounded 32-entry in-memory cache, with a default 10-second
TTL (configurable up to 60 seconds). Keys include operation, provider, and account
reference. A cache hit retains the original fetched_at. Failed refreshes preserve
cached snapshots and mark both the snapshot and its nested rows stale; an error code
is returned alongside them. No snapshot is persisted.

CLI displays fetched_at and is_stale. Fresh account discovery is required before
account selection. Reconciliation refuses stale or failed snapshots rather than
reporting a false MATCH or MAGI_ONLY. Stale fallback has no hard retention cutoff;
consumers must inspect freshness before using it. The service is synchronous and
intended for a single caller, not a distributed cache. Broker failures do not enter
normal MAGI analysis, the Portfolio CLI, or Market CLI control flow.

## Read-only reconciliation

`ReconciliationEngine.compare(ledger_positions, broker_result)` accepts in-memory
snapshots. It never calls transaction-writing methods. CLI loads the existing ledger
using a dedicated SQLite `mode=ro`, `query_only` connection, with no schema creation
or migration. A missing or unreadable ledger is UNAVAILABLE; it is not created or
assumed empty. The ledger must already have schema version 2; local Database initialization migrates v1.

Identity is **provider + safe account reference + symbol + market + currency**. Closed/zero positions are omitted.
Duplicates, unknown market mappings, and inconsistent broker account references are
AMBIGUOUS rather than silently combined. Explicitly different markets/currencies
remain separate. Comparison is limited to the matching local provider/account pair; manual and other
account positions are ignored. See [Phase 6C](account-aware-portfolio.md) for migration,
opaque local references, and broker-only previews without inferred transactions.

For each identity, the result includes both quantities and average costs, differences
(broker minus MAGI), cost_comparable, and status:

1. Unavailable/error/stale snapshot or unreadable ledger: UNAVAILABLE for the report.
2. Ambiguous identity: AMBIGUOUS, without computed differences.
3. Present only at broker or ledger: BROKER_ONLY or MAGI_ONLY; absent-side quantity is zero.
4. Different quantities: QUANTITY_MISMATCH (takes precedence over cost mismatch).
5. Equal quantities and different available average costs: COST_MISMATCH.
6. Otherwise MATCH. Missing broker cost means quantity-only MATCH with cost_comparable=false.

Comparison uses exact Decimal values with no hidden tolerance. The ledger includes
fees in average book cost; broker cost conventions, corporate actions, transfers,
and rounding may differ. COST_MISMATCH is an observation, not proof either source
is wrong. No reconciliation command fixes, deletes, or imports anything.

## Optional market/FX presentation

`enrich_holding(holding, market_data, display_currency=None)` returns an immutable
HoldingView without changing the original holding. Broker current price is preferred;
MarketDataService is queried only when it is absent. Identity/currency mismatches are
rejected. Derived display value, P/L and return use Decimal and explicitly identify
BROKER versus MARKET_DATA as the price source. Quotes and FX remain separate metadata.

Display conversions use the existing MarketDataService directional FX. Native price,
currency and values remain intact. Missing FX leaves display values absent; missing
quotes leave broker state available without manufactured valuations. Staleness
propagates from broker, quote, and FX. No cross-currency grand total is generated.
Enrichment is an explicit Python helper, not an implicit CLI network operation.

Presentation dictionaries map internal statuses into English and Korean, including
MATCH / Matched / 일치 and QUANTITY_MISMATCH / Quantity mismatch / 수량 불일치.
Models, reconciliation, and persistence identifiers remain language-neutral.

## CLI

These are explicit live read-only commands; do not run them in automated tests:

```sh
python main.py broker accounts
python main.py broker holdings
python main.py broker holdings --account 2
python main.py broker summary --account 2
python main.py broker balances
python main.py broker reconcile --account 2
```

A single discovered account is selected automatically. Multiple accounts require the
index displayed by `accounts`. Indices refer to the current account list, sorted by
internal reference for Toss; they are not durable identifiers across account changes.
Unknown/out-of-range selectors fail without a holdings request. All help commands
are offline. Toss balances reports unsupported without even authenticating.

Successful displays exit 0, including clearly flagged stale holdings. Expected
unavailable/unsupported/ambiguous selections and unavailable reconciliation exit 1.
Argument errors exit 2. Existing no-argument analysis, portfolio and market commands
are preserved.

## Verification and first live test

Tests use synthetic identifiers/credentials, httpx.MockTransport, fake clocks, socket
tripwires, and temporary databases. They cover OAuth sharing and expiry, privacy,
normalization, unsupported Toss cash, provider-neutral cash, reconciliation identity
and ambiguity, immutable ledger bytes, enrichment, staleness, and endpoint rejection.

No live account requests were made in Phase 6B implementation. Before the separately
authorized live test, confirm registered egress IP/access eligibility, ensure no other
process is issuing tokens with the same credentials, and begin with account discovery.
Confirm the selected account corresponds to the intended local ledger scope. Review
currency/return conventions and missing optional fields in a small holdings response.
Cash access remains out of scope unless a suitable non-order official endpoint appears.
