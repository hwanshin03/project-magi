# Phase 7E.2 — deterministic analytical grouping

SOURCE COUNT != IMPORTANCE. This pure layer constructs reference-based contexts
from an existing `EvidenceUniverse`. It performs no fetching, persistence, ranking,
consensus, source weighting, sentiment analysis, selection or agent integration.

## API and identity

`GroupedEvidence(universe)` retains the original universe and derives `groups`,
`unassigned`, and `grouping_id`. `AnalyticalGroup` has an optional `GroupAnchor`,
qualified `members`, and a deterministic `group_id`. No evidence text is copied.
A `GroupAnchor` contains `namespace`, `key`, `scope`, and a compatibility tuple.

A group's ID identifies its immutable membership snapshot. Adding a report can
change that ID. The anchor namespace/key is the external event/document identity
asserted by the source; it is not a universal event identity or verified truth.
Request identity scope and compatibility remain part of matching. All IDs reuse
the existing canonical encoder and fingerprint helpers, with no generated clocks.

## Implemented anchors

- Regulatory publication: existing `source_record_id`, scoped by the existing
  `GOVERNMENT_REGULATORY:<jurisdiction>:<agency>` family. Regulatory type and status
  must agree. A docket, RIN, legal reference or C1 prefix is never an event key.
- News and official company articles: existing explicit classification
  `event_namespace` and `event_key`, with nonempty `classification_source`.
  Event type and the explicit UTC event-date bucket must agree. An unknown event
  date is a separate compatibility value, never an invented midnight.
- SEC filing: existing ten-digit CIK and validated accession syntax, scoped by
  `SEC_FILING:<cik>` and document type. Missing or malformed filing keys do not match.

A news/company article can explicitly identify a supplied regulatory document by
using its agency-family namespace and exact source record ID in the existing news
classification fields. A unique compatible supplied regulatory anchor is required.
Missing or conflicting regulatory anchors leave that report unanchored. This is
an explicit upstream attribution, not URL parsing, title matching or semantic
extraction. No provider has been modified to populate these fields automatically.

The request supplies the context, not an inferred issuer mapping. Known conflicting
tickers/markets receive separate scopes. Entity-only requests also separate known
instrument scopes. Missing markets remain missing in the original records. Target
identity is never inferred from names. This layer does not certify that a report
with missing market metadata actually belongs to the requested instrument.

## Membership, duplicates, and relationships

Document/report roots originate contexts. Exact content identities repeated across
snapshots share an unanchored context while every qualified reference remains
accessible. Repeating an identical input snapshot changes nothing. Changed text is
not an exact duplicate, even with the same URL or bare source ID.

Only generated Phase 7E.1 provenance links propagate membership from source records
to evidence, events, metrics and catalysts. Existing news clusters can reference
multiple contexts; they never cause those contexts to merge. A derived summary may
belong to multiple contexts without becoming an independent source or bridge.

`unassigned` retains container roots, claims, relations and other aggregates without
a safe generated context route. They remain accessible through the original
universe, not dropped or counted as additional events. The union of group members
and unassigned references covers the entire universe. Group/member counts are not
importance, source independence or corroboration measures.

Original lineage is preserved in `GroupedEvidence.universe.lineage`. Explicit
VERSION_OF, TRANSLATION_OF and caller assertions do not union groups. Regulatory
RULE_FAMILY, AMENDS, CORRECTS and SUPERSEDES relationships remain in their original
containers. Proposal/final/correction developments stay separate. No correction is
inferred from C1. Same URL, syndicated hints, similar-external-ID lists, matching
headlines, dates, issuer, sentiment or language never establish event equivalence.

There is no union-find or transitive anchor union. Every root matches one complete
anchor or its own exact content key. Reports cannot bridge conflicting regulatory
status/type anchors. Supporting/contrary claims remain intact; nothing votes on or
resolves their truth. Source families and authorities stay in the source records.

## Precision, validation and limits

All original date-only FSC values, aware timestamps, reporting periods, citations,
assertions and disagreements remain unchanged. Phase 7E.1 temporal observations
remain available; this phase neither filters look-ahead evidence nor ranks recency.

Serializer registrations are additive. Deserialization reconstructs the grouping
from the validated universe and rejects forged derived groups/unassigned references
or identities. A standalone group is a reference value; membership compatibility
is certified by reconstruction in `GroupedEvidence`, not by that value alone.

No metric-period matching, arbitrary official URL matching, DART filing anchor,
semantic grouping, event inference or market provenance bridge is introduced.
Unsupported/ambiguous event matches remain unanchored. Future eligibility, relevance,
materiality, attention, diversity, budgets and agent views are outside this phase.
