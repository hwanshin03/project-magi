# Balthasar response validation and safe diagnostics

Phase 7F.4i is offline implemented. It has not been live verified. The earlier
Gemini HTTP 200 / `invalid_response` failure cannot be conclusively explained:
its response body and completion metadata were not retained. Phase 7F.4h proved
that the old `response.text` path discarded completion/candidate information,
selected the first candidate, omitted non-text parts, and collapsed JSON and
schema failures. Those are confirmed gaps, not proof of the historical cause.

## Unchanged request contract

Balthasar still calls Google GenAI 1.47.0 `models.generate_content` with
`gemini-2.5-flash`, the existing contents and system instruction, a 30-second
SDK timeout, one SDK attempt and up to three shared-provider attempts. Retryable
failures retain the shared 0.5/1-second backoffs. Prompts, history, research
context, sampling, thinking, candidate count, MIME type and response schema are
unchanged. No output-token limit is explicitly configured; `None` means
unconfigured, not zero or a known server default. No JSON schema generation,
repair call, extra provider request or malformed-output retry is introduced.

## Completion and extraction

`magi.balthasar_response.response_result` consumes SDK-typed responses directly;
it never calls the SDK `response.text` convenience property.

- Exactly one typed candidate is required. No candidates and ambiguous/malformed
  candidates have separate failure categories; another candidate is never chosen.
- Only `STOP` proceeds to extraction. The SDK defines STOP as a natural stop **or
  a configured stop sequence**; it is not a stronger guarantee of completeness.
- `MAX_TOKENS` is incomplete even when text looks like valid JSON. Safety,
  recitation, blocklist, prohibited-content, SPII and image-safety/prohibition
  finish reasons are blocked. Other known non-STOP reasons are unsupported;
  missing, unknown and unspecified reasons are invalid completion metadata.
- Explicit prompt-block feedback also prevents acceptance.
- Content and parts must have the SDK's expected structure. Unsupported media,
  tool/code output, mixed text/non-text parts and missing ordinary text fields
  are rejected. Part categories are counted without serializing part objects.
- Thought parts are counted and excluded. Thought text and opaque signatures
  are never included in extracted JSON or diagnostics. A signature on a text
  part is ignored, not retained. Thought-only and empty content have no usable
  response.
- Multiple ordinary text parts concatenate **in original order without added
  separators**. The result must be exactly one JSON document. Two concatenated
  documents fail; nothing is searched, stripped, inferred or repaired.

## Strict parsing and public compatibility

Surrounding whitespace is accepted. Markdown, surrounding prose, trailing text,
duplicate keys, NaN/Infinity and numeric overflow to infinity are rejected.
The additional JSON preflight supplies precise failure diagnostics and rejects
nonstandard numbers anywhere in the document; the existing `parse_decision`
remains the authority for the unchanged AgentResult schema. Its deliberate
second decode avoids changing the shared parser or unrelated agents.

Position, confidence, reasoning, risks and evidence gaps retain their existing
validation. Response failures remain public `UNAVAILABLE / invalid_response`,
with no position, zero confidence and no eligible vote. Provider failures retain
their existing public reason, attempt count and HTTP status. Voting,
AnalysisResult, selection, citations, temporal rules and other agents are unchanged.

## In-memory diagnostic contract

`Balthasar.last_response_diagnostic` is cleared at invocation start and set to an
immutable `BalthasarDiagnostic` after a returned response/provider failure.
It records provider/configured model, bounded finish and prompt-block categories,
candidate count, fixed part-category counts, thought count, usable-text presence,
optional output/thought/total token counts, configured output budget (`None`),
extraction/JSON/schema statuses, failure category and actual callback attempts.

Usage fields accept only nonnegative integers within signed 64-bit range;
missing or invalid values become `None`, while a genuine zero remains zero.
Stages are `NOT_ATTEMPTED`, `PASS` or `FAIL`. Completion rejection occurs before
JSON parsing; subsequent stages remain unattempted.

Internal failure categories:

- `provider_failure`
- `incomplete_response`
- `blocked_response`
- `missing_candidate`
- `unsupported_candidate`
- `invalid_completion_metadata`
- `unsupported_completion`
- `unsupported_content`
- `empty_response`
- `invalid_json`
- `invalid_schema`

Diagnostics contain no raw response, thought, signature, prompt, evidence excerpt,
credential, header, provider message or arbitrary exception text. Nothing is
persisted. Counts and enums are observations, not evidence of decision quality.

## Verification and next step

Permanent synthetic tests use installed SDK response types; deliberately malformed
objects use explicitly labeled `model_construct`/fake objects. Coverage includes
completion priority, candidate ambiguity, thoughts/non-text, multipart JSON,
strict syntax/schema, Unicode, usage sanitization, redaction, attempt accounting,
configuration preservation, provider failures and no-vote behavior. Existing
integration fixtures now provide typed STOP candidates rather than text-only fakes.

After review and separate authorization, perform one controlled Balthasar retest
with representative selected evidence. Capture only the safe diagnostic, measured
latency and sanitized HTTP status; independently enforce the authorized request
budget. Do not retain raw responses or prompts. This phase neither authorizes
nor performs that live retest. Server defaults, live response compatibility and
the original failure cause remain unverified.
