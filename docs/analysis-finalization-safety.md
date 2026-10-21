# AnalysisResult content safety and finalization diagnostics

Phase 7F.4r corrects a confirmed offline compatibility defect. AgentResult's
JSON serializer introduces escape backslashes for newline, tab and quotation
characters. A letter followed by a colon and one of those escapes could match
the balancing fingerprint validator's Windows-drive-path rule. Original safe
reasoning, key_risks and evidence_gaps could therefore fail finalization.
The historical KO live failure may have involved this defect, but its exact
cause remains unproven: the original live objects were not retained.

## Narrow correction and unchanged contracts

Only `magi/analysis/models.py` changes production behavior. AnalysisResult first
reconstructs each AgentResult to verify its schema and immutability, then visits
original strings from its dataclass fields (including identity, optional fields,
nested values and keys). Those strings pass the existing balancing fingerprint
boundary, which applies existing credential checks and local-path rejection
before JSON escaping. The existing serialized credential check is retained.
Literal Windows paths, POSIX paths covered by the existing policy, file-resource
references and credential-bearing strings remain rejected. JSON-looking input
receives no exemption. Actual literal backslashes remain subject to the same
path rule; this does not expand or weaken the established path policy.

The final artifact payload is unchanged: request identity, selection identity,
serialized ordered AgentResults, explanation, explanation error and optional
SEC projection identities. All non-agent fields still pass the shared fingerprint
validator. The already validated agent representations enter the same tagged
encoder and the same BF SHA-256 identity calculation, followed by the same AN
identity calculation. JSON ordering, separators, encoding, field selection and
version remain unchanged. No unchecked replacement payload is accepted from a
caller. The shared fingerprint implementation and all its other callers are
unchanged.

Regression tests compare against an independent copy of the old canonical hash
formula. A known valid artifact/request/selection identity is pinned from the
committed pre-correction constructor. Safe formatting is checked against the
same formula and round-tripped through production serialization. No identities
are regenerated under a new version. Genuine request, selection, projection,
audit and citation corruption still fails validation.

## Bounded diagnostics

Existing finalization boundaries attach a frozen `FinalizationDiagnostic` to
`AnalysisError`, with category `VALIDATION_FAILED` and exactly one enum stage:

- REQUEST_VALIDATION
- SELECTION_VALIDATION
- AGENT_CONTENT_SAFETY
- VOTING_VALIDATION
- EXPLANATION_VALIDATION
- SEC_PROJECTION_VALIDATION
- ARTIFACT_FINGERPRINT

`AGENT_CONTENT_SAFETY` includes schema reconstruction and membership-duplication
checks before content validation. The category intentionally does not expose
underlying exception messages or finer-grained content. Direct constructors
still raise a ValueError subclass. The orchestrator's existing AnalysisError
pass-through preserves the public `INVALID_ANALYSIS_RESULT` code and now also
preserves these diagnostics. No orchestrator change is needed.

Diagnostics contain no rejected text, model output, source excerpts, prompt,
credentials or exception objects. Exception display suppresses the original
context. As with ordinary Python exceptions, applications must not introspect
traceback locals or suppressed exception chains for logging. Diagnostics are
in-memory only; no logging or persistence is added. Successful serialization
has no new fields. Failures before finalization retain their existing errors.

## Synthetic coverage

Permanent offline tests cover safe plain text, newlines, colon-space,
colon-newline, colon-tab, colon-quote, repeated sequences, Korean/Unicode,
and literal non-path backslashes across original fields. They also cover real
path patterns, credentials, JSON-looking path strings, malformed/missing agent
fields, duplicate/unknown agents, forged identities, corrupted citation closure,
projection policy/audit mismatches, invalid source availability, duplicate JSON
keys, serialization tampering and bounded immutable diagnostics.

A clearly synthetic KO/US fixture exercises the actual orchestration pipeline:
5 groups and assessments; 3 selected groups and units; 4 citations; 7 unique
selected references; 2 REVIEW_LIMIT omissions. NEWS is PARTIAL because
COMPANY.COLLECTOR_NOT_CONFIGURED; RESEARCH is AVAILABLE; other families are
NOT_SUPPLIED. Synthetic revenues use 2024/2025 calendar-year annual periods;
synthetic assets use a separate 2025 year-end instant. Missing accounting
concepts remain missing. These are not actual issuer facts or recovered live
model responses.

All three synthetic agents are AVAILABLE/ABSTAIN with varied confidence and safe
formatting in reasoning, risks and gaps. Independent VotingEngine validation
confirms zero eligible voters, zero BUY/HOLD/SELL votes and
INSUFFICIENT_PARTICIPATION. Finalization, round-trip serialization and deterministic
artifact identity succeed without bypassing production provenance validators.

## Scope and remaining verification

Agent models, prompts, response schemas, completion validation, retry policies,
timeouts and output budgets are unchanged. Voting, provider collection, temporal
eligibility, selection algorithms, broker behavior and dependencies are unchanged.
No live verification is part of this correction. After review and separate
approval, a bounded KO end-to-end live retest can verify finalization and an
in-memory serialization round trip with the fixed stage metadata available on
failure. Do not retain raw responses or infer the historical root cause from a
successful future run.
