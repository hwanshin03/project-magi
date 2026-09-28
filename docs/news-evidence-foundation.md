# Phase 7D: offline news evidence foundation

This module has no live provider, HTTP client, CLI integration, database connection,
agent connection or persistence path. Its inputs are normalized objects supplied by
an explicitly injected provider or an offline caller. No dependency was added.
Existing official adapters, financial snapshots, agents and trading decisions are
unchanged. Never put raw provider JSON directly into downstream MAGI logic.

## Public entry points

- `news.base.NewsProvider.fetch(NewsQuery)` defines the future adapter contract.
- `news.service.build_news_pack` constructs a complete immutable news graph.
- `news.deduplication.deduplicate` returns duplicate groups without losing revisions.
- `news.selection.select_news` returns a bounded view and omitted article IDs.
- `news.presentation.render_news` renders Korean (default) or English.
- `render_news_context` keeps trusted instructions separate from untrusted JSON.
- Existing research `dumps` / `loads` support all news models, with version 1.
- `NewsEvidencePack.as_evidence_pack()` exposes shared source/evidence objects and
  explicit conflict relations for future merging. It generates no ResearchClaims.

## Schemas

All records are frozen dataclasses. IDs hash normalized content deterministically;
forged IDs, invalid enums, nonfinite confidence, naive timestamps and unsupported
versions fail closed. Tuple ordering, metadata and references are canonicalized.
Article IDs identify immutable retrieved versions, so retrieval changes may change
the version ID; provider/external ID and canonical URL identify duplicate families.

`NewsArticle`: article_id, provider, publisher, title, optional summary, URL,
published_at (optional), retrieved_at, language, tickers, company_names, authors,
source_type, authority, external_id, metadata, classification, schema_version.
Tickers are uppercase/trimmed without losing Korean leading zeros. Company names
are informational; no fuzzy name-to-ticker inference is performed.

`NewsClassification`: explicit event type, verification status, content kind,
claimant, event namespace/key/time, assertion key/value, catalyst type/direction,
time horizon, optional Decimal confidence, sentiment, classification source,
optional market_moving flag and ticker relevance map. Confidence describes the
annotation, not truth probability or investment weight. It may remain unknown.

`ResearchEvent`: event_id, subject, ticker, event_type, headline, description,
occurred_at, published_at, source_ids, evidence_ids, status, metadata, schema_version.
Event time may describe a scheduled future event; it is not substituted for the
publication timestamp when computing article age.

`ResearchCatalyst`: catalyst_id, ticker, catalyst_type, direction, time_horizon,
description, evidence_ids, source_ids, confidence, status, created_at,
classification_source, schema_version. Negative direction is a risk view; positive,
mixed, neutral and unknown directions remain distinct. These are never trades.

`EventCluster`: cluster_id, ticker, event_type, event_time, source_ids, evidence_ids,
primary_source_ids, secondary_source_ids, event_ids, status, schema_version.

`NewsEvidencePack`: pack_id, ticker, subject, created_at, articles, sources,
evidence_items, events, event_clusters, catalysts, selection_omissions,
derived warnings/coverage, schema_version. Construction and deserialization check
the entire graph against normalized articles: cited text, statuses, source links,
clusters and catalysts cannot be silently replaced or omitted. Source/evidence
provenance uses the existing Phase 7A types.

## Authority, attribution and classifications

Future adapter policy must explicitly assign authority: official company releases,
regulatory or exchange announcements PRIMARY; professional reporting SECONDARY;
general web sources SECONDARY or TERTIARY after provider review; MAGI memory
INTERNAL_HISTORY. Publisher names alone never confer authority. Authority is not a
truth score. CONFIRMED_OFFICIAL requires PRIMARY authority plus explicit annotation.

Factual extraction uses the supplied summary, or headline when absent. Content kind
is FACT, REPORTED_CLAIM (default), OPINION or ANALYST_VIEW. Every evidence statement
keeps its kind, claimant when supplied, publisher attribution and original wording,
including uncertainty. No keyword extraction invents factual claims. Sentiment is
separate event metadata with classification provenance; it never becomes evidence
of financial performance. Catalysts and market-moving flags require explicit
classification provenance. No LLM, keyword sentiment, price prediction or
BUY/SELL/HOLD recommendation is generated.

## Duplicate and event policy

Duplicate families use exact content ID, provider-scoped external ID, conservatively
canonicalized URL, or explicit syndication_origin + syndication_id. URL normalization
lowercases hosts, removes default ports/fragments and known tracking parameters,
and sorts query pairs. It preserves path semantics and non-tracking query values.
It makes no request, follows no redirects and does not merge HTTP with HTTPS.

Duplicate groups retain all distinct versions/sources and expose one deterministic
latest-retrieved representative. Exact identical repeats are removed. A correction
is retained, not overwritten. Syndicated copies are grouped but never counted as
independent corroboration. Independent professional reporting and company releases
remain separate sources even when discussing the same event. The raw pack retains
all distinct versions; bounds apply to selected views, not destructive raw cleanup.

Clustering requires the same ticker, event type, explicit shared event namespace/key
and UTC event date (when known). Unkeyed events remain separate. Dates or similar titles
alone never establish identity. Different or missing event dates can split a real
event conservatively; future providers must document their stable IDs/time precision.
No semantic embeddings or fuzzy clustering exist.

Within a cluster, contradictory values for the same explicit assertion key produce
DISPUTED and CONFLICTING_NEWS, retaining both sources and evidence. Free text is not
semantically compared. Otherwise explicit DISPUTED, RUMOR or UNCONFIRMED status is
retained conservatively before official/corroborated status. Multiple articles or
publishers do not automatically create CORROBORATED status: that annotation must
come from a reviewed provider/editor policy with provenance. The individual
articles/events/catalysts retain their own reporting status alongside cluster status.

## Relevance, age, bounds and future views

Relevance is PRIMARY_SUBJECT, DIRECTLY_RELATED, INDUSTRY_RELATED, MACRO_RELATED or
MENTION_ONLY. Only explicit normalized ticker metadata elevates relevance. An
unclassified mention defaults to MENTION_ONLY; absent tickers are excluded.

Caller-supplied aware `as_of` makes freshness reproducible. Published age <=1 hour
is BREAKING; <=24 hours RECENT; <=7 days CURRENT; <=30 days AGING; otherwise STALE.
Missing dates are UNKNOWN, never replaced with retrieval dates. A future timestamp
cannot be classified as old news. `age` can also inspect past event times; scheduled
future events must be handled separately. No investment decay weight is computed.

Selection defaults: 20 articles, 40 evidence items, 20 catalysts, 20 clusters,
10 articles per publisher. Limits are independently configurable. Duplicate groups
and clusters form atomic bundles so bounds cannot keep only one side of a conflict.
A bundle that cannot fit is omitted explicitly, with article IDs and
BOUNDED_SELECTION; even an empty view is valid. The original pack is unchanged.
Selection favors publisher diversity and event coverage, then requested event/risk
preferences, explicit relevance, authority, recency and stable IDs. These are
context-selection priorities, not investment weights.

Future named policies for Melchior favor earnings/guidance/company fundamentals;
Balthasar favors products/industry/macro; Casper favors legal/regulatory and
negative, mixed or uncertain reporting. They are pure views, not agent connections.
Future Daily Picks can query catalyst direction, event type/market_moving,
classification provenance, freshness, publisher diversity and ticker relevance.
Future forecasts can join timestamped events with OHLCV externally; there is no
forecast, price target, daily-pick calculation or trading behavior here.

Quality warnings: NO_RECENT_NEWS, NO_PRIMARY_SOURCE, CONFLICTING_NEWS,
UNCONFIRMED_EVENT, STALE_NEWS, LOW_SOURCE_DIVERSITY, MISSING_PUBLICATION_DATE,
BOUNDED_SELECTION. Publisher count is descriptive, not independence certification.

## Trust, copyright, serialization and storage

Source text is UNTRUSTED RESEARCH DATA, including official releases and malicious
instruction-like strings. Context rendering places source data in JSON in the user
content field; trusted system instructions receive only the existing fixed research
policy. This isolation does not promise future LLM immunity to prompt injection.
No agents consume these contexts in Phase 7D.

Headlines are limited to 1,000 characters and optional summaries to 4,000. These are
technical ceilings, not copyright permission or an entitlement to retain content.
There is no article-body field. Metadata is for structured identifiers/annotations,
not full text, raw payloads or HTML. Future adapter review must establish licensing,
summary/excerpt rights, attribution and retention limits; URL/metadata-only records
are supported. Nothing is persisted in this phase.

Serialization preserves enums, Decimal, aware timestamps, frozen metadata, citations,
schema version and deterministic ordering. Existing sensitive-text/URL checks apply.
Korean/English presentation labels do not translate enums or rewrite source meaning.
Rendering fails explicitly when over its character limit; it never clips citations.
Select a smaller view instead.

Before a live provider: review licensing, ticker/market disambiguation, stable event
and syndication identifiers, correction semantics, publication/event timezone and
precision, metadata trust, rate limits/retries, credential redaction and a bounded
read-only live verification plan. No external calls are made by this foundation.
