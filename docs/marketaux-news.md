# Phase 7D.1: Marketaux news adapter

Marketaux is an explicitly invoked live provider behind `NewsProvider` and
`NewsService`. Nothing calls it during normal `python main.py` analysis. There
is no integration with the three agents, voting, recommendations, Daily Picks,
forecasts, paper trading, or the portfolio database. Imports perform no I/O.

## Requests and authentication

Only these fixed HTTPS GET endpoints are permitted:

- `https://api.marketaux.com/v1/entity/search`
- `https://api.marketaux.com/v1/news/all`

The contract reference is the [official API documentation](https://www.marketaux.com/documentation).
No website scraping, destination-article fetching, redirect following, or
arbitrary URL request method is implemented. All implementation verification
uses synthetic fixtures and `httpx.MockTransport`, never a live API call.

Explicit provider construction loads the repository `.env` without overriding
existing environment values and reads `MARKETAUX_API_TOKEN`. An empty value
returns `MARKETAUX_CONFIGURATION_ERROR`. The real `.env` is never written.
The template contains an empty placeholder; no token belongs in Git.

Marketaux requires `api_token` in the query string, so request URLs are secrets.
The private transport bypasses HTTPX Client's URL logging and temporarily
suppresses HTTPX/httpcore request diagnostics under a synchronous lock, restoring
filters afterward. This also suppresses other library diagnostics during that
short request window. It never returns a Request/Response or raw provider error.
HTTP failures expose a fixed MAGI error, HTTP status, and only an allowlisted
provider error code. Unknown provider error messages/codes are discarded.
Successful payloads containing the configured token, including URL-encoded
variants, are rejected. Normalized text also passes the existing research
credential boundary. Credentials stay only in transport/request memory, not
articles, snapshots, logs, caches of results, URLs in research sources, or files.
Do not add external request logging, HTTP tracing, or debugging that dumps
request objects or local variables with credentials.

Connect/read/write/pool timeouts are 15 seconds. Each operation has at most
three attempts, with 1- and 2-second exponential backoff. Only 429, 500, 503,
network failures, and timeouts retry. Numeric Retry-After hints can increase the
wait up to 15 seconds; a longer hint returns the error for later caller action.
400/401/402/403/404 do not retry. No automatic pagination or concurrency is used.
Responses are bounded to 2 MB; a news page has at most 1,000 input articles and
an entity page at most 100. Malformed responses fail closed with
`MARKETAUX_INVALID_RESPONSE`; there is no silent malformed-article substitution.

Optional numeric `X-UsageLimit-Remaining`, `X-RateLimit-Remaining`, reset and
Retry-After headers are exposed as status hints. Hyphenated variants are also
accepted. Missing headers mean unknown, not zero. These hints are not universal
subscription limits; their actual availability still needs live verification.
A request for 20 or 50 articles returning only three succeeds. CLI defaults to
three articles to minimize quota use, not because every account has that limit.

## Entity resolution

`search_entities` accepts search text or explicit symbols plus country, type,
and exchange filters. It returns normalized provider entities, not MAGI global
issuer identities. Country/type/exchange filtering is repeated locally.

Resolution searches using the original MAGI ticker or an explicit company-name
search. Exact symbol matches take precedence. Otherwise a returned provider
symbol whose dot-separated base equals the ticker, or an exact searched company
name, may match. Country and type still have to agree. One candidate resolves;
multiple equally plausible candidates are `AMBIGUOUS`; no match is `NOT_FOUND`.
A provider page advertising additional unseen candidates is `AMBIGUOUS` rather
than permission to guess or fetch unlimited pages. No news request follows an
unresolved entity. Market/country contradictions are rejected.

NVDA defaults to country `us`, type `equity`. Korean calls must explicitly use
`--country kr` (and optionally `--market KR`). The original string `005930` is
preserved, including zeros. The adapter never constructs `005930.KS` or another
suffix. It uses only a symbol actually returned by entity search. If ticker
search cannot confirm a mapping, use an explicit company-name search and review
the candidates. The fixture `005930.FIXTURE` is deliberately synthetic and is
**not** a proposed Samsung mapping. The first controlled live verification confirmed a provider-returned Samsung symbol
and a small non-Korean-language news sample; see the [live verification report](marketaux-live-verification.md).
That observation is not a hardcoded mapping or proof of comprehensive coverage. Article metadata retains MAGI ticker, provider symbol/name,
exchange, country, market, and mapping retrieval time.

Resolution uses a bounded 128-entry in-memory LRU cache with a one-hour TTL. Keys
include ticker, search text, country, market, exchange, and type. Ambiguous and
empty results expire too. News uses a 64-entry, five-minute cache keyed by the
full query and resolution context. Both use the injected clock, invalidate on
clock rollback, preserve original retrieval/publication times, and never persist.
Caches are per provider instance; separate CLI invocations do not share them.

## News normalization and trust

One explicit page supports resolved symbols, `published_after`,
`published_before`, languages (`language` on the API), countries,
`filter_entities=true`, page, and limit. Without a text search, the API's default
publication-time ordering applies; no undocumented sort value is sent. Request
dates are converted to UTC whole seconds (`YYYY-MM-DDTHH:MM:SS`), without fractional
seconds or a timezone suffix. Internal timestamps remain timezone-aware.
`MarketauxProvider.get_news(ticker, published_after=..., limit=...)` resolves
underneath; `NewsService(provider).collect(NewsQuery(...))` builds the pack.
The service's pack creation time is the later of the requested publication cutoff
and actual injected retrieval time, so slow network responses are not backdated.

| Provider field | Normalized field/policy |
| --- | --- |
| `uuid` | `NewsArticle.external_id`, `ResearchSource.external_id` |
| `title` | Plain-text title, at most 1,000 characters |
| `description` | Optional attributed summary, at most 1,000 characters |
| `snippet` | Optional provider excerpt metadata, at most 500 characters |
| `url` | Canonical original article URL, never the API request URL |
| `source` | Actual publisher/domain, separate from provider `MARKETAUX` |
| `published_at` | Aware provider timestamp; missing stays unknown |
| retrieval clock | MAGI `retrieved_at`, never substituted for publication |
| `language` | Provider language code |
| `entities` | At most 20 entities' identifiers, names, exchange, country, type, industry, scores |
| `highlights` | At most five per entity, 300 characters each, stripped markup |
| `similar` | At most 20 associated UUIDs; nested articles are not imported |

Malformed/naive/future publication times fail closed. Undated articles can appear
in unfiltered packs with UNKNOWN freshness; a `since` filter excludes them
because their inclusion in that period cannot be established. Existing explicit
clock freshness classes remain unchanged.

HTML tags are stripped, script/style contents removed, and highlights are plain
UNTRUSTED RESEARCH DATA. Match scores remain numeric provider scores, not
probabilities. A matched resolved symbol/country/exchange with a title highlight
is DIRECTLY_RELATED; otherwise relevance is conservatively MENTION_ONLY. Nothing
is automatically PRIMARY_SUBJECT. Other provider entities are retained as
metadata, not silently translated into MAGI tickers.

Entity/highlight sentiment keeps its numeric value, provider, source field and
symbol scope, marked `PROVIDER_SUPPLIED_SENTIMENT`, `MARKETAUX_SENTIMENT`, or
`MARKETAUX_HIGHLIGHT`. It never changes MAGI sentiment, voting, an event, catalyst,
or risk classification. Classification provenance is `MARKETAUX_ENTITY_METADATA`.

Authority defaults to TERTIARY. An explicit hostname-to-Authority policy may
assign SECONDARY to a reviewed professional publisher or PRIMARY to an
independently verified official publisher. Both the article URL hostname and
reported source must exactly match that configured domain. There is no built-in
publisher endorsement list or title-based authority inference.

ResearchSource uses NEWS, MARKETAUX, the actual publisher, original URL, UUID,
publication and retrieval times. EvidenceItem is an attributed REPORTED_CLAIM
using only the description or headline, with Category.OTHER, source ID, ticker,
publication time, and provider/source-field provenance. It does not verify the
claim or invent extracted facts. `event_extraction=NONE` is a compatible metadata
annotation: the existing normalization/validation pipeline creates source and
evidence but no ResearchEvent, cluster, or catalyst. Existing annotated Phase 7D
articles retain their behavior and models/schema are unchanged.

## Deduplication, selection, and storage

The existing content identity removes exact repeats. UUID and canonical URL
matches group versions, preserving their provenance. Explicit `similar` UUID
associations group already-normalized top-level articles without importing
nested objects. Similarity is not proof of equivalent facts, corroboration, or
syndication. Independent publisher members and official sources remain present;
grouping does not create events or upgrade authority. Atomic groups can be
omitted when a selected view has insufficient capacity, as in Phase 7D.

All articles pass through NewsService/build_news_pack and existing deduplication,
source/evidence validation, selection, and untrusted presentation. CLI selected
views retain the existing default maximum of 20 articles, 40 evidence items,
20 catalysts, 20 clusters, and 10 articles per publisher. Provider response limits
do not replace those policies. Renderers retain their character bounds and fail
closed if the selected content still exceeds them. Full serialization round trips
are supported; pack output uses the existing versioned format.

There is no news database schema, disk cache, full body field, source-page fetch,
or portfolio write. Provider bodies/raw objects and unknown fields are discarded.
Short supplied excerpts are not evidence of redistribution permission. Review
Marketaux and publisher licensing, attribution and redistribution rights before
sharing data with friends or a private beta. Phase 7E may later analyze bounded
cited evidence; no agent integration or investment inference is part of this phase.

## CLI (live only when separately authorized)

```sh
python main.py research news entity NVDA --country us
python main.py research news entity 005930 --country kr --market KR
python main.py research news entity 005930 --country kr --search 'Samsung Electronics'
python main.py research news latest NVDA --hours 72 --limit 3
python main.py research news pack NVDA --hours 72 --limit 3 --provider marketaux
```

Korean is the default presentation language. `--language en` changes presentation;
`--news-language en` filters provider articles. `--exchange` and `--type` refine
entity search. `entity` reports candidates/status; `latest` renders a selected
view; `pack` additionally prints its versioned JSON. A help request makes no client.
No arguments to the application retain the previous normal analysis behavior.

## Offline verification and live-test prerequisites

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py'
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -B -m pytest -q
.venv/bin/python -m pip check
git diff --check
```

Pytest is a development test runner; it does not add a production dependency.
New fixtures use example.invalid domains and synthetic data only. Tests cover
security/logging, fixed endpoints, timeout/retry/usage errors, caches, US/Korean
resolution, ambiguity, normalization, optional/malformed fields, provider scores,
authority, deduplication, selection, freshness, serialization, and CLI routing.

Before a separately authorized controlled live test, review the exact planned
requests and quota: entity search first, then one news page of at most three
articles only after deterministic resolution. NVDA/US mapping, Samsung/KR
coverage and exact provider symbol, actual error/usage headers, and current
plan endpoint/filter access still require verification. Stop for ambiguity,
authentication/access/usage errors or unexpected payload shape. No live result
or Korean provider symbol has been claimed by offline testing.

The [first controlled live verification report](marketaux-live-verification.md) records
the subsequently authorized US/Korean checks, timestamp-format fix, usage and coverage limitations.
