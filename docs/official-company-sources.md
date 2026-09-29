# Phase 7D.2: official company sources

This is a bounded first-party ingestion layer, not an investment decision feature. Controlled verification on 2026-09-29 established live status for **only three RSS sources**:

| Source | Live status | Observed accepted / total | Limitation |
|---|---|---:|---|
| NVIDIA News RSS | **LIVE VERIFIED** | 2 / 20 | 18 unsupported item hosts skipped; no blog fetches |
| Samsung Global Newsroom RSS | **LIVE PARTIAL** | 49 / 50 | One unsafe canonical URL skipped; security was not relaxed |
| Samsung Korea Newsroom RSS | **LIVE VERIFIED** | 20 / 20 | UTF-8 percent-encoded Korean canonical URLs accepted |

Each feed produced complete `NewsEvidencePack` objects for accepted items. The second verification made exactly three requests with no retries, redirects, article requests or PDF requests. All three returned HTTP 200 / `text/xml`; TLS certificate/hostname verification and original-host SNI succeeded. All 71 accepted items mapped to PRIMARY, with bounded excerpts, correct internal identities and no inferred events, directional catalysts or recommendations. No deterministic translation pair was observed. The earlier Samsung date error did not recur; no date format was guessed or added.

**NVIDIA IR pages, Samsung IR earnings pages and Samsung announcement pages are OFFLINE-IMPLEMENTED / LIVE-UNVERIFIED. They are not live-supported sources.** The first controlled run returned NVIDIA IR 403 responses, unsupported Samsung earnings HTML, and Samsung announcement 404/500 responses. They were not retried in the second verification. Synthetic HTML fixtures establish an offline parsing contract only. A page without supported metadata fails explicitly rather than appearing to be an empty success.

These counts describe the bounded verification snapshot, not guaranteed future feed contents or complete company coverage. No full live payload or article body is included in this repository.

## Architecture and use

`NvidiaProvider` and `SamsungProvider` implement the provider-neutral `OfficialCompanyProvider.fetch_items(NewsQuery)` contract. Both also implement `NewsProvider.fetch` for existing consumers:

```
registered company source → bounded parser → OfficialCompanyItem
 → NewsArticle → ResearchSource + EvidenceItem
 → ResearchEvent only for explicit metadata types → NewsEvidencePack
```

`CompanySourceService` and `NewsService` accept explicitly injected providers. Construction/imports never fetch data. A provider without an injected transport raises `TRANSPORT_NOT_CONFIGURED` when asked to fetch. Parsing supplied bytes requires no network transport. There is no CLI, scheduled ingestion, database writer, agent, voting, portfolio, Daily Picks, forecast or paper-trading integration.

```python
from datetime import datetime, timezone
from magi.research.company_sources.nvidia import NvidiaProvider
from magi.research.company_sources.service import build_company_pack

# Offline: bytes from an approved local fixture, not a new network request.
provider = NvidiaProvider()
items = provider.parse("nvidia_newsroom", fixture_bytes,
                       retrieved_at=datetime.now(timezone.utc))
pack = build_company_pack(items, ticker="NVDA", subject="NVIDIA",
                          created_at=max(item.retrieved_at for item in items))
```

The common parser handles RSS 2.0 and Atom with explicit categories, original-language metadata and canonical item links. Limited HTML handling reads only schema.org JSON-LD `NewsArticle`, `Article`, `Event`, and `PresentationDigitalDocument` metadata. It does not interpret arbitrary page text, render JavaScript, traverse links or download PDFs. JSON-LD support is a candidate compatibility contract; these live pages may instead require a reviewed, narrowly scoped listing parser after verification. Categories use exact matches, never title keywords or an LLM.

## Closed source catalog

Only the three RSS entries have the live statuses shown above. All IR/announcement entries remain offline-implemented, live-unverified candidates. Only catalog keys can be requested; no caller-supplied URL is accepted by the transport.

| Source key | Exact registered endpoint | Contract |
|---|---|---|
| `nvidia_newsroom` | `https://nvidianews.nvidia.com/releases.xml` | RSS/Atom; English press releases, explicit earnings/product categories |
| `nvidia_ir_releases` | `https://investor.nvidia.com/news-and-events/press-releases/default.aspx` | Structured HTML metadata; press/earnings |
| `nvidia_ir_events` | `https://investor.nvidia.com/events-and-presentations/default.aspx` | Structured HTML metadata; scheduled investor events/presentations |
| `nvidia_ir_results` | `https://investor.nvidia.com/financial-info/financial-reports-and-sec-filings/default.aspx` | Structured HTML metadata; presentation metadata only; SEC items excluded |
| `samsung_newsroom_en` | `https://news.samsung.com/global/feed` | RSS/Atom; English Electronics newsroom |
| `samsung_newsroom_ko` | `https://news.samsung.com/kr/feed` | RSS/Atom; Korean Electronics newsroom |
| `samsung_ir_earnings_en` | `https://www.samsung.com/global/ir/financial-information/earnings-release/` | Structured HTML metadata; earnings |
| `samsung_ir_earnings_ko` | `https://www.samsung.com/sec/ir/financial-information/earnings-release/` | Structured HTML metadata; earnings |
| `samsung_ir_notices_en` | `https://www.samsung.com/global/ir/stock-information/announcements/` | Structured HTML metadata; notices, explicit dividends/shareholder categories |
| `samsung_ir_notices_ko` | `https://www.samsung.com/sec/ir/stock-information/announcements/` | Structured HTML metadata; Korean notices |

Canonical **item links are retained but never followed**. NVIDIA item links are restricted to `nvidianews.nvidia.com/news/`, `/releases/`, and `investor.nvidia.com/news-and-events/press-releases/`, `/events-and-presentations/`. Samsung item links are restricted to `news.samsung.com/global/`, `/kr/`, and `www.samsung.com/global/ir/`, `/sec/ir/`. Index/feed endpoints cannot masquerade as article URLs. CDN-hosted documents, other domains, query-dependent URLs and alternate redirect targets are not implicitly allowed. This intentionally limits current coverage pending review.

NVIDIA identity is `NVDA / US`. Samsung identity is **string `005930 / KR`**, never `005930.KS`. Marketaux may independently use its verified external symbol mapping. The Samsung registry identifies Samsung Electronics only; Samsung SDI, Biologics, Electro-Mechanics and their domains are not registered. Explicit conflicting structured publisher/organizer names are rejected. An Electronics announcement mentioning another business does not make that business the issuer. SEC/DART categories are excluded; external filing URLs are not ingested by this layer.

## Immutable model and provenance

`OfficialCompanyItem` contains `item_id`, `ticker`, `market`, `company_name`, `provider`, `source_name` (registered source key), `item_type`, `title`, optional `summary`, canonical `url`, optional `published_at`, `retrieved_at`, original `language`, optional `external_id`, and frozen `metadata`.

Controlled types: `PRESS_RELEASE`, `EARNINGS_RELEASE`, `IR_PRESENTATION`, `IR_EVENT`, `COMPANY_NOTICE`, `PRODUCT_ANNOUNCEMENT`, `CORPORATE_NEWS`, `DIVIDEND_NOTICE`, `SHAREHOLDER_NOTICE`, `OTHER`.

IDs derive from company/provider, internal identity, canonical URL and language; refresh time does not change logical identity. Duplicate retrievals collapse to the latest normalized representation. Simultaneous conflicting content fails closed. This is not revision-history storage. Publication and retrieval times remain distinct. Missing dates stay missing. Date-only values are preserved in `publication_date` metadata without inventing a time zone. Naive or malformed timestamps and publication after retrieval reject the affected feed item with a bounded diagnostic; date formats and clock invariants remain unchanged. Region-qualified `en-US` / `ko-KR` are retained. The common versioned research serializer supports item and pack round trips.

Research sources retain original canonical links, dates, internal ticker/market, language, provider external IDs, stable official item IDs, source keys, source-family/event-family IDs, classification basis and bounded source metadata. Earnings map to `EARNINGS_RELEASE`; other official metadata maps to `COMPANY_IR`; both use `PRIMARY` authority. Evidence retains explicit attribution and citations. Titles or limited source excerpts support only what they actually say.

`PRIMARY` means first-party/original provenance. It **does not mean unbiased, objectively correct, financially material, positive or investment-worthy**. Company communications have first-party bias. `CONFIRMED_OFFICIAL` identifies a company-issued statement, not independent validation of every claim. Even prompt-like text remains untrusted source data, never system instructions. No source text changes application behavior.

## Events, translations and independent reporting

Exact source categories or a dedicated registered source type can map earnings → `EARNINGS`, product announcements → `PRODUCT`, dividends → `DIVIDEND`. Structured investor events map to existing `OTHER`, preserving `IR_EVENT` in item metadata and an explicit schedule time where available. Generic press releases, presentations, shareholder notices and other corporate notices create evidence without invented events. No directional catalyst, sentiment, confidence, financial-impact inference or BUY/SELL/HOLD recommendation is generated.

Korean and English source representations are preserved independently. Reciprocal official alternate-language links, the same company and the same item type are required to join their event families. Similar titles, nearby publication dates, or a shared generic identifier are insufficient. Missing/one-way language links do not establish equivalence. Translation families have one cluster when the event is deterministic, even when publication dates differ. There are two cited representations, not two independent corroborations. Explicit conflicting event times remain separate under the existing event clustering contract.

`build_company_pack(..., additional_news=...)` combines already-normalized external news. Marketaux ingestion is unchanged. An official source and independent coverage retain separate provider/source/evidence records. They can share an event cluster **only when external reporting already carries an explicitly sourced matching event identity**. There is no automatic semantic linker and no invented event extraction for Marketaux's unclassified live articles. The synthetic tests demonstrate an explicitly annotated external event, not new Marketaux behavior.

**SOURCE COUNT != IMPORTANCE.** All IR/newsroom/language representations for a company share `COMPANY_OFFICIAL:<market>:<ticker>`. First-party views expose `source_family_count` and `source_families` separately from raw `publisher_count`. Explicit syndicated origin groups republished articles into one family. These descriptive counts do not establish independence, corroboration or importance. Ten company announcements remain one first-party family, not ten votes. Events retain separate identities; quantity never creates catalysts or weights. Unknown syndication is not guessed.

SEC, DART, snapshots, Marketaux, market, macro and regulatory evidence remain separable. Phase 7E must address relevance, materiality, diversity, evidence budgets and agent-specific context selection. This phase adds no scoring or balancing policy and does not route official packs into existing selection/agent pipelines.

## Security, retention and cache

The transport uses HTTPS, GET, exact endpoints, no credentials, no environment proxies, no cookies and no redirects (including same-host redirects). Registered hosts resolving to any non-global address fail before transport. Arbitrary hosts, IP literals, local/private addresses, user-info, query/fragment URLs, encoded ASCII syntax, dot-segment paths and unsupported item paths are rejected. Approved item paths may contain strictly decoded UTF-8 Unicode segments; canonical output uses uppercase percent escapes. Encoded separators, nested encoding, invalid UTF-8, controls and Unicode compatibility forms of dangerous syntax remain rejected. Request endpoint allowlists are unchanged. The connection backend pins the checked numeric address while retaining the original hostname for TLS/SNI, preventing a second hostname lookup from bypassing the address check. No untrusted content can supply a network target. This backend uses a narrowly isolated internal pool hook in the project-pinned HTTPX 0.28.1; its compatibility must be retested on dependency upgrades. Only HTTPX MockTransport can replace the production transport for offline tests.

Per-operation timeout is 10 seconds; at most two attempts with one one-second backoff for transient transport/status failures. At most 1 MB uncompressed response bytes; only expected XML/RSS/Atom or HTML MIME types are accepted. Compressed responses are rejected. XML DTDs/entities are rejected before parsing, UTF-8 only. Maximum 200 entries per source. Errors expose static codes, never provider payloads or rejected URLs.

Retained titles are at most 500 characters; plain source excerpts at most 600. Only allowed bounded metadata fields survive. Full page/release bodies, `articleBody`, Atom content, raw payloads and PDF contents are not modeled, cached, logged or persisted. Raw response bytes exist transiently during parsing. No database writes exist. The bounded LRU/TTL cache holds normalized items and bounded skip diagnostics only (default eight sources, 300 seconds; configurable capacity 1–32, TTL 0–3600 seconds). Hits preserve original retrieval/publication timestamps; cache clearing is explicit. Cache bounds and query limits are operational limits, not materiality rankings.

First-party availability is not a redistribution license. Friend/club/commercial distribution requires a separate review of source terms, copyright, excerpt permissions and attribution requirements before release.

## Remaining controlled verification

The three-feed verification is complete. Further requests require separate authorization. The items below describe remaining coverage/compatibility work, not permission to repeat successful checks or bypass failed access restrictions.

1. Review each exact catalog endpoint, company ownership and applicable access/redistribution terms. Confirm path availability, content type/encoding, status, redirects and permitted rate. Do not auto-follow a redirect or widen the allowlist.
2. Fetch each approved source once through the bounded transport with an explicit request budget. Confirm RSS/Atom vs actual HTML structures. If IR pages lack supported JSON-LD or require JS/vendor APIs, stop that source and review a narrow alternative; do not crawl, render or download documents.
3. Check real item canonical links, category vocabulary, date/timezone formats, language tags, missing fields and bounded excerpts. Confirm NVIDIA SEC exclusion and Samsung Electronics issuer isolation. Review whether first-party CDN/PDF-link metadata requires a separately approved URL rule.
4. Verify one NVIDIA earnings/product/event/presentation sample and Samsung Korean/English earnings, notices, presentations, newsroom and dividend/shareholder samples as available. Absence of an item is not evidence of compatibility.
5. Verify actual reciprocal language-link representation before asserting any Samsung translation equivalence. If absent, leave records unlinked; never invent a pairing from titles/dates.
6. Review source-family metadata and optional explicit links to independently obtained Marketaux coverage. No new Marketaux request is needed or authorized by this phase.
7. Keep any live records/payloads out of Git and the production database. Record only sanitized aggregate results; compare protected-file hashes before/after. Endpoint/parser fixes, if needed, require offline regression checks before claiming live compatibility.


## Phase 7D.2a: narrow offline compatibility patch

No new live requests are part of this patch. The preceding controlled run exposed language casing, encoded Unicode paths and an unresolved Samsung date failure. NVIDIA blog links remain unsupported; no IR HTML, access-control, redirect or endpoint changes are included.

Language tags use standard casing within the existing supported language/region subset: `en-us` becomes `en-US`, `ko-kr` becomes `ko-KR`. Malformed tags, unsupported languages, scripts/extensions and private-use tags remain rejected. This is not a new language inference or translation feature.

`provider.parse(...)` now returns an immutable, sequence-compatible `ParseResult`: `items`, `skipped`, `skipped_count`, and `total_count`. Each frozen skipped-item diagnostic contains a zero-based entry `index`, static `code`, and optional sanitized `date_value`. Unsupported item hosts—including NVIDIA blog links—are skipped; no linked URL is requested. Invalid individual RSS/Atom records no longer discard valid neighbors. Intentional SEC/DART-category exclusions are also counted. An all-invalid feed has zero items and a nonzero skipped count, distinguishable from an empty feed. Malformed XML, DTD/entity use, unsupported feed structure, response/entry bounds and invalid retrieval clocks still fail the complete parse. HTML remains strict and unchanged in scope.

`fetch_items(...)` retains its tuple contract. Inspect `provider.last_diagnostics[source_id]` for skipped records from the most recent fetch, including cache hits. The bounded cache keeps normalized records and their small diagnostics, never raw responses. `clear_cache()` also clears those diagnostics. Consumers must inspect diagnostics before labeling a collection complete.

Date diagnostics preserve at most 80 ASCII characters consisting only of date-shaped syntax and recognized date/time words. The complete candidate is checked against known secrets before copying. Overlong values, prose, markup, URLs, controls, credential-like content and known secrets become `[REDACTED]`. Diagnostic values are separate untrusted data fields; exception messages remain static. No title, article URL, body or payload is included in a skipped-item diagnostic. Publication formats and the prohibition on future publication timestamps are unchanged; no Samsung-specific date parser was guessed.

Encoded item paths are validated against the unchanged official host and literal path prefix before strict per-segment UTF-8 decoding. Encoded ASCII—including slash, backslash, dots, NUL, query/fragment delimiters, colon, at-sign and percent—is rejected. Decoded segments and their Unicode compatibility forms cannot contain separators, traversal segments, controls or whitespace. Encoded non-ASCII characters are canonicalized without changing the represented text. This deliberately does not accept arbitrary encoded paths or new hosts.

The subsequent three-request verification confirmed that all three RSS sources reach the full normalization pipeline, with the exact live statuses and partial-coverage caveats recorded at the top of this guide. Samsung Global’s unsafe item remains skipped; 50/50 acceptance is not a goal that can override URL security. Further diagnosis or additional source verification requires separate authorization. IR HTML endpoints retain offline-only implementation status.
