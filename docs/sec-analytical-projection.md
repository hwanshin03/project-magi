# Phase 7F.2: SEC analytical projection and pipeline performance

## Scope and financial requirements

The full normalized SEC company-facts catalog is historical financial observations,
not a list of filings. It remains intact in `SECAnalyticalProjection.catalog` on
`AnalysisResult.sec_projections`. Only `analytical_pack` enters Phase 7E and the
optional Phase 7C snapshot builder. No data is fetched or persisted by projection.

Phase 7C uses a current reporting interval and a comparable interval one year
prior for income, cash flow and per-share metrics. Year-over-year growth uses
income and per-share metrics; profitability uses current income and revenue.
Balance-sheet metrics use current-period instants, not a historical series.
`_sec_period` and `comparable` remain the authoritative interval rules: annual
330–380 days, quarter 70–105 days; comparable starts/ends 350–380 days apart and
interval lengths within ten days. DART behavior is unchanged.

## Versioned projection policy

`sec-comparable-periods-v1` accepts explicit `as_of`, scope, annual/quarter report,
optional period-end calendar year, supported Phase 7C concepts, publication horizon
and currency. Ordinary analysis defaults to the existing SEC concept set and the
latest eligible period plus its comparable prior-year interval. All intervals
sharing the latest end are preserved; ambiguity is not silently resolved. Current
balance-sheet instants are retained. Instant-only catalogs use the existing
snapshot fallback based on explicit official report dates.

An explicit publication horizon additionally retains matching report intervals
whose filings fall within that horizon, with their comparable intervals. It is a
publication horizon, not an invented observation-age cutoff. An explicitly broad
horizon or many revisions can produce a larger projection: there is no hidden
record cap. The `year` option selects period-end calendar year, not the SEC `fy`
label (which can describe the filing reporting older comparative observations).
No first-N, last-N, provider-order or random selection is used.

Every observation/revision/accession for a selected interval remains available,
including differing values and amendment metadata. No revision or supersession
relationship is inferred. Annual and quarter intervals remain separate. The
optional currency filter applies before interval choice. Catalogs with analytical
claims/relations are outside this financial-catalog projection contract; existing
caller-supplied research graphs continue through their existing path.

## Time boundary and audit

Phase 7F.3 uses `public-availability-v1` temporal eligibility alongside the unchanged
`sec-comparable-periods-v1` period policy. Explicit publication establishes source
availability; later retrieval and catalog normalization do not veto it. Without
publication, retrieval can establish possession by the cutoff, but never earlier.
A reporting period and any explicit evidence `as_of` must still respect the cutoff.
Date-only publication on the cutoff day is excluded as unresolved, rather than
interpreted as midnight knowledge. All original timestamps remain intact.
See [temporal eligibility](temporal-eligibility.md) for fallback and precision rules.

`audit` contains exactly one `(evidence_id, reason)` row for every original
observation. Reasons distinguish required-period selection, history outside the
required periods, report mismatch, concept/currency scope, future knowledge and
unavailable publication/period information. The original catalog closes every
reference, including excluded observations and unreferenced sources. The audit
is a projection decision, not a claim that collection was incomplete.

Catalog content fingerprint, policy (including version, scope and cutoff), selected
observation/source IDs and content fingerprints, and the complete audit determine
`projection_id`. This identity is distinct from both the source catalog identity
and the projected pack identity. AnalysisResult binds projection IDs into its
artifact identity and supports explicit lossless export of the catalogs/audits.
Existing result exports without projections retain their previous identities and
remain readable. Nothing is automatically exported to disk.

## Performance and validation boundaries

`storage.sensitive_operation` holds one immutable tuple of known-secret values
per operation. Nested checks reuse it; completion or exceptions reset the context.
The next operation reads the environment and local secret file again. Pattern
checks, research credential-assignment checks and credential-bearing URL checks
remain active on every string. No global secret-value cache, disk cache or logging
of secrets is introduced. A change to credentials during an operation is observed
at the next operation boundary, consistent with the explicit snapshot contract.

Research validation uses operation-local receipts. A receipt applies only to the
exact object that passed reconstruction, and every encoded field (including
identities/derived fields) must still equal its validation snapshot. New objects,
changed content and new operations reconstruct normally. There is no public
trusted flag. Decoding records a receipt only after constructors and all derived
field checks succeed. Receipts never enter serialization or cross operation
boundaries.

InputSnapshot builds immutable qualified-reference pairs. EvidenceUniverse owns
a read-only object/reference index shared by assessment and selection; grouping
uses the same pairs with its container context. Resolution fingerprints the
requested object to detect changed content, without fingerprinting the full pack
for every source lookup. Unknown references, collisions, version coexistence and
lineage validation keep their previous rules. Runtime indexes are not dataclass
fields, so they do not alter serialization or fingerprint composition.

No scoring, grouping, relevance, attention, diversity, budget, view ordering,
agent, voting, portfolio, broker or history rules changed. No concurrency was
added. Existing arbitrary research graphs retain their original path; normalized
SEC financial catalogs use the explicit projection boundary before snapshot
collection and Phase 7E construction.

## Verification and live limitation

Synthetic tests cover catalog preservation, complete omission auditing, report
separation, comparable history, revision preservation, permutation invariance,
no look-ahead, explicit horizons/concepts/year, identity binding and round-trip
analysis export. Golden digests captured from the unchanged baseline cover full
serialized graphs, fingerprints, resolved objects and temporal decisions, including
mixed sources and changed versions of the same source. Security tests cover
nested credentials, standard secret patterns, local-file refresh, fail-closed
reads, fresh operations and mutation detection.

Phase 7F.3 corrects the former retrieval-time veto observed in the second live run.
A cutoff captured before collection can now admit an explicitly earlier publication;
it is never advanced after collection. Future publication and ambiguous same-day
publication remain excluded. This correction is offline verified only; a separately
authorized live verification must inspect counts, exclusion reasons and provenance.


## Offline benchmark results

Baseline: `62f71f978e508c07e5012a74d3994077e6ac41ba`. Synthetic fixture:
`tests/test_sec_projection.py::catalog`, 60 observations/year spanning annual,
quarterly and amended annual reports. All accepted observations were available
by the explicit cutoff. This is not a future-only exclusion benchmark.

Each cell is **before → after**, wall seconds. Before used the full catalog in
Phase 7E; after uses the declared projection. Each stage ran sequentially in a
single process. The unchanged baseline used the same real local secret-file path
without copying or printing it. A 60-second per-stage deadline stopped slow
baseline stages; NR means subsequent stages were not reached. No projection
existed in the baseline. These are measurements, not estimated timeout values.

| Catalog observations | Projected | Catalog construction | Projection | Universe | Grouping | Assessment | Selection |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 10 | 0.068 → 0.002 | — → 0.006 | 0.490 → 0.022 | 1.115 → 0.029 | 2.334 → 0.041 | 4.730 → 0.054 |
| 100 | 52 | 0.597 → 0.010 | — → 0.044 | 4.502 → 0.100 | 10.115 → 0.135 | 22.399 → 0.182 | 43.092 → 0.223 |
| 500 | 64 | 2.909 → 0.049 | — → 0.197 | 22.160 → 0.128 | 51.341 → 0.169 | ≥60 → 0.224 | NR → 0.319 |
| 1000 | 64 | 5.785 → 0.095 | — → 0.386 | 43.555 → 0.126 | ≥60 → 0.170 | NR → 0.227 | NR → 0.274 |
| 2700 | 64 | 15.993 → 0.258 | — → 1.052 | ≥60 → 0.129 | NR → 0.175 | NR → 0.230 | NR → 0.277 |

The 2,700-observation catalog retains 64 analytical observations from 4 filing sources; 2636 observations have explicit omission reasons. The current and prior annual reporting intervals retain all selected concept versions and amendments, with current-period balance-sheet instants.

A separately timed operation from bounded pack through universe, grouping, assessment and selection took **0.738 seconds**. Full orchestration from a normalized full catalog through projection, financial snapshot and fake agents/result validation took **5.790 seconds**. Catalog collection and input-object construction are outside those latter two timers. No live transport or real agent invocation was measured.

Dedicated simplification removed duplicate assessment/selection object indexes and reused snapshot reference pairs. It retained canonical reconstruction on first validation, fresh operation boundaries and explicit runtime-index integrity checks. No scoring or selection algorithm was rewritten.

Final offline regression: 1,198 pytest tests plus 737 subtests; 1,198 unittest tests; 1,198 tests under unrestricted `*.py` discovery. Syntax: 142 Python files. Imports: 108 MAGI modules. Dependency pins, pip check, whitespace and secret/artifact scans passed. All three guarded suites reported zero real network calls and zero unexpected network attempts. The only reported warning was the existing Google GenAI/Pydantic deprecation warning.
