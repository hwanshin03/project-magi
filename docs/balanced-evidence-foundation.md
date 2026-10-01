# Phase 7E.1 — reference universe and identity foundation

This is an offline, deterministic reference/index boundary over already normalized
research. SOURCE COUNT != IMPORTANCE. It does not rank, select, score, cluster events,
allocate context, invoke an LLM, render agent prompts, or make investment decisions.

## Inputs and identities

`InstrumentIdentity(market, symbol)` requires explicitly normalized identity fields.
`KR / 005930` and `OTHER / 005930` are different instruments. The caller must distinguish
market namespaces: this layer does not equate US with NASDAQ or infer any exchange.

`EntityIdentity(namespace, identifier, display_name=None)` identifies a research entity
by its namespace/identifier pair. A display name is only a label. `TargetIdentity` may
contain an instrument, an entity, or both. Supplying both requires `mapping_asserted_by`
and `mapping_reference`. This is an attributable caller assertion, not independent
verification of that mapping. No name, ticker, headline or content similarity resolves
an entity. Missing issuer identity remains `None`.

`SelectionRequest` contains the target, an explicit aware `as_of`, textual research
scope, a canonical set of declared languages, policy version, optional expected input
manifest ID, and a computed request ID. Languages are declarations, not exclusion
rules or translation requests. There is no budget profile or horizon policy in this
subphase. No wall-clock default exists.

## Qualified references and fingerprints

`InputSnapshot` retains the caller's original immutable container. It revalidates the
existing model graph without mutating it or flattening it into a generic EvidencePack.

A `QualifiedReference` contains:

- snapshot identity;
- exact existing object class name;
- the existing object ID, where that object has one;
- a content/version fingerprint;
- a computed qualified-reference ID.

Objects without existing IDs (for example financial metric values or relations) use
`None` for object ID and remain distinguishable by content fingerprint. Existing IDs
are never replaced. Same bare source IDs in different snapshots remain separate
references; changed source text is not overwritten. References contain no duplicate
headline, statement or body.

Fingerprints reuse the existing tagged `serialization.encode` and SHA-256 `identity`
utility. They cover normalized model content, including the caller-supplied retrieval
and observation timestamps. They contain no generated clock values, random UUIDs,
process addresses, runtime caches, paths or request headers. Local filesystem paths
and credential-like content are rejected by the fingerprint/security boundary. This
is not a general-purpose broker/raw-payload hasher; unsupported containers fail closed.

Identical supplied objects fingerprint identically. Exact duplicate input snapshots
are represented once. Input permutation and duplicate replication do not change the
universe or its reference set. A changed request/policy changes universe identity,
while references to unchanged snapshots stay unchanged. Date offsets and explicit
retrieval versions remain content; this layer does not silently equate them.

## Universe and input availability

`EvidenceUniverse` stores the request, original input snapshots, availability entries,
qualified references, lineage, and deterministic manifest/universe IDs. Index and
structured lineage are reconstructed and validated on creation/deserialization.
All original specialized containers remain accessible through `inputs`; `resolve`
returns the original referenced object.

`InputFamily` describes the container lane: RESEARCH, FINANCIAL_SNAPSHOT, NEWS,
REGULATORY or MARKET. These are not analytical source weights or independence claims.
Original provider, publisher, agency and source-family metadata stays in its container.

Each `InputAvailability` has an attributable caller-specified `input_key`, so callers
can separately describe SEC and DART, or BIS and FSC, within one lane. With no explicit
entries, the builder creates one aggregate `default` entry per lane. Callers should
supply separate input keys when they know per-source availability. Do not interpret
default aggregation as a claim that every possible provider was requested.

| State | Meaning |
|---|---|
| NOT_SUPPLIED | No input was supplied for that declared key; no conclusion about the world. |
| UNAVAILABLE | No usable snapshot; a static reason code is required. This can describe a collection or unsupported-bridge failure, distinguished by the code. |
| EMPTY | A successful supplied container has zero primary records. |
| AVAILABLE | A supplied container has primary records and no declared omissions/errors. |
| PARTIAL | A supplied container has explicit errors or omissions, even if it contains zero primary records. |

Primary records mean evidence items for EvidencePack, evidence references for a
financial snapshot, articles for news, and items for regulatory bundles. EMPTY never
means evidence of no risk. Even empty containers are retained. Each input snapshot
must belong to exactly one manifest entry; missing, duplicate or wrong-family
references are rejected. Known news selection omissions automatically make an input
PARTIAL and cannot be hidden by an override. Other collection diagnostics must be
explicitly supplied by the caller because normalized containers cannot reconstruct
unprovided provider failures. Error codes are static identifiers; no raw exception or
HTTP payload logging is introduced.

An optional request `input_manifest_id` must match the computed manifest. For a
workflow that binds it explicitly, build the input manifest/universe once without that
constraint, then supply its manifest ID in the final request; no fetching occurs.

## Lineage, not analytical grouping

`LineageReference` connects qualified child/parent references with a controlled kind,
asserting party and basis. The implementation uses only structured relationships:

- container membership: REPRESENTATION_OF;
- evidence to its cited source: DERIVED_FROM;
- normalized source to its explicitly identified article/regulatory item: DERIVED_FROM;
- events, catalysts and financial metric values to explicit evidence IDs: DERIVED_FROM;
- an existing news cluster to its existing events: REPRESENTATION_OF, without computing a new cluster;
- financial snapshot to a separately supplied underlying pack: DERIVED_FROM, only when pack ID and cited source/evidence versions match exactly;
- reciprocal official translation identifiers and explicit regulatory translation links: TRANSLATION_OF;
- exact normalized object aliases across supplied containers: REPRESENTATION_OF.

No URL/title similarity, C1 prefix, publisher count or semantic inference creates a
link. Financial snapshot references do not become additional independent evidence;
their citation/derivation links remain visible. If an underlying pack is not supplied,
its original ID remains in the snapshot and its cited evidence is still indexed; no
fictional container is created.

Callers may supply explicit attributable lineage, including VERSION_OF. This records
an assertion; it does not certify its semantics. Caller assertions cannot impersonate
the reserved `structured-input-v1` generator. Dangling endpoints and cyclic
DERIVED_FROM/VERSION_OF chains are rejected. Symmetric translation/representation
links are not interpreted as chronological parentage. Existing claim conflicts and
regulatory amendments/corrections remain in the original objects; they are not
reclassified as derivation. No independence score or corroboration count exists.

## Read-only adapters and deferred market support

`adapt` and `build_universe` accept only existing EvidencePack,
CompanyResearchSnapshot, NewsEvidencePack and RegulatoryBundle objects (or their
InputSnapshot wrappers). Official-company material already exposed in NewsEvidencePack
is supported without another duplicate adapter.

No provider, transport, MarketDataService, broker, agent or fetching service is called.
The adapters perform no environment setup or database/file writes. The existing
research security boundary still rejects known credential-like content.

Raw Quote/CandleSeries/FXRate objects are explicitly unsupported in 7E.1. Their existing
market model records are not validated research provenance graphs, and there is no
approved stable market-to-research reference bridge yet. No source URL, issuer mapping,
observation time or research citation is fabricated. A caller may declare MARKET as
UNAVAILABLE with `UNSUPPORTED_MARKET_REFERENCE_BRIDGE`, or NOT_SUPPLIED when none was
provided. Unsupported market data is never silently turned into an empty successful
input. A later narrow pure bridge can add support without changing fetching services.

## Temporal precision and look-ahead diagnostics

`temporal_observations(universe, reference)` reads explicit values without dropping or
ranking records. It preserves aware datetimes, calendar dates, effective dates,
reporting-period boundaries and unknown values. Known regulatory date-only metadata
is recovered for generic source/evidence references, so FSC publication dates remain
known even when generic timestamp fields are `None`.

Aware instants compare as AFTER or AT_OR_BEFORE the explicit request boundary. Date-only
values compare to the calendar in the caller's `as_of` as DATE_BEFORE, DATE_AFTER or
SAME_DATE_UNORDERED. These calendar comparisons do not assert intraday order, invent a
source timezone or imply midnight UTC. Later eligibility policy must handle that
uncertainty explicitly.

Publication, retrieval, creation and evidence-as-of observations are marked as possible
knowledge boundaries. Effective/occurrence dates and reporting periods are not proof
of when information became available. Future retrieval/publication remains visible;
no evidence is silently discarded. The helper is an observation report, not a final
look-ahead eligibility verdict.

## Serialization, validation and limits

New model/enum registrations are additive in the existing research serializer. Old
research objects and their serialized schemas remain unchanged. Original containers
are encoded once within the universe; references carry no copied source text.
Derived indexes, fingerprints and lineage are checked on deserialization, including
forged field rejection. Decimal/date/datetime and original specialized graphs survive
round trips. No persistence layer or DB migration is added.

All tests are synthetic/offline, with network and database guards. Invariant tests
cover input permutation, duplicate replication, cross-market identities, source-ID
version collisions, availability, derivation, temporal precision and serialization.

Explicitly outside this implementation: relevance, attention/materiality, analytical
event grouping, source weighting/diversity policies, evidence/token budgets, selection,
agent views, contradiction resolution, LLM assistance, agent prompt rendering,
recommendations, trading, provider changes and live verification.
