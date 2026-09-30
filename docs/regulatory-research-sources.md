# Phase 7D.3 — government and regulatory ingestion

## Verification status

**LIVE VERIFIED:** BIS 20/20, SEC Regulatory 20/20, and Korean FSC 10/10 accepted through the full RegulatoryBundle pipeline. BIS `C1-2026-16628` was observed and accepted without inferred relationships. FSC Dublin Core publication dates populated 10/10 records; its type/status remained PUBLIC_NOTICE/UNKNOWN and no events were generated. Earlier BIS rejection and FSC endpoint/date incompatibilities were resolved by 7D.3a/7D.3b. All repository fixtures remain synthetic; no raw live responses are retained. Final offline regression passed 859 tests in pytest, unittest and unrestricted discovery, with zero real network calls or unexpected attempts.

Initial contracts cover BIS and SEC regulatory publications through agency-filtered Federal Register JSON, and Korean FSC RSS. SEC EDGAR remains a separate company-disclosure adapter. Fed, FTC, DOJ, FSS, KFTC and MOTIE are extension candidates only.

## Architecture and use

`RegulatoryProvider` → immutable `RegulatoryItem` → attributed `ResearchSource` / `EvidenceItem` → explicit `RegulatoryEvent` → `RegulatoryBundle`. `GovernmentProvider` implements the three catalog contracts. `RegulatoryService` collects one explicit agency query; the bundle can expose an existing `EvidencePack`. No agents, voting, portfolio, Daily Picks, forecasting, trading or persistence are connected.

A provider has no default transport. Offline callers may call `parse(source_key, synthetic_bytes, retrieved_at)` or inject a fixture transport. A separately authorized live caller would have to explicitly supply `RegulatoryTransport`. There is no pagination, document fetch, browser or crawler. Serialization registers the new models additively.

## Models and meaning

`RegulatoryItem` is frozen and contains `item_id`, `source_key`, `agency`, `jurisdiction`, `title`, optional `summary`, `url`, `published_at`, `effective_at`, `retrieved_at`, `language`, `regulatory_type`, `status`, optional `legal_reference`, optional `docket_or_reference`, optional `affected_industries` / `affected_entities`, and bounded immutable metadata. Absent affected labels are empty, never inferred from sector or company names in prose.

Types: RULE_PROPOSAL, FINAL_RULE, INTERIM_FINAL_RULE, GUIDANCE, ENFORCEMENT, ANTITRUST, EXPORT_CONTROL, MONETARY_POLICY, SANCTIONS, INDUSTRIAL_POLICY, PUBLIC_NOTICE, OTHER.

Statuses: PROPOSED, FINAL, INTERIM, EFFECTIVE, SCHEDULED, WITHDRAWN, SUPERSEDED, GUIDANCE, ENFORCEMENT_ACTION, UNKNOWN. Types and statuses are independent. Their presence in the enum does not mean each agency supplies them.

`RegulatoryEvent` preserves agency, jurisdiction, type, status, publication/effective dates, legal/reference fields, source IDs, evidence IDs, explicit affected labels, provenance metadata and deterministic `event_id`. It exists only when a recognized structured classification field supports it. No LLM, headline inference, directional catalyst, recommendation, risk/relevance/materiality score, portfolio impact or analytical weighting is generated.

Publication and effective dates are independent. Missing dates stay `None`; calendar dates remain dates, never invented midnight timestamps. Timestamps must be timezone aware. A future effective date does not change FINAL to SCHEDULED; a past date does not change it to EFFECTIVE. Generic `ResearchSource.published_at` and evidence `as_of` require timestamps, so date-only values are preserved in metadata with DATE precision; generic missing-publication warnings can therefore still appear.

## Source-specific contracts

BIS and SEC rows must contain the matching agency slug. The parser accepts explicit Federal Register `type`, narrowly recognized `action`, `document_number`, `publication_date`, `effective_on`, `html_url`, `abstract`, `cfr_references` and `regulation_id_numbers`. Proposed Rule and Rule classifications remain distinct; recognized action fields refine interim, withdrawn, superseded, guidance and enforcement states. BIS export-control subject text stays attributed text; it does not assign harm to NVIDIA or any other company. Export-control category is available in the neutral model, but is not guessed from a headline.

Optional `correction_of`, `amends`, and `supersedes` fields represent **unverified candidate structured relationships**, not confirmed Federal Register response fields. Missing relationships remain absent; no prose extraction substitutes for them.

FSC preserves original Korean title/description and `ko-KR`, using RSS title/link/description and publication dates from pubDate or the exact Dublin Core namespace; category/guid remain optional. Exact recognized category labels map to conservative classifications. Optional `regulatoryType`, `regulatoryStatus`, `effectiveDate`, `legalReference`, and `reference` are **synthetic candidate extension fields, not confirmed live feed fields**. They must be checked during live compatibility review; missing fields stay unknown. No automatic translation or English FSC feed is implemented.

## Provenance, evidence, families and versions

Normalized sources use `WEB_SOURCE`, `PRIMARY`, `regulatory:<source_key>`, and `REGULATORY_PUBLICATION`, without a company ticker or market. PRIMARY means first-party provenance only: not applicability, economic materiality, positivity, negativity, unbiased interpretation or investment value. It is based on the accepted official-source contract, not a guarantee of legal applicability or completeness.

Evidence uses category REGULATORY and the wording “agency published the following source statement,” followed by the bounded official summary or title. Source and evidence IDs are retained by events. Bundle validation reconstructs the entire graph and rejects inconsistent provenance or relationships.

All records carry `source_family=GOVERNMENT_REGULATORY` and an agency family such as `GOVERNMENT_REGULATORY:US:BIS`. Twenty publications do not imply twenty times the importance. There is no corroboration score or weight. Generic evidence-pack record counts remain inventory counts, not independent-source counts; Phase 7E must apply family/relationship-aware selection before analytical integration.

Content IDs exclude retrieval time: repeated retrievals deduplicate, newest retrieval wins. Changed text/status/date produces a distinct version, even at the same URL/reference. Shared explicit rule identifiers create RULE_FAMILY links without collapsing proposals and final rules or asserting supersession. Explicit references alone create AMENDS, CORRECTS and SUPERSEDES links. Korean/English records need reciprocal official translation URLs plus matching agency/type/status to link as TRANSLATION_OF. Similar titles are insufficient; translations remain in the same agency/source family and never generate independent corroboration.

## Exact network and canonical URL policy

Only these fixed GET requests are configured:

- `https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau&per_page=20&order=newest`
- `https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=securities-and-exchange-commission&per_page=20&order=newest`
- `https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111`

Canonical citation URLs (never fetched) allow only HTTPS with exact hosts, no userinfo, port, query or fragment:

- `www.federalregister.gov`: `/documents/YYYY/MM/DD/(C1-)?YYYY-NNNN[NN]/<single-slug>/`, trailing slash optional; document suffix is 4–6 digits.
- `www.fsc.go.kr`: `/no010101/<digits>` or `/po040301/<digits>`, trailing slash optional.

Strict UTF-8 and Unicode-normalization checks reject encoded ASCII, traversal, delimiters, controls and ambiguous escaping. Other source keys/hosts/paths are rejected. All DNS answers must be public/global; the connection is pinned to a validated numeric address. HTTPcore retains the original hostname for SNI and hostname verification; trusted certificate validation is required. Environment proxy configuration is disabled. HTTPX 0.28.1's internal backend seam is isolated in transport and must be retested before dependency upgrades.

All redirects are rejected. There are at most two attempts with one-second backoff for selected transient statuses/network errors. Connect/read/write/pool timeouts are ten seconds each (not a global wall-clock deadline). Responses are capped at 1,000,000 bytes; JSON accepts application/json and RSS accepts rss+xml, atom+xml, application/xml or text/xml, but the parser only implements RSS. Non-identity content encoding is rejected. XML DTD/entities are rejected. Invalid individual records produce static skip codes; malformed containers fail closed. At most 200 input records are accepted per parse.

## Trust, retention and cache

Source text is untrusted data, including government interpretation and prompt-like language. Context rendering separates system policy from serialized attributed data; no context is wired to an agent. HTML removal handles summary text only, not webpages. Titles are at most 500 characters, summaries 600, legal references 500, and record references 200. No full document, webpage, PDF, raw payload, article body or database persistence exists. Bounded public excerpts are not blanket permission to redistribute content; terms/copyright suitability remains part of controlled source review.

The in-memory cache stores normalized parse results and static diagnostics only: 1–3 entries, default three; TTL 0–3600 seconds, default 300. Clearing it removes diagnostics too. No disk cache. Date filters and item limits are operational bounds, not relevance ranking.

## Controlled verification policy and remaining limits

For future separately authorized re-verification of an exact catalog endpoint, check availability, no-redirect behavior, TLS/SNI/hostname validation, DNS/public-IP behavior, MIME/encoding/response-size compatibility and actual response schema. Confirm Federal Register agency slugs/filtering, document URL patterns, date precision, legal/RIN/action fields and whether relationship metadata exists. Confirm FSC endpoint/query, RSS layout, Korean encoding, language, canonical URL paths, category semantics and which optional date/reference fields actually exist. Do not infer missing fields or weaken URL restrictions to improve acceptance.

With separately approved bounded requests, compare accepted/skipped counts and static reasons; run accepted items through source/evidence/event/serialization stages; verify dates/status/provenance and zero inferred company impact; inspect terms and excerpt limits. Verify cache behavior without extra calls. Do not retain raw responses or make follow-up document requests. Current live verification passed all three endpoints. Optional effective-date, action, CFR/RIN and relationship fields were absent in the retrieved Federal Register results. FSC supplied title/link/description/DC date, not the synthetic classification extensions. Those optional contracts remain live-unobserved; licensing/redistribution review remains required.

## Phase 7D.3a compatibility boundaries

FSC requests are selected by the closed `fsc_releases` source key, not caller-provided URLs or query dictionaries. The only configured path/query is `/about/fsc_bbs_rss/?fid=0111`; other fid values, extra parameters, the obsolete endpoint and arbitrary URLs cannot be requested. No FSC content-path expansion was needed: existing `/no010101/<digits>` and `/po040301/<digits>` remain the only canonical shapes. The new standards-based Korean fixture is synthetic, not proof of the live response schema. Missing structured classification leaves PUBLIC_NOTICE/UNKNOWN and creates no event; headline words do not imply final status or effectiveness.

The shared Federal Register identifier grammar is `(?:C1-)?[0-9]{4}-[0-9]{4,6}`. Only the observed C1 prefix is recognized; C2, C01, lowercase and arbitrary prefixes remain rejected. The identical grammar governs the canonical URL document segment. C1 conveys no correction/amendment/supersession link. Optional dates, actions, CFR/RIN and relationship metadata remain absent when not supplied.

Completed controlled checks confirmed the replacement FSC endpoint, TLS/SNI, canonical item URLs and full bundle pipeline, plus BIS C1 acceptance without inferred relationships. Phase 7D.3b completed the FSC date compatibility check described below. Future checks must not follow redirects, fetch item bodies or expand allowlists automatically.

## Phase 7D.3b FSC Dublin Core dates

The diagnostic observed `{http://purl.org/dc/elements/1.1/}date` with `2026-09-30 00:00:00`: explicit calendar fields but no timezone. The parser preserves this exact midnight-label shape as a calendar `date`, not an invented timestamp or assumed Korean timezone. Existing safely supported shared date formats remain available; other timezone-free times and malformed dates fail conservatively. A present `pubDate` takes precedence, including empty (unknown) or malformed (item skipped) values; DC is used only when pubDate is absent. No prose/URL dates or effective dates are inferred. Date-only publication metadata remains visible in the normalized source/evidence metadata with DATE precision. Type/status, transport and allowlists are unchanged.
