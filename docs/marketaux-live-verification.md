# Phase 7D.1 — first controlled live verification

Date: 2026-09-28. Verification ran from 23:15:17 to 23:20:15 UTC.
Scope: read-only NVDA/US and Samsung Electronics/KR only, 72-hour news windows,
limit three, one page each. No agents, recommendations, forecasts, trading or
production persistence were involved. No commit or push was performed.

## NVDA (requested report items 1–10)

| Check | Observed result |
| --- | --- |
| Entity resolution | PASS; one deterministic entity |
| MAGI ticker / market | NVDA / US |
| Provider symbol | NVDA |
| Provider entity name | NVIDIA Corporation |
| Country / type / industry | us / equity / Technology |
| Exchange / exchange_long | Both null in the provider response; not inferred |
| News query | PASS after the single date-format compatibility fix below |
| Articles / sources / evidence | 3 / 3 / 3 |
| Publishers | gurufocus.com (one publisher) |
| Languages | en |
| NewsEvidencePack | PASS |
| Freshness | BREAKING: 1; RECENT: 2 |
| Events / catalysts | 0 / 0, as intended |
| Duplicate groups / selected articles | 3 / 3; no observed top-level duplicate collapse |
| Relevance / source authority | All MENTION_ONLY / TERTIARY |
| Match scores | Present on the resolved entity in all three articles |
| Provider entity sentiment scores | Present in all three articles; kept as provider metadata |
| Provider highlights | Present in all three articles; 1, 5 and 1 retained after bounds |

Publication times observed (UTC): 2026-09-28 21:57:04, 21:57:25 and 22:59:52.
MAGI retrieval time: 2026-09-28 23:20:14.926970 UTC.

All three normalized articles had a UUID, title, description, snippet, original
URL, publisher, language, publication/retrieval timestamps, and one retained
resolved-entity metadata record. ResearchSource URLs equaled the canonical
original article URLs, never the API URL. No article text, URL list or raw
provider payload is reproduced or stored in this report.

## Samsung Electronics (requested report items 11–19)

| Check | Observed result |
| --- | --- |
| Entity resolution | PASS; one deterministic entity |
| Original MAGI ticker / market | **005930 / KR**, leading zeros preserved |
| Exact provider symbol | **005930.KS**, confirmed by live entity search; not guessed or hardcoded |
| Provider entity name | Samsung Electronics Co., Ltd. |
| Country / type / industry | kr / equity / Technology |
| Exchange / exchange_long | Both null in the response; not inferred |
| Search | Original ticker 005930 with country kr and type equity; no name fallback needed |
| News query | PASS |
| Articles / sources / evidence | 2 / 2 / 2 |
| Publishers | kyodonewsprwire.jp; manilatimes.net |
| Languages | ja; en — no Korean-language article observed in this sample |
| NewsEvidencePack | PASS |
| Freshness | RECENT: 2 |
| Events / catalysts | 0 / 0 |
| Duplicate groups / selected articles | 2 / 2 |
| Relevance / source authority | Both MENTION_ONLY / TERTIARY |
| Match scores | Present in both articles |
| Provider entity sentiment | Present for the English article; absent/null for the Japanese article |
| Highlights | One retained for the Japanese article and two for the English article |

Publication times observed (UTC): 2026-09-28 04:00:00 (Japanese) and 03:56:14
(English). MAGI retrieval time: 2026-09-28 23:20:15.772208 UTC.

Both articles supplied UUID/title/description/snippet/original URL, language,
publisher, timestamps and resolved-entity metadata. The pack retained MAGI's
005930/KR separately from the live provider symbol, name, country and exchange.
Korean and English presentation both rendered successfully; the Japanese article
text also survived normalization/serialization. This is not evidence of Korean
language news coverage.

Korean-market entity coverage works for this company, but the narrow sample shows
only mentions in two non-Korean-language sources. It is usable as supplementary
discovery, not proof of sufficient standalone Korean equity news coverage.

## Shared verification (requested report items 20–30)

### 20–21. Requests and usage

Exactly **five live Marketaux HTTP requests** were made:

| # | Step | Endpoint path | HTTP | Usage remaining | Rate remaining |
| --- | --- | --- | --- | --- | --- |
| 1 | NVDA entity | GET /v1/entity/search | 200 | 99 | 29 |
| 2 | NVDA news, initial format | GET /v1/news/all | 400 | 98 | 28 |
| 3 | NVDA news, corrected format | GET /v1/news/all | 200 | 97 | 29 |
| 4 | Samsung entity | GET /v1/entity/search | 200 | 96 | 28 |
| 5 | Samsung news | GET /v1/news/all | 200 | 95 | 27 |

The observed headers reported usage limit 100 and rate limit 30. Final remaining
values were **95** and **27**, respectively. No reset information was supplied in
the inspected headers. These are observations for this account and response,
not hardcoded universal limits or an inferred subscription-plan name.

No pagination, Samsung Group expansion, extra name search, additional article
fetch or completed entity re-query was performed. Request 2 was not retried by
the automatic transport because 400 is non-transient. After the offline fix,
only the failed news step was repeated; the verified NVDA entity was reused.

### 22–23. Compatibility issue and files changed in this live-verification task

The first news request returned HTTP 400 / `malformed_parameters`, identifying
`published_before` and `published_after` as incorrectly formatted. The adapter
had sent ISO timestamps with fractional seconds and an explicit timezone offset.
The [official API documentation](https://www.marketaux.com/documentation) specifies
UTC dates and accepts whole-second date strings without those suffixes.

Minimal runtime fix: convert the already-aware timestamp to UTC and format
`YYYY-MM-DDTHH:MM:SS` only at the request boundary. Internal publication/retrieval
timestamps stay timezone-aware. While checking this request contract, the
undocumented `sort=published_desc` parameter was removed; the documented default
publication-time ordering applies because the request contains no text search.
The corrected request returned 200. No other live schema incompatibility occurred;
nullable exchanges and an absent Japanese sentiment score were handled normally.

Code files changed during this task:

- `magi/research/news/providers/marketaux.py` — request timestamp formatting and removal of undocumented sort value.
- `tests/test_marketaux.py` — correct request-format expectations plus a regression for offsets and fractional seconds.

Documentation changed during this task:

- `docs/marketaux-news.md` — corrected request contract and link to these observations.
- `docs/marketaux-live-verification.md` — this report.

The working tree already contained the uncommitted Phase 7D.1 implementation
before this task. Its pre-flight state was recorded. No unrelated runtime module
or production dependency was changed by the live verification.

### 24–27. Security, storage and offline regression

Security checks passed for captured stdout, stderr, library logs, normalized
URLs, in-memory serialized objects, visible errors and Git diff. Both serialized
packs round-tripped without a token or credential-bearing request URL.
ResearchSource URLs remained original article URLs. The real `.env` stayed
ignored and untracked and matched its pre-flight SHA-256 baseline.

A counting transport enforced GET/HTTPS/api.marketaux.com and the exact two-path
allowlist. It made no destination article-page request. Full article bodies,
raw provider payloads and NewsEvidencePacks were not written to disk or production
storage. Only a redacted verification summary was retained; article text and
original URL lists are excluded. Bounded provider descriptions/snippets were
used only in memory for normalization and pack validation.

Production-data inventory matched the baseline. There was no production DB in
this checkout before verification, and none was created, copied or modified.
No production analysis memory or portfolio data was created or changed.

Verification for both packs included linkage, deterministic deduplication,
freshness, relevance, provider-sentiment provenance, versioned serialization
round trip, and Korean/English untrusted-data rendering. No events or catalysts
were inferred from sentiment. Existing source-diversity and authority policies
were retained.

Offline verification after the fix:

- Focused Marketaux unittest suite: **92 passed**.
- Full unrestricted pytest discovery: **704 passed**.
- Full unrestricted unittest discovery: **704 passed**.
- Both full runs blocked socket connections/DNS and the real HTTPX transport
  before discovery; all automated tests used zero live requests.
- All original 612 regression tests remain included and passed.
- Syntax parsing/compilation: PASS.
- Dependency check: PASS, no broken requirements.
- `git diff --check`: PASS.

Pytest emitted the existing Google GenAI/Pydantic deprecation warning; no new
runtime failure occurred. No commit or push was made.

### 28–30. Readiness and Phase 7E considerations

**Ready for milestone commit review** for this bounded read-only adapter,
including the verified date-format fix. This statement does not perform or
authorize a commit; the working tree remains uncommitted.

**Suitable as an initial supplementary live news provider.** US and Korean-company
resolution and complete news-to-evidence paths work. The sample does not justify
treating Marketaux as the sole source of investment-quality or Korean-language
coverage: NVDA had one publisher, Samsung had only English/Japanese mentions,
all relevance was MENTION_ONLY, all authority was TERTIARY, and exchanges were null.

Before Phase 7E, review source diversity, Korean-language/primary-source coverage,
mention-versus-subject relevance, missing metadata, quota budgets and provider/
publisher redistribution licensing. Retain explicit untrusted-content boundaries
and citation checks. Provider sentiment is not verified truth or an investment
signal; agent grounding should not silently elevate these observations into
recommendations, forecasts, catalysts or authoritative facts.
