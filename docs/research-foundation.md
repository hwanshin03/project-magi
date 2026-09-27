# Phase 7A: research evidence and citation foundation

This package is offline architecture only. It creates no provider clients, performs
no HTTP requests, reads/writes no database, and changes no agent, debate, vote, or
explanation behavior. Production portfolio schema remains v3. Research JSON schema
version 1 is separate from the database schema. A CLI and AgentResult migration are
deferred; Python APIs provide construction, validation, selection, and rendering.

```text
Future SEC / OpenDART / IR / macro / news / market adapters
                     |
        ResearchSource + SourceLocator
                     |
                EvidenceItem
                     |
        ResearchClaim + EvidenceRelation
                     |
          immutable validated EvidencePack
                     |
      deterministic bounded agent-specific view
                     |
        separate trusted and untrusted context
                     |
  future agents -> debate -> deterministic vote -> report
                     -> presentation -> historical track record
```

## Domain schemas

All models are frozen dataclasses. Collections are copied into tuples; nested
metadata is copied recursively into immutable FrozenMetadata mappings. Numeric
financial evidence accepts integer or finite Decimal, not float. Categorical text
and prose-only evidence are supported. Enums are language-neutral identifiers.

- **ResearchSource**: source_id, source_type, authority, provider, title, publisher,
  optional url, published_at, retrieved_at, document_type, ticker, company_name,
  market, language, external_id, structured metadata.
- **EvidenceItem**: evidence_id, source_id, subject, ticker, category, statement,
  optional value/unit, period_start/end, as_of, source_locator,
  extraction_confidence (optional Decimal from 0 to 1), retrieved_at.
- **SourceLocator**: optional page, section, filing_item, table, paragraph,
  xbrl_concept, timestamp, article_section. Page is a positive integer; other
  locations are literal source-provided text. Locator timestamp denotes a media
  position, not publication time. Missing locators remain missing.
- **ResearchClaim**: claim_id, subject, ticker, claim_text, category,
  supporting_evidence_ids, contrary_evidence_ids, unresolved_evidence_ids,
  created_by, created_at, computed status.
- **EvidenceRelation**: kind (CORROBORATING, CONFLICTING, UNRESOLVED), evidence_ids,
  optional explanatory note. Corroboration/conflict requires at least two IDs;
  unresolved relations require at least one. Relation references are validated.
- **EvidencePack**: pack_id, subject, ticker, market, created_at, sources,
  evidence_items, claims, relations, computed warnings, computed coverage,
  stale_after_days, schema_version.

Source types: SEC_FILING, DART_FILING, COMPANY_IR, EARNINGS_RELEASE, EARNINGS_CALL,
MARKET_DATA, MACRO_DATA, NEWS, ANALYST_RESEARCH, WEB_SOURCE, MAGI_MEMORY. Extend the
controlled enum intentionally when adding an adapter; unknown values are rejected.

Categories: FINANCIAL, VALUATION, GROWTH, PROFITABILITY, BALANCE_SHEET, CASH_FLOW,
GUIDANCE, CATALYST, RISK, MACRO, INDUSTRY, COMPETITION, MANAGEMENT, MARKET_PRICE,
MARKET_VOLUME, SENTIMENT, REGULATORY, OTHER.

## Source hierarchy is provenance, not a truth score

- PRIMARY: filings, official company disclosures and earnings releases.
- AUTHORITATIVE_DATA: government datasets or authoritative market datasets.
- SECONDARY: reporting and analysis based on other sources.
- TERTIARY: general commentary.
- INTERNAL_HISTORY: prior MAGI reasoning/history.

Authority is assigned explicitly by a future trusted adapter, not inferred from an
arbitrary URL or an LLM confidence score. No numeric authority weighting exists.
MAGI_MEMORY must use INTERNAL_HISTORY, preventing internal generated text from
being promoted to primary evidence. Neither source presence nor a citation proves
that a factual statement is true or that a source actually supports it.

## Evidence versus interpretation

Evidence records extracted or source-derived observations. Claims record
interpretations. Applications must build a validated pack before using either:
every evidence source and every claim/relation evidence reference must resolve.
Unsupported generated prose belongs in an UNSUPPORTED claim, not in an invented
source. Future adapters must substantiate extraction; this foundation validates
structure and references, not document authenticity or entailment.

Claim status is computed, not accepted from a model:

| Evidence links | Status |
|---|---|
| No support (including contrary-only) | UNSUPPORTED |
| Support, no contrary or unresolved references | SUPPORTED |
| Support and unresolved references, no contrary | PARTIALLY_SUPPORTED |
| Support and contrary references | CONFLICTED |

These names describe citation structure, not verified truth or completeness of
semantic support. Standalone claims are unbound until EvidencePack validates IDs.
One evidence ID cannot be both support and contrary for the same claim.
There is no LLM contradiction detection. Explicit relations preserve opposing,
corroborating, and unresolved observations without overwriting them.

Citation consumers can use `pack.evidence(id)`, then `pack.source(source_id)` and
the locator. Future agent output can cite `[E...]` IDs or caller-supplied readable
IDs such as E12. No changes to AgentResult are needed now; later integration should
validate output citation IDs against the exact selected pack, not the full corpus.

## Identity and deduplication

SHA-256 IDs are deterministic when omitted. Source identity uses source type,
provider, and external ID when present, otherwise the exact URL, otherwise document
metadata. Retrieval time is excluded. URLs are not aggressively canonicalized:
future adapters must consistently provide canonical URLs/external IDs. Independent
providers retain independent identities even when reporting the same fact.

Evidence identity includes the source ID, locator, subject, category, statement,
value/unit and temporal fields, excluding retrieval time and extraction confidence.
Identical observations collapse; different facts on the same page are preserved.
`deduplicate` is explicit and deterministic: repeated identical records differing
only in retrieval time retain the earliest retrieval. Conflicting reuse of an ID
raises an error instead of overwriting text or financial values. Extraction
confidence disagreement also requires an explicit resolution.

Packs themselves reject duplicate IDs. A pack's generated ID hashes its complete
snapshot content and policy, not merely its member IDs. IDs may also be supplied by
the caller; they are opaque identifiers, not authenticity signatures. Construct a
new pack without a supplied ID for changed content. Do not use dataclass `replace`
while retaining an old ID to claim content identity. Old packs remain immutable.

## Temporal semantics and historical snapshots

Publication, retrieval, evidence as-of time, and financial reporting periods are
separate fields. Datetimes must be timezone-aware; period dates are actual dates.
Publication cannot follow retrieval; source/evidence retrieval and claim creation
cannot follow the pack creation cutoff. Guidance can refer to future periods, so
period_end/as_of are not assumed to be retrieval dates. Naive datetimes and reversed
periods are rejected. No timestamp is silently replaced with the current clock.

Warnings are computed at pack creation time, so deserialization years later does
not change a historical snapshot. `stale_after_days` defaults to 365 calendar days
and is explicitly configurable: this generic warning policy is not a promise of
market-price freshness or a filing-specific regulatory freshness rule. A future
adapter/analysis should select a suitable policy for its sources.

Coverage areas financial, valuation, market, macro, news, risk, regulatory are
computed from actual evidence categories (news additionally uses source type).
A source or an unsupported claim alone cannot establish coverage. Coverage means
presence, not completeness, timeliness, authority, or factual correctness.

Warnings: NO_PRIMARY_SOURCE, STALE_SOURCE, CONFLICTING_EVIDENCE,
MISSING_FINANCIALS, MISSING_MARKET_DATA, MISSING_PUBLICATION_DATE. These flags never
block analysis. Missing publication time is not silently treated as fresh. Primary
coverage requires at least one evidence item referencing a primary source.

## Selection and context boundaries

SelectionPolicy supports authority, category, source type, ticker, publication
cutoff, maximum source count and maximum evidence count. Results are ordered by
publication recency with deterministic ID tie breaks. Authority is a filter, not
a numeric truth ranking. Only sources used by selected evidence are retained.
Claims and relations survive only if **all** references survive: filtering cannot
remove contrary evidence and upgrade a conflicted claim. The original pack is
unchanged, while the selected pack receives a new content ID and recomputed warnings.

AGENT_POLICIES provides reusable category views for Melchior (financial/valuation),
Balthasar (macro/market/catalysts), and Casper (risk/balance sheet/regulation).
Callers can refine these policies with source types or ticker filters. They are
not connected to live agents and are not investment-decision policy changes.

`render_context` returns separate `system_instructions` and `user_content` fields.
Only caller-owned behavior instructions and a fixed trust policy enter the system
field. The user field contains a JSON object with separately named sections:

- CURRENT USER QUESTION
- RESEARCH EVIDENCE — UNTRUSTED SOURCE CONTENT
- HISTORICAL MAGI MEMORY — UNTRUSTED HISTORICAL CONTENT

Imperatives inside evidence remain JSON string values, never message roles or
system instructions. Source titles, metadata, claims, and locators are equally
untrusted. Internal-history sources in a pack retain their MAGI_MEMORY /
INTERNAL_HISTORY labels; additional historical memory is placed in its own section.
The renderer enforces an explicit combined character bound (default 60,000) and
rejects oversized context rather than cutting citations or JSON in half. This is
not a token estimator and cannot guarantee prompt-injection immunity; future
integrations need output citation validation as well. No embeddings/vector search.

## Serialization, validation, and security

`dumps/loads` and `to_dict/from_dict` support all four root models. The envelope is
`{"schema_version":1,"data":...}`. Tagged values represent dataclasses, enums,
Decimals (exact decimal strings), dates, timezone-aware datetimes, tuples and
metadata maps. Keys and pack members are deterministically ordered; Decimal scale
and timestamp offsets survive the JSON round trip. Computed claim status, warnings
and coverage are serialized and checked against recomputation when loading. A
forged supported status or invented coverage is rejected. Duplicate JSON keys,
unknown tags/fields/schema versions, bad references and invalid enums are rejected.

Financial values reject floats, booleans and nonfinite values. Unit validation is
syntactic (e.g. USD, shares, percent, USD/share); semantic unit meaning belongs to
future adapters. Tickers must already be uppercase and normalized (including
numeric Korean tickers); invalid input is rejected, not silently repaired.

Secret validation reuses MAGI's known-environment/local-secret detector without
printing secrets or loading provider clients. Additional checks reject credential
assignments, Authorization headers, private keys, sensitive metadata keys, URL
userinfo and token/key-bearing query parameters, including URLs embedded in prose
or metadata. Secrets are rejected rather than silently sanitized. This cannot
identify every unknown opaque credential; future adapters must never ingest raw
credential-bearing requests. Serialization revalidates strings. No research data
is persisted in Phase 7A.

English/Korean presentation labels are kept in a separate module. Domain enums
remain English regardless of presentation language.

## Before Phase 7B adapters

Define SEC and OpenDART external identifiers, version/amendment handling, official
publisher checks, ticker/issuer mappings, filing publication timestamps, exact XBRL
units/periods and locators, and a source-specific freshness policy. Define bounded
request behavior, offline fixtures, licensing/quotation limits and raw-content
handling separately. Company IR, FRED/BLS, news and reference-market providers can
then use the same models without changing portfolio/accounting source semantics.

Future Research → Report → Presentation should retain the exact pack ID and
snapshot used by an analysis, citations, opposing evidence and unknowns. Snapshot
persistence and direct agent grounding require a later explicit implementation;
no production migration is implied by this phase.
