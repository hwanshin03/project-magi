# Phase 6A: read-only market data

Market data is opt-in. Normal MAGI analysis and `portfolio` commands retain their
existing behavior and never construct a Toss client. No account, holdings, cash,
execution-history, or order endpoints are implemented. Quotes and tokens never enter
SQLite. `.env` is read but never modified.

## Provider contract

`magi.market.base.MarketDataProvider` defines:

- `get_quote(symbol, market) -> Quote`
- `get_quotes(instruments) -> tuple[Quote, ...]` (default sequential implementation)
- `get_daily_candles(symbol, market, limit=30) -> CandleSeries`
- `get_minute_candles(symbol, market, limit=30, interval='1m') -> CandleSeries`
- `get_fx_rate(base_currency, quote_currency) -> FXRate`

Adapters validate wire data, return normalized immutable dataclasses, and raise
`MarketError(ErrorCode)` for expected failures. They must not attach raw requests,
responses, credentials, or exception text. Programming exceptions propagate.
`MarketDataService(provider=...)` normalizes caller identifiers and returns
`MarketResult(data, error)`: data alone on success, no data plus a code on failure,
or explicitly stale data plus a code after a failed refresh. Empty candle history
is successful and distinguishable from failure.

A future Kiwoom adapter implements the same methods and handles its own credentials,
symbol mapping, transport, limits, and parsing. The service, portfolio valuation,
and presentation modules need no Kiwoom-specific response fields. Provider selection
is dependency injection; there is no automatic fallback that could silently mix feeds.

## Schemas

All models are frozen dataclasses. Decimal values serialize to JSON strings.
Timestamps are timezone-aware; original offsets are retained (especially daily
candles' local trading dates). Missing quote timestamps remain `None`.

- **Quote:** symbol, market, currency, price, timestamp, provider, fetched_at,
  is_stale; optional asset_name, previous_close, absolute_change, percent_change,
  open, high, low, volume.
- **Candle:** symbol, market, currency, timestamp, open, high, low, close, volume,
  provider. OHLC and volume are Decimal; inconsistent OHLC ranges are rejected.
- **CandleSeries:** symbol, market, interval, immutable candles tuple, provider,
  fetched_at, is_stale. Candles are always chronological by timestamp.
- **FXRate:** base_currency, quote_currency, rate (Decimal), timestamp, provider,
  fetched_at, valid_until, is_stale. Rate must be positive.
- **MarketResult:** optional data and optional ErrorCode. Error identifiers stay
  English/language-neutral; user-facing messages live in presentation.py.

## Toss contract and limitations

Implemented against the official [OpenAPI 1.2.17 specification](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
and [integration/rate-limit guide](https://developers.tossinvest.com/), inspected
2026-09-26. No authenticated requests were made during implementation.

The adapter's endpoint allowlist permits only:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/oauth2/token` | OAuth client credentials |
| GET | `/api/v1/prices` | Current quotes; internally split into 200-symbol batches |
| GET | `/api/v1/candles` | Adjusted daily or one-minute OHLCV |
| GET | `/api/v1/exchange-rate` | Directional USD/KRW display exchange rate |

Explicit MAGI markets are `US` and `KR`. Alphabetic symbols are uppercased;
`005930` retains zeroes. Toss's REST endpoints accept symbols rather than a market
parameter; returned currency must match the requested market (US/USD or KR/KRW).
No market is guessed from numeric versus alphabetic symbols. Other markets fail
with UNSUPPORTED. The adapter does not resolve exchange-level ticker collisions.

The prices endpoint supplies only symbol, lastPrice, currency, and optional timestamp.
Quote name, previous close, change, OHLC, and volume therefore remain `None`;
they are not fabricated or taken from an unrelated session.

Toss candles arrive newest-first, at most 200 per page. The adapter follows the
provided `nextBefore` cursor unchanged, de-duplicates inclusive boundary candles,
rejects conflicting duplicates/nonadvancing cursors, and returns oldest-first.
Requests are bounded to 1–10000 candles with bounded pagination. The provider's
200-row page limit does not leak into the service. Fewer candles may be returned
when history ends. `history --days` means trading-day candles, not calendar days.
Adjusted prices are explicitly requested. One-minute timestamps represent candle
end times; daily timestamps represent local midnight. Only `1d` and `1m` exist.

FX is a reference/display rate, refreshed by Toss approximately each minute, not a
promise of an executable exchange rate. Each requested direction is fetched from
Toss and validated; KRW/USD is **not** silently derived by reciprocating USD/KRW,
since directional rates can differ. The returned `rate`, not `midRate`, is used.

## Authentication and resilience

Required environment variable names (empty placeholders in `.env.example`):

```dotenv
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
```

Environment takes precedence over repository `.env`. Client construction does not
request a token. First data retrieval POSTs form-encoded `grant_type=client_credentials`,
client_id and client_secret to the fixed official HTTPS endpoint. Tokens are kept
only in the provider instance; expiry uses a monotonic clock and a margin of the
smaller of 30 seconds or 10% of token lifetime. Close discards the token and closes
the HTTP client. Redirects and environment proxy inheritance are disabled.

There is no refresh-token flow. Expiry reissues a client-credentials token. A data
401 permits one reauthentication and one replay; another 401 ends with AUTHENTICATION.
Each network operation has at most three attempts, so even combined failures are
bounded. Toss permits one valid token per client: issuing another invalidates the
previous token. Reuse one provider/service instance; separate processes sharing
credentials can invalidate each other's tokens. Coordination across processes is
not implemented.

HTTP timeout is 10 seconds per transport phase. Only 429/500/502/503/504 and
HTTPX timeout/network/remote-protocol failures retry. Backoff is 1 then 2 seconds
plus 0–0.25 seconds jitter. Malformed data, auth failures, 404 and other permanent
statuses do not retry. A per-instance lock serializes token/data requests, with a
conservative two requests/second ceiling across groups (official documented limits:
AUTH 5, quotes 15, candles 20, FX 3 requests/second). Lower advertised limits and
exhausted-bucket reset headers further slow requests.

Retry-After and reset delays are respected. A requested wait above ten seconds
returns a coded failure instead of sleeping for a long time or retrying early;
the instance retains the cooldown. Limits apply per client at Toss, so multiple
processes still need coordination. No concurrent fan-out or streaming is used.

## Cache and freshness

The service is a synchronous, single-caller cache, bounded to 256 entries with
least-recently-used eviction. Cache keys include instrument/market, interval/count,
or FX direction. Quote TTL is 15 seconds, daily candles 300 seconds, minute candles
15 seconds, and FX 30 seconds. Repeated calls inside TTL do not hit the provider,
even when the source itself is old; batching only requests cache misses.

`fetched_at` is the original fetch time. A quote with no source timestamp or a source
age over 60 seconds is marked stale, including quiet/closed-market quotes. This is
conservative: no market-calendar inference is attempted. FX is stale after its
provider validity window. Candle staleness describes snapshot-fetch freshness,
not the age of each historical candle. A failed refresh retains cached data with
`is_stale=True` and the error code; it never makes old data appear live. Stale fallback
has no hard age cutoff in this phase, so consumers must inspect timestamp/fetched_at
and is_stale before using it. Cache is not persisted, and a new CLI process starts
with an empty cache. No automatic background refresh is performed.

## Portfolio and currencies

Use a separate read-only wrapper:

```python
from magi.market.service import MarketDataService
from magi.market.toss import TossProvider
from magi.market.valuation import PortfolioValuationService
from magi.portfolio import Portfolio

with TossProvider() as provider:
    market = MarketDataService(provider)
    valuation = PortfolioValuationService(Portfolio(), market)
    result = valuation.get_position_with_market_data(
        'NVDA', market='US', currency='USD', display_currency='KRW')
```

Calling this example performs live market requests; automated tests use fake transports.
`PositionValuation.position` uses the existing ledger's Decimal accounting for price,
market value, unrealized P/L, and return. `quote`, `fx`, and errors retain provenance.
`is_stale` propagates from either price or conversion rate. Missing quotes return
an unvalued base position; missing FX keeps native valuation and leaves display
values absent. Unknown positions return None. Ambiguous ledger identities remain
errors. No historical transaction is updated, and no position total across currencies
is invented. Native price/currency never change: display_price and display_market_value
are separate. `convert` validates direction and uses Decimal arithmetic. Ledger
precision limits still apply; a quote outside them cannot value a position.

`presentation.LABELS` supplies a small English/Korean field-label map (Current Price /
현재가, Daily Change / 일간 등락, Volume / 거래량). This is groundwork, not a complete
translation system. Internal fields, currencies, and error identifiers never translate.

## Explicit manual CLI

These commands contact Toss when valid credentials are available:

```sh
python main.py market quote NVDA --market US
python main.py market quote 005930 --market KR
python main.py market fx USD KRW
python main.py market fx KRW USD
python main.py market history NVDA --market US --days 30
python main.py market candles NVDA --market US --interval 1m --limit 30
```

Output is normalized JSON including timestamps and staleness; Decimal amounts are
strings. Expected failures print only safe presentation messages. Exit status is 0
when data is returned (including clearly marked stale data), 1 for unavailable data,
and 2 for invalid arguments. `python main.py market --help` constructs no client.
Existing `python main.py` and `python main.py portfolio ...` are unchanged.

## Offline verification and first live request review

`tests/test_market.py` uses only synthetic credentials, httpx.MockTransport, fake
clocks/sleeps, and temporary SQLite databases. Socket tripwires prevent live calls.
All legacy tests remain import-safe. No live test utility runs during discovery.

Before the first explicitly authorized live request, review Toss account/API access
eligibility and registered egress IP, the currently published specification and
limits, environment-variable availability, and the single-token-per-client caveat.
Avoid sharing these credentials with another running token issuer. Start with one
quote command and inspect currency, source time and staleness. Real provider access,
entitlements, IP allowlisting, and production payload compatibility remain unverified.
No broker account or order permission is used by this implementation.
