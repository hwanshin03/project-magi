# Personal research failure diagnostics

The first owner-operated NVDA pilot produced a complete report but insufficient
participation: Melchior returned a valid ABSTAIN, Balthasar returned
`invalid_response`, and Casper returned
`provider_unavailable:transient_failure_exhausted`. There were zero directional
votes. Successful export is not successful participation. The original private
report is not rewritten, copied into this repository, or used as a fixture.

## Evidence standard

- **CONFIRMED ROOT CAUSE of diagnostic loss:** Balthasar and Casper retained safe
  validation categories only on the adapter instance. Orchestration retained
  `AgentResult` only. Response validation results lost observed attempts, while
  Markdown omitted the existing provider-failure attempts and HTTP status.
  Synthetic Gemini invalid JSON reproduced adapter `invalid_json` / one attempt
  versus result `invalid_response` / zero attempts before this correction.
- **REPRODUCIBLE FAILURE CLASS:** typed synthetic Gemini candidate, completion,
  content, JSON and schema failures produce unavailable results. Synthetic
  Anthropic timeouts, transport failures, retryable HTTP errors and nonretryable
  errors preserve unavailable/no-vote behavior with the existing retry policy.
- **PLAUSIBLE BUT UNVERIFIED:** any of those compatible failures could explain
  the historical coarse classifications. Casper's transient class includes
  timeout/transport and retryable statuses; no particular status is established.
- **INSUFFICIENT EVIDENCE:** the precise historical Gemini validation failure and
  Anthropic exception/status are absent from the saved report. No raw responses
  or exception records are recovered or invented. This is not proof that live
  provider availability has been fixed.

## Minimal correction and file-by-file rationale

The proposed correction was recorded before implementation: expose only safe
failure classifications and already observable attempts/status through existing
result and presentation boundaries. No response-behavior change was justified.

- `magi/provider.py`: classify caught exception types and numeric status using
  fixed codes; omit malformed HTTP metadata. Retry decisions, limits, sleeps and
  exception-catching behavior remain unchanged. No exception text is inspected.
- `magi/decision.py`: add optional, allowlisted `diagnostic_code` to immutable
  `AgentResult`; forward provider classifications and retain diagnostics when
  reconciling a stale answer. No position/schema parsing or vote rules change.
  Absent codes are omitted from JSON to preserve legacy serialization and
  artifact identities. Existing consumers need not supply this optional field.
  The code is non-comparing metadata: legacy decision equality and history-store
  roundtrips remain unchanged without changing persistence. Legacy history stores
  do not retain the code; explicit research JSON and Markdown do. Full regression
  exposed this compatibility requirement and it was corrected within the result
  boundary, without a database or history-store change.
- `magi/balthasar_response.py` and `magi/casper_response.py`: copy the existing
  validation category and actual attempt counter into the returned result.
  Provider failures retain their more specific transport/status code.
- `magi/analysis/presentation.py`: render the optional fixed code, observed
  attempts and safe HTTP status. Missing metadata is explicitly unavailable.
- `tests/test_research_failure_diagnostics.py`: exercise real adapter code with
  mocked SDK constructors, typed synthetic responses and synthetic exceptions;
  test roundtrip, consensus, report and CLI boundaries without live clients.
- `tests/test_casper_response.py`: retain the existing completed-response check,
  updating its expected result to include the observed single attempt.
- This document records the scope, evidence and remaining uncertainties.

Security impact: bounded fixed codes and numeric metadata only. No prompt,
response, excerpt, header, URL, account ID, exception message, credential or
validation value is retained in the new diagnostic. No diagnostic file, database,
cache or telemetry service is introduced. Existing research content-safety
validation remains active.

Regression impact: `error`, availability, positions, confidence and voting
eligibility are unchanged. New results with diagnostic codes or corrected
attempt counts can have different artifact identities because their recorded
metadata differs. Legacy results without the new field still roundtrip with
unchanged identities. CLI exit codes remain unchanged. Default audit JSON has the additive nested
`diagnostic_code` field described below; it is not byte-for-byte unchanged.
Models, token limits, SDK configuration, timeouts, prompts and retry ceilings are
unchanged. Melchior response parsing is unchanged; its missing success/validation
attempt count remains unknown rather than inferred.

## Failure classifications

Gemini's existing validation path distinguishes:

- missing or unsupported candidate;
- blocked response;
- incomplete response;
- missing/unknown completion metadata or unsupported completion;
- unsupported content or empty response;
- invalid JSON (including duplicate keys, prose and Markdown wrappers);
- invalid schema (including missing required fields, invalid position or types).

Anthropic's existing response path distinguishes incomplete response,
invalid completion metadata, unsupported completion/content blocks, empty
response, JSON decoding failure and schema validation failure. Strict parsing
and completion validation are unchanged; apparently valid JSON in an incomplete
response remains rejected. No JSON repair or fabricated values are introduced.

Caught provider failures expose one of:
`provider_timeout`, `provider_transport_error`,
`provider_authentication_failed` (401/403), `provider_rate_limited` (429),
`provider_service_unavailable` (503), `provider_server_error` (other 5xx), or
`provider_error` (remaining caught errors). These describe observable type/status,
not an inferred provider root cause. Missing HTTP status stays unavailable.
Casper still allows one provider attempt, with no automatic retry; shared
providers retain their existing bounded retry behavior. Unknown programming
exceptions still fail the orchestration with its fixed execution-error code.

A successful validated ABSTAIN has no failure code. Invalid responses and
provider failures are UNAVAILABLE and cannot vote. Missing required evidence
prevents agent execution. Insufficient directional participation is a voting
outcome, not a parser or provider error. These states remain distinct.

`AgentResult` does not require provenance fields or verify references embedded
in rationale text. Missing required `evidence_gaps` is a schema error; an
unresolved citation in otherwise valid rationale is not. No new citation or
semantic validation is added here. No raw-value field paths are exposed.

The detailed per-adapter completion/block/token summaries remain in memory.
Only the failure code and observable attempts/status are forwarded; this is
intentional bounded observability rather than persistent raw logging. HTTP
status on successful SDK responses remains unavailable when not supplied.

## Research limitations and next step

The pilot's two selected financial observations did not provide a sufficient
operating/profitability/cash-flow picture. No trusted current quote or verified
valuation bridge was available. Its Marketaux item had unresolved target
connection. Consequential company-source claims lacked independent corroboration;
first-party provenance alone does not establish truth. These evidence issues
are recorded without changing collection, selection or market-data integration.

First review this narrow diagnostic change independently. A separately approved
single bounded live run could then produce useful safe failure classifications
if a failure recurs, but cannot reconstruct the original missing metadata or
guarantee success. No additional live run is authorized by this offline task.

## Historical pre-audit developer validation

After compatibility and simplification review: focused regression passed 522
tests and 436 subtests; full pytest passed 2,074 tests and 1,285 subtests;
unittest and unrestricted discovery each passed 2,074 tests (47 discovered
files). Syntax passed for 177 Python files, imports for 129 MAGI modules including
the root package, and dependency pins / pip check passed. Guards recorded zero
network attempts and zero boundary violations in the final runs. Each full run
constructed 121 existing synthetic MockTransport Toss fixtures, separately from
zero real broker initialization. All SQLite activity was isolated test data.
The only pytest warning was the existing SDK/Pydantic deprecation warning.


## Independent audit remediation: AFD-01 / AFD-02 / AFD-03

The first implementation was blocked by independent audit. AFD-01 reproduced
strings and mutable containers entering result attempt metadata and JSON while
Markdown hid them. AFD-02 reproduced a secondary TypeError when SDK status was a
list or dictionary; this also existed in the committed baseline. AFD-03 identified
an inaccurate claim that default nested audit JSON was unchanged.

AFD-01 now uses one shared attempt validator at the result boundary and at entry
to both response helpers, before inspecting a response or retaining diagnostics.
Only exact Python integers 0 through 3 are accepted. Zero is the legacy unknown
sentinel, not evidence of zero requests; None, booleans, floats, strings, containers,
negative and excessive values raise a fixed ValueError. No coercion or clamping
occurs. Counts describe SDK invocation attempts, including retries, not parsing
passes or independently observed network transmissions. Existing Casper calls
still use one attempt; accepting historical metadata never changes retry policy.
AgentResult also accepts HTTP status only as None or an exact integer 100–599.
These checks apply to direct construction and research deserialization, preventing
mutable aliases and arbitrary data in JSON. Markdown renders zero/None as
not available; JSON retains the documented zero/null representations.

AFD-02 normalizes unsupported SDK status values to None before set membership,
retry decisions or fixed-code classification. Valid integer status behavior is
unchanged, including 401/403 non-retry and the existing 429/500/503 retry limits.
Malformed status alone does not justify a retry. Exception-type precedence is
unchanged: an observed timeout/transport exception can still be transient when
its HTTP metadata is absent or malformed. No message parsing is used.

AFD-03 is resolved by accurate additive-compatibility documentation and objective
schema comparisons. Standalone AgentResult and explicit research JSON omit an
absent diagnostic_code. Nested VotingResult/default CLI audit JSON uses asdict
and includes diagnostic_code: null when absent, or the fixed code when present.
The voting serializer is outside the authorized correction files, so it is not
changed. Legacy persisted history schema and equality remain unchanged; history
still omits the new code. Consumers of nested JSON must tolerate this optional
additive field. No byte-for-byte default audit compatibility is claimed.

Ten additional permanent tests cover both helper boundaries, mutable-input
rejection, direct and deserialized numeric metadata, JSON/Markdown roundtrips,
installed Gemini SDK malformed statuses, Anthropic errors, valid-status retry
behavior, explicit legacy/nested schema comparisons and all 125 five-state
three-agent voting combinations. One previous presentation test now requires
rejection of invalid metadata before checking the valid unknown representation.
No raw responses, credentials, prompts or exception text are retained. Historical
live failure causes remain unknown; these offline corrections do not establish
live provider reliability and authorize no additional paid request.


### Remediation validation

Focused regression: 532 passed, 631 subtests. Full pytest: 2,084 passed,
1,480 subtests. Full unittest and unrestricted discovery: 2,084 passed each;
unrestricted discovery covered 47 files. Syntax: 177 Python files. Imports:
129 MAGI modules including root. Dependency pins, pip check, whitespace,
secret-pattern and exact eight-file scope checks passed. Final guarded runs
recorded zero network attempts and zero boundary violations. Each full run
used 121 existing synthetic MockTransport Toss fixtures; no real broker was
initialized. No credentials or owner databases were accessed. Original report
hash, size and permissions remained unchanged. These are developer remediation
results, pending independent re-audit, not a live-provider reliability claim.
