# Phase 7E.4 — diversity, evidence budgets and agent views

`EvidenceSelection(assessment, policy=EvidenceSelectionPolicy())` is an offline,
immutable selection over the original `AssessmentSet`. It preserves its universe,
groups, assessments, lineage and original records. `render_view(selection, agent)`
returns bounded, explicitly untrusted JSON for inspection. Nothing invokes an agent,
changes a prompt or connects to `history.agent_request` or `ResearchContext`.

## Repository boundaries

The older research selector filters categories and returns ID-sorted EvidencePacks.
The older news selector ranks authority, supports a risk preference and treats groups
atomically. Neither algorithm is reused. Existing agent requests use historical text,
not the research context boundary. No reliable cross-provider tokenizer abstraction
exists. These interfaces and all Phase 7E.1–7E.3 public semantics remain unchanged.

## Policy and artifacts

- `EvidenceSelectionPolicy`: version `7E.4-v1`, profile `structural-v1`, explicit
  immutable limits and exact required analytical group IDs. Unknown versions,
  profiles, IDs and invalid bounds are rejected. Request prose is never interpreted.
- `SelectedGroup`: existing group ID, qualified unit/citation/conflict references,
  controlled disagreement notice, unresolved review flag and audit codes.
- `OmittedGroup`: existing group ID and controlled omission reason.
- `BudgetUsage`: groups, excerpt units, citation entries and rendered UTF-8 bytes.
- `AgentEvidenceView`: agent name, ordered existing group IDs, measured budget usage.
- `EvidenceSelection`: original assessment, policy, ordered shared core, all group
  omissions, three views, usage and deterministic content ID.

These are references and projections, not a duplicate evidence store. The ephemeral
index joins existing graph references and is not serialized. No source truth or
independence score is produced.

## Selection rounds and priorities

Only roots marked AS_OF_COMPATIBLE by Phase 7E.3 can supply content. Derived evidence
must also have eligible provenance and no future knowledge timestamps. Mixed groups
may retain eligible roots; excluded versions cannot supply topics or excerpts.
Unrelated groups are omitted. Unresolved groups use at most the explicit review-slot
limit and stay labeled as unresolved; relevance/attention are never promoted.

Each round orders candidates lexicographically by:

1. Explicit required group, or an existing REQUESTED_COMPARABLE_METRIC basis.
2. Existing relevance category.
3. Existing attention category.
4. Existing known disagreement, among otherwise comparable candidates.
5. Soft family concentration and uncovered structured topics.
6. Stable group ID.

No score, source count, publisher count, authority, legal FINAL status, direction,
sentiment, headline keyword or `prefer_risks` participates. A requirement cannot
bypass temporal, provenance, review-lane or hard budget checks. Unsatisfied required
groups remain in the omission audit; the policy records which groups were required.
When required metric evidence cannot be represented safely, it is omitted rather
than replaced with a claim that the comparison has been shown.

## Diversity and lineage

Source family is the original root's SourceType, or REGULATORY for a regulatory
item. It is an anti-flooding axis, never an importance weight. No allocation quotas
exist. Among equally ranked candidates, a family below the soft ceiling precedes an
already concentrated family. If no comparable under-ceiling candidate can be used,
concentration is allowed and `SOFT_FAMILY_CEILING_RELAXED` is recorded. Stronger
relevance/attention is never sacrificed just to add weak evidence of another family.

Topics use existing EvidenceItem categories, attributed news event types and
explicit dated exposure relationships already accepted by the assessment. Unknown
stays unknown; text is not classified. Uncovered topics break comparable ties before
IDs. Adding more identical reports never multiplies a group's coverage contribution.

Exact normalized roots and explicit translation/representation/derivation/version
links between eligible roots identify shared lineage. A wholly represented lineage
can be omitted as REDUNDANT_COVERAGE unless explicitly required or contested.
Explicit syndication origin/story identifiers also identify shared lineage.
Publisher differences alone never establish independent corroboration. Common-group
membership alone never establishes identical lineage. Filing snapshots retain their
existing group and do not gain an extra slot. No inference from similar titles is
used. Future roots never bridge lineage components used by selection.

## Compact representation and conflict closure

A group normally contributes one eligible evidence unit and its transitive generated
source provenance, with a preference for evidence supporting its existing assessment.
If it has no evidence unit, an eligible original document/report is used. Required
comparable financial metrics retain all cited evidence inputs. If the citation bound cannot fit that evidence/source chain, a non-contested
original-root summary may substitute with ROOT_SUMMARY_ONLY recorded. Requested
comparisons never degrade to this summary. Original groups are never mutated. Full records, URLs and source locators can be resolved
through the qualified references in the audit universe.

Existing ResearchClaim contrary/unresolved references and non-corroborating
EvidenceRelation records trigger transitive closure. Claims must be created by as_of;
undated relations require their container to be available by as_of. Attributed news
assertions with the same event/key and different explicit values also disclose
disagreement. Similar headlines, direction or sentiment do not establish conflict.
An explicitly DISPUTED report without a complete counter-position uses a notice.
No new truth/claim system and no contradiction resolution is introduced.

A selected contested group includes all connected evidence positions and source
citations when they fit. Closure may cite evidence from other analytical groups
without independently selecting or ranking those groups. Conflicts are never used
to infer which side is true. If closure is temporally unsafe or too large, a
`DISAGREEMENT_DETAILS_OMITTED` marker replaces **all** excerpts and citations for that
subject. The group ID links back to its full graph in the retained universe. This
marker discloses a gap, not the content of a future counter-position. If even the
marker cannot fit, the group is omitted with BUDGET_LIMIT. No one-sided excerpt is
silently retained. Optional detail expansion is deferred.

## Hard budgets and rendering

Defaults: 8 groups, 16 excerpt units, 32 provenance/claim citation entries, 16,000
UTF-8 bytes, 320 UTF-8 bytes per excerpt, soft family ceiling 2, unresolved review
slots 1. All numeric limits enter selection identity. There are no hidden tokenizer
budgets. Empty JSON envelope overhead counts; a byte bound smaller than it is invalid.
Zero group/unit/citation limits are permitted. A disagreement marker may use zero
units/citations while still consuming a group slot and bytes.

The actual deterministic JSON projection is measured, including field names,
identifiers, notices, audit codes, uncertainties, punctuation and escaping. Excerpts
are byte-bounded on valid UTF-8 boundaries and explicitly marked truncated. Excerpts
are untrusted source text, not full evidence statements or standalone conclusions.
Citation entries are counted per selected group (shared citations may be charged
again conservatively). Full conflict references count as citation entries. All
three views contain identical rows, so reordering preserves exact byte usage.

**Only `render_view` is bounded.** Lossless serialization of EvidenceSelection retains
the full input universe and omission audit and is deliberately not a bounded prompt.
It must not be passed to agents as if it were one. Exact tokenizer/model accounting,
instruction/output reserves and actual prompt integration remain deferred. UTF-8
bytes do not claim exact token fit.

## Shared core and agent views

All selected groups are common to all three views. This first policy uses ordering
preferences only; separate optional agent slots are deferred. Requirement, relevance,
attention and known disagreement precedence remain the same for every agent.
Specialty is a comparable-evidence tie preference, never an inclusion filter:

- Melchior: financial/valuation/fundamental/guidance and structured business topics.
- Balthasar: market/macro/industry/catalyst and event-development topics.
- Casper: risk, balance sheet, regulatory/legal, competition and supply-chain topics.

Casper has no negative-only preference. Positive resilience, mitigation and limitations
remain available and receive the same category preference. Major earnings and target
regulatory developments can appear in every view. No agent code is imported.

## Audit, identity and serialization

Every original analytical group is either selected or has one controlled omission:
BUDGET_LIMIT, REDUNDANT_COVERAGE, TEMPORAL_EXCLUSION, UNRELATED, REVIEW_LIMIT or
UNSUPPORTED_PRESENTATION. Group order and omission order are explicit and separate
from EvidencePack ordering. Unassigned aggregates remain in GroupedEvidence.unassigned;
upstream availability/omissions remain in the universe manifest.

Selection identity covers the request/universe/grouping/assessment (including its
policy), selection version/profile/limits, references, notices, omissions, usage and
all agent orderings. There is no random value or clock. Exact input duplication and
permutation are invariant. Adding new report content or changing authority metadata
can change content-derived IDs and therefore final equal-priority ID tie ordering;
it never changes the substantive ranking categories or adds source-count weight.

Serialization is additive. EvidenceSelection reconstructs all derived results and
rejects forged IDs, order, references and usage. Standalone output models validate
shape; the enclosing selection validates graph membership and policy application.
No DB, cache, live request, new dependency, trading action or provider change exists.
