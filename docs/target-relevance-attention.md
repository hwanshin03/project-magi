# Phase 7E.3 — target relevance and analytical attention

The offline API is `AssessmentSet(grouped, policy=AssessmentPolicy(), exposures=())`.
It retains the original `GroupedEvidence` and derives one immutable `GroupAssessment`
per group. It performs no selection, ranking, rendering, provider calls or writes.
Phase 7E.1/7E.2 public models and grouping semantics are unchanged.

## Independent categorical results

- Relevance: DIRECT, LINKED, CONTEXTUAL, UNRESOLVED, UNRELATED.
- Attention: ELEVATED, STANDARD, BACKGROUND, UNASSESSED.
- Temporal fitness: AS_OF_COMPATIBLE, FUTURE_RELATIVE_TO_AS_OF, OUTSIDE_HORIZON,
  UNKNOWN; MIXED is a group containing different root fitness states.

These are neither direction nor recommendations. Source family, authority, FINAL
status, sentiment, catalyst direction, specialty, prefer_risks, publisher count and
group size are not assessment weights. No numeric score or majority vote exists.

## Identity and explicit exposures

DIRECT requires an exact structured issuer identity, an explicitly qualified affected
entity, or matching market/symbol in an issuer-bearing filing/company record.
Issuer metadata uses the existing provider namespace, for example `SEC:0001045810`.
`EntityIdentity('SEC','0001045810')` matches that token; its display name does not.
An affected-entity value must likewise contain the exact qualified token; company
names in that field are not resolved. Different identity namespaces do not establish
an issuer mismatch. Contradictory known instrument/entity identities are unresolved.

A ticker mention, title, company name, country, or textual request scope cannot by
itself establish DIRECT. Unknown identity/exposure remains UNRESOLVED. Explicit
instrument or comparable-namespace issuer mismatch can establish UNRELATED.

`TargetExposure` is a caller assertion tied to the exact request TargetIdentity and
one original qualified document/report reference. It requires LINKED or CONTEXTUAL,
a controlled relationship (supplier, customer, product, segment, industry,
jurisdiction, event), asserting party, basis reference and aware `known_at`.
Its deterministic mapping ID preserves attribution. It is not verified business
knowledge. No business relationship is hard-coded or inferred from prose.
A mismatched target, dangling reference, or derived evidence endpoint is rejected.
Future-dated mappings are retained but cannot improve the assessment. Explicit
exposure may connect an otherwise different issuer (for example a supplier).

## Attention rules

Version `7E.3-v1` uses these narrow rules:

- Supported DIRECT/LINKED connection: STANDARD.
- Explicit CONTEXTUAL connection: BACKGROUND.
- Insufficient or unrelated connection: UNASSESSED.
- Explicitly requested existing comparable financial metric: ELEVATED only when
  Phase 7C marks that DerivedMetric AVAILABLE, its cited sources/evidence are known
  by as_of, explicit evidence boundaries are satisfied, and relevance is DIRECT.
  Later deterministic calculation/extraction is not new public information.

`AssessmentPolicy.requested_metrics` names existing derived metrics such as
`revenue_yoy`. This declares a structured research question; prose in request.scope
is never parsed into instructions. Elevation means an available comparison answers
that question, not that its magnitude is economically material or directional.
No accounting calculation or percentage threshold is introduced.
Missing/incomparable requested metrics give UNASSESSED and explicit uncertainty.
If some requested comparisons are available, ELEVATED can coexist with an incomplete
coverage warning. Unsupported metric names produce unavailable coverage, not guesses.

Every assessment records structured `AssessmentBasis` rule IDs, qualified references,
and mapping IDs where applicable. Missing information is represented by stable
uncertainty codes. Existing claims and disagreements stay in the original universe;
the assessment never resolves them or calls them consensus.

## Temporal boundary

`SelectionRequest.as_of` is authoritative. The original document/report root's
explicit publication time controls availability, even when retrieved later. Missing
publication falls back to retrieval with `RETRIEVAL_TIME_FALLBACK`: possession must
already have occurred by as_of. The fallback cannot establish a publication horizon.
Same-day calendar-only publication stays UNKNOWN; retrieval never overrides it.
SEC/DART `date; midnight UTC/KST convention` metadata is honored as date precision.
Stored timestamps stay unchanged; temporal observations now consistently expose
those declared calendar dates, just as the assessor already did.
See [Phase 7F.3 temporal eligibility](temporal-eligibility.md).

A prior calendar date remains a date and can be as-of compatible under the existing
request-calendar comparison convention; no source timezone is invented. Optional
`published_since` is an explicit publication horizon, not a recency score. A date-only
publication on the horizon boundary is UNKNOWN. Without a horizon, historical
publication can remain suitable. Fiscal period end and effective date are not
publication/availability timestamps. A known publication announcing a future
condition is not made future knowledge merely by its effective date.

Each root's `ReferenceFitness` remains visible in `temporal_records`; a MIXED group
can use eligible roots while excluding future/unknown/outside-horizon versions.
No original record or group is deleted. Future/unknown roots cannot improve the
relationship or supply a requested financial comparison. Original temporal values
remain available through the retained universe and its temporal observations.

## Determinism and validation

AssessmentPolicy accepts `7E.3-v1` with `temporal_version=public-availability-v1`; unknown implementations are
rejected rather than silently executing old rules under a new label. Policy inputs,
request, original grouping, exposure assertions and derived assessments enter the
content-derived IDs. No clocks, random IDs, language models or API access are needed.
Input permutation and exact input replication are invariant. Additional copies can
change reference-bearing snapshot IDs while leaving categorical attention unchanged.

Serialization registrations are additive. AssessmentSet reconstructs results from
validated grouping and rejects forged derived assessments or IDs. Standalone output
models are immutable reference values; use the enclosing AssessmentSet to validate
rule application against its source universe. The earlier source/lineage and group
serialization contracts are unchanged.

Numerical materiality thresholds, semantic interpretation, automatic exposure
resolution, arbitrary horizon inference, contradiction resolution, evidence/token
budgets, diversity, specialty views, rendering and agent integration remain deferred.
