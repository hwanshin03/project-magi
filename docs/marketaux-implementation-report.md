# Phase 7D.1 implementation report

Status: offline implementation complete; ready for review before a separately
authorized, controlled live test. No live Marketaux request was made. No commit
or push was performed.

## 1. Every changed or added file

Modified:

- `.env.example` — empty Marketaux placeholder only.
- `README.md` — adapter overview and guide link.
- `magi/research/cli.py` — dispatch explicit `research news` commands.
- `magi/research/news/base.py` — update provider contract documentation.
- `magi/research/news/classification.py` — compatible source/evidence-only normalization and provenance.
- `magi/research/news/deduplication.py` — explicit provider similar-ID associations, retaining members.
- `magi/research/news/service.py` — optional events and honest live retrieval/pack timestamps.

Added:

- `magi/research/news/providers/__init__.py`
- `magi/research/news/providers/marketaux.py`
- `magi/research/news/providers/transport.py`
- `magi/research/news/cli.py`
- `tests/fixtures/marketaux/entities.json`
- `tests/fixtures/marketaux/news.json`
- `tests/fixtures/marketaux/errors.json`
- `tests/test_marketaux.py`
- `docs/marketaux-news.md`
- `docs/marketaux-implementation-report.md` (this report)

## 2. New modules

`marketaux.py` contains provider entity resolution, article normalization, authority
policy, metadata and caching. `transport.py` contains fixed-endpoint authenticated
transport, sanitized errors, status hints, logging isolation and bounded retry.
`providers/__init__.py` exports the adapter and result/error types. `news/cli.py`
provides manual entity/latest/pack inspection.

## 3. Endpoint allowlist

Only GET `https://api.marketaux.com/v1/entity/search` and
GET `https://api.marketaux.com/v1/news/all`. Redirects and arbitrary endpoints
are rejected. No article destination or website is fetched by the adapter.

## 4–5. Authentication and token sanitization

Explicit provider construction reads `MARKETAUX_API_TOKEN` from environment,
optionally loading the root `.env` without overwriting environment values.
The real file is never modified. Missing configuration and HTTP 401 have fixed,
clean errors. The query credential remains private to transport memory.

No raw request/response or exception URL is returned. HTTPX/httpcore diagnostics
are suppressed during requests; known provider error codes alone are retained.
Echoed raw or URL-encoded token values in successful data are rejected. Existing
research text/URL credential checks remain active. Serialization tests verify
absence of token values and credential-bearing URLs.

## 6–7. Entity resolution and Korea

Country/type/exchange filters are applied both to the request and locally. Exact
symbols are preferred; otherwise only returned base-symbol or exact searched-name
matches qualify. Multiple plausible or incomplete results are AMBIGUOUS; no result
is NOT_FOUND. Unresolved identities do not trigger news requests.

NVDA uses US/equity defaults. Korea requires explicit country `kr`. MAGI ticker
`005930` is never converted to a guessed suffix. The exact provider-returned
symbol/name/exchange/country and original ticker are retained. The Korean fixture
uses `005930.FIXTURE`, which is intentionally not a real proposed mapping.

## 8–10. Article, source, and evidence mappings

UUID becomes external identity; title/description become bounded plain-text title
and summary; snippet becomes bounded excerpt metadata. URL stays the canonical
original article URL, source stays the actual publisher, and language/publication
and MAGI retrieval times are preserved. Entities and sanitized highlights are
bounded structured metadata, never the raw payload.

ResearchSource is NEWS / MARKETAUX with publisher, authority, UUID, original URL,
and timestamps. EvidenceItem is an attributed REPORTED_CLAIM using only provider
description or title, Category.OTHER, source ID, ticker/subject, publication time,
and source-field/provider provenance. No factual synthesis or LLM extraction is
performed. Events and catalysts are absent for these unclassified articles.

## 11–12. Sentiment and authority

Numeric entity/highlight sentiment is marked PROVIDER_SUPPLIED_SENTIMENT with
provider, source field, symbol scope and MARKETAUX_SENTIMENT/MARKETAUX_HIGHLIGHT
provenance. It never changes MAGI sentiment, votes, recommendations or catalysts.
Match score is not a probability. Relevance is at most DIRECTLY_RELATED for a
matched entity with a title highlight; otherwise MENTION_ONLY.

Authority defaults to TERTIARY. Explicit reviewed publisher hostname policies
can assign SECONDARY or PRIMARY only when source and URL hostname match exactly.
Aggregator membership and headline wording do not determine authority.

## 13–14. Deduplication and caches

Existing content IDs remove exact repeats. UUID/URL and explicit similar-ID
associations group already-normalized articles while retaining distinct versions
and publishers. Nested similar articles do not bypass the request limit. Similarity
is not proof of corroboration or identical reporting.

Per-instance in-memory LRU caches: entity resolution 128 entries / one hour;
news 64 entries / five minutes. Full query/resolution context participates in
keys. Original publication/retrieval times remain intact; ambiguity expires.
There is no disk cache or persistent mapping.

## 15. Rate and usage limits

15-second transport timeouts; at most three attempts. Retry only 429/500/503 and
network/timeout failures with 1/2-second backoff. Numeric Retry-After is bounded;
long waits return control. 400/401/402/403/404 do not retry. Optional numeric usage,
rate and reset headers are status hints, not hardcoded subscription rules.
Fewer returned articles than requested is accepted. CLI defaults to three.

## 16. NewsEvidencePack

Resolution → bounded news page → normalized articles → existing NewsService →
ResearchSource/EvidenceItem → deduplication/validation → existing bounded selection.
Compatible event-less normalization avoids invented events. Models/schema and
legacy annotated news remain intact. Serialization round trips and trust-boundary
rendering pass. Existing selected-view and presentation character limits remain.

## 17. CLI syntax

Do not run these live commands without the separate authorization contemplated by
the request:

```sh
python main.py research news entity NVDA --country us
python main.py research news entity 005930 --country kr --market KR
python main.py research news entity 005930 --country kr --search 'Samsung Electronics'
python main.py research news latest NVDA --hours 72 --limit 3
python main.py research news pack NVDA --hours 72 --limit 3 --provider marketaux
```

Korean presentation remains default; `--language en` changes it. Provider-language
filters use `--news-language`. Normal no-argument application behavior is unchanged.

## 18. Copyright and storage

Only short supplied descriptions/excerpts, identifiers, provenance and original
links are retained. No full body storage, scraping, news schema or production DB
writes. Review provider/publisher licensing and redistribution rights before
friend/private-beta sharing. Phase 7E agent analysis is future work.

## 19–20. Offline and regression results

- New Marketaux tests: **91 passed**.
- Full unittest discovery: **703 passed**, including all existing 612 tests.
- Full pytest discovery: **703 passed**, including parameterized subtests.
- Both complete suites also run with socket connection/DNS and real HTTPX network
  transport blocked before discovery; mock transports remain available.
- Syntax parsing/compilation passed for changed Python files.
- `pip check`: no broken requirements.
- `git diff --check`: passed.
- CLI help works without constructing a provider or sending requests.

Pytest reports one existing Google GenAI/Pydantic deprecation warning. No runtime
dependency pin was changed; pytest was installed in the development virtualenv.
SEC/OpenDART implementations, CompanyResearchSnapshot, agents, voting and portfolio
code are unchanged. Phase 7D models/schema are unchanged; shared pipeline changes
are limited to the compatible behavior described above.

## 21–22. Protected data and no live requests

The real `.env` matches its pre-implementation SHA-256 baseline; its contents were
not printed. Git still ignores it. No production database existed in this clone,
and none was created or copied. The production data file inventory matches the
baseline. No Marketaux API or original-article request was made. Verification used
synthetic fixtures and mocks only. No commit or push occurred.

## 23. Review before controlled live verification

For NVDA/US, authorize and review an entity search first, followed only after a
unique match by one news page with limit three. Verify actual endpoint/filter
availability, entity fields, usage headers and account quota.

For Samsung/KR, inspect provider entity results before accepting any symbol.
If numeric ticker search is inconclusive, separately review a company-name search.
Do not infer suffixes or accept ambiguous/truncated results. Korean coverage,
provider symbol and news availability remain unverified. Consider the possible
three-attempt transient retry bound when approving a live request budget. Stop
on authentication, access, usage, ambiguity or unexpected payload failures.

See [the implementation guide](marketaux-news.md) for detailed policies and limits.
