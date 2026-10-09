# Phase 7F.3 — Temporal eligibility for live and historical analysis

Status: **offline implementation; no new live verification**.

## Cause and scope

The second live run captured `as_of` before collection. The SEC projection rejected
an observation if **any** of catalog `created_at`, evidence `retrieved_at`, or source
`retrieved_at` was later than that cutoff. All 2,688 normalized observations therefore
became `AFTER_AS_OF` before publication was considered. Assessment independently
vetoed late-retrieved roots; selection independently vetoed later extraction. Fixing
only SEC projection would leave the downstream problem in place.

The correction is shared by SEC projection, root assessment and derived-record
selection. It changes no CLI cutoff behavior, provider, normalization format,
financial calculation, grouping, relevance rule, attention rule, budget, diversity
constraint, agent order, vote, portfolio, broker, or database.

## Explicit temporal roles

| Field | Role |
| --- | --- |
| Source publication / filing acceptance timestamp | Public-availability boundary for that normalized source version |
| Filing / receipt calendar date | Calendar availability only; never invented intraday precision |
| Effective / occurrence date | Describes the subject/event, not when the announcement became available |
| Reporting period | Financial-period semantics; not a publication proxy; SEC projection still rejects future period ends |
| `retrieved_at` | Actual collection/possession time; retained for provenance, freshness, audit and reproducibility |
| Catalog / deterministic snapshot `created_at` | Normalization/calculation time, not the source's publication |
| Authored claim/catalyst `created_at` | An assertion's own creation boundary, retained conservatively |
| Evidence `as_of` | Existing explicit evidence boundary, still enforced |
| Request `as_of` | Immutable analytical knowledge boundary supplied by the caller or captured once by the CLI |

SEC sources already carry exact acceptance times where available, otherwise filed
dates labelled `date; midnight UTC convention`. OpenDART carries receipt dates labelled
`date; midnight KST convention`. Those placeholder midnights are read as calendar
dates for eligibility; they are not rewritten. News/official-company articles have
optional aware publication timestamps. Regulatory items retain real `date` values;
their generic source/evidence representations recover dates from explicit metadata.
No effective date, period end, title, or unstated filing metadata is used to invent
publication time.

## Deterministic policy: public-availability-v1

1. An explicit aware publication instant at or before `as_of` is eligible, regardless
   of later retrieval. A later publication remains ineligible.
2. Explicit calendar publication dates use the existing request-calendar convention:
   earlier day eligible, same day unresolved, later day ineligible. No timezone is
   guessed for a date. Exact instants retain their timezone-aware ordering.
3. Without publication information, retrieval at or before `as_of` establishes
   possession of that version; later retrieval cannot establish earlier knowledge.
   Assessment reports `RETRIEVAL_TIME_FALLBACK`. This is not an inferred publication.
4. Retrieval never overrides an explicit future or ambiguous date-only publication.
   Missing publication cannot satisfy a `published_since` publication horizon.
5. Derived normalized evidence must have an eligible source closure. Later extraction
   alone does not veto it; its explicit publication/as-of/authoring boundaries still
   apply. Deterministic Phase 7C calculations may be performed later from eligible
   cited inputs; their formulas and comparison requirements are unchanged.
6. Claim creation and undated relationship assertions bounded by their container's
   creation remain strict. A later-authored contrary claim is not backdated merely
   because its cited documents were published earlier.

Temporal observations still report later retrieval as `AFTER`. Their
`knowledge_boundary` flag identifies a possible boundary role, not an unconditional
veto: the eligibility policy determines precedence. Original timestamps, source
objects, locators and complete catalogs remain available in serialized provenance.

Examples:

- Published 2025-05-01, retrieved 2026-10-09, cutoff 2025-06-01: eligible.
- Published 2025-07-01 with that same retrieval/cutoff: excluded.
- Publication date 2025-06-01, cutoff 10:00 on that date: unresolved, excluded.
- Filing published T0−30 days, collected T0+5 seconds: eligible at cutoff T0.
- No publication, first retrieved T0+5 seconds: not eligible at cutoff T0.

## SEC projection and historical safety

The annual/quarterly interval and prior-comparable-period policy is unchanged.
Every observation is still audited, and every source and observation remains in the
full catalog. Publication eligibility precedes period selection. Required comparisons
never admit future filings, revisions, or amendments. Known past revisions retain
separate accessions and provenance. Instant-only balance-sheet fallback uses the
same source-availability rule.

The synthetic regression uses 2,700 historical observations, annual and quarterly
reports, amendments, multiple accessions, and collection at T0+5 seconds. The former
rule excluded all 2,700. The corrected annual projection contains **64 observations**
for current/prior periods (2025/2024), preserving the unchanged financial policy.
For the smaller 180-observation fixture, a 2025-06-01 historical cutoff admits
2024/2023 periods; a 2023-01-01 cutoff produces an empty projection. Future revisions
and future-public required comparable periods remain excluded explicitly.

This is normalized-publication eligibility, not proof of an archived web version.
A provider's wrongly dated revision cannot be detected merely from its claimed
publication time. Callers must supply version-correct normalized provenance; no
archival retrieval, timestamp repair, fuzzy inference or additional provider is added.

## Identity and serialization

AssessmentPolicy and SECProjectionPolicy include the identity-bound
`temporal_version=public-availability-v1`. The existing assessment rule version and
SEC period-policy version stay unchanged. Source IDs, reference/fingerprint
algorithms, grouping rules and grouping IDs for identical inputs are unchanged.
Changed retrieval provenance still changes content fingerprints, as before.
Corrected assessments/selections/projections receive their content-derived identities.

The existing generic serializer carries the new policy field and reconstructs all
invariants. Old serialized policy-bearing artifacts without the temporal version
are rejected by strict field validation, rather than silently reinterpreted under
new rules. Rebuild them from retained normalized inputs. Raw source/catalog formats
remain unchanged. Unknown temporal versions and forged derived artifacts are rejected.

## Verification and remaining live work

Offline tests cover historical/current cutoffs, exact/date-only publication,
retrieval fallback, SEC/DART midnight conventions, FSC effective dates, company/news
normalization, required-period future leakage, revisions, Phase 7C equivalence,
selection closure, authored assertions, permutations, explicit-clock independence,
tampering and a complete synthetic orchestration/serialization round trip.

No provider or agent calls are part of this phase. A separately authorized controlled
live run should verify nonempty SEC projection and selected views while retaining
future/date-only exclusions and the original cutoff. Report actual counts and
provenance; do not advance the cutoff to force acceptance.

Casper's prior HTTP-200 / `invalid_response` outcome is a separate unresolved
structured-output compatibility issue. Casper, its model, token limit, parser,
retry behavior and all other agent/provider behavior are unchanged here.

## Simplification and final offline validation

After the first full green pytest run, the dedicated simplification pass removed
repeated publication-date parsing and duplicate availability comparisons. One shared
precision/comparison implementation now serves projection, assessment and temporal
diagnostics; derived-record checks share one closure-dependent helper. No new
provider-specific branches, global cache, clock access or timestamp rewriting were added.

Final regression after simplification: **1,224 pytest tests and 756 subtests**,
**1,224 unittest tests**, and **1,224 unrestricted `*.py` discovery tests** passed.
Each suite recorded **zero real network calls and zero unexpected attempts**.
Syntax validation covered **144 Python files**; imports covered **109 MAGI modules**.
Dependency pins, pip check, tracked/untracked whitespace and secret checks passed.
The existing Google GenAI/Pydantic deprecation warning remains unrelated.

Four existing already-eligible artifact fixtures were also compared directly with
baseline `3981d71`: grouping, assessments, selected core/views/omissions, usage,
resolved fingerprints and temporal observations were identical. Only overall
policy-bearing artifact identities changed. The artifact golden hashes now bind
the explicit temporal version; the underlying analytical rules are unchanged.
