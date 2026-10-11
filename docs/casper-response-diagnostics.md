# Casper response completion and diagnostics

Phase 7F.4a is an offline compatibility change. It does not establish the cause
of the previous live invalid JSON response or prove live compatibility.

Casper uses `claude-sonnet-4-6`, `max_tokens=2000`, prompt-only JSON,
a 30-second timeout and the existing shared retry policy. No structured-output
API option, output repair, Markdown stripping or parsing retry is enabled.

Only `end_turn` responses containing text blocks with string values reach JSON
validation. Multiple text fragments are concatenated in order. Empty fragments
are permitted; empty/whitespace-only combined text is rejected. Mixed/non-text
blocks are rejected rather than silently discarded. Missing/unknown completion
metadata fails closed. Output-token usage is diagnostic, not proof of completion.

| Condition (checked in this order) | Diagnostic failure category |
| --- | --- |
| Existing provider failure | `provider_failure` |
| `max_tokens`, `pause_turn`, `model_context_window_exceeded` | `incomplete_response` |
| Missing/unknown stop reason | `invalid_completion_metadata` |
| Other known stops (`tool_use`, `refusal`, `stop_sequence`) | `unsupported_completion` |
| Invalid content container, non-text blocks or non-string text | `unsupported_content_blocks` |
| Empty content or combined whitespace-only text | `empty_response` |
| Strict JSON decoding/duplicate-key failure | `json_decoding_failure` |
| Decoded JSON rejected by the existing decision contract | `schema_validation_failure` |
| Completed, valid decision | `None` |

`max_tokens` is rejected even if the text appears to contain a complete JSON
object. No synthetic decision is substituted. All response failures preserve
`UNAVAILABLE / invalid_response`, absent position and zero confidence. Existing
transport failures preserve their original error, attempts and HTTP status.
Voting and other providers' parsing semantics are unchanged.

`Casper.last_response_diagnostic` is an immutable, in-memory `CasperDiagnostic`.
It resets at the start of every invocation, including before request rendering.
It is not added to AgentResult, serialized analysis results, prompts or storage.
It contains only:

- Fixed configured provider/model identifiers and output-token budget.
- Allowlisted stop category, or `MISSING` / `UNKNOWN`.
- Integer output-token usage in the configured 0–2000 range, otherwise `None`.
- Counts of allowlisted block types, with unknown/missing types aggregated.
- A fixed failure category, or `None` on success.
- Actual invocations of the existing SDK request callback, including shared retries.

No response text, source excerpts, prompts, tool inputs, response-supplied model
identifiers, headers or exception messages are retained in diagnostics. Unexpected
programming errors are not converted into fabricated decisions. The orchestrator
continues to handle them through its existing execution-failure boundary.

The adapter pre-decodes JSON with the shared duplicate-key rule solely to separate
decoding failures from decision-schema failures. The existing shared parser remains
the authority for AgentResult validation; no alternate decision schema is introduced.
The shared synthetic provider fixture now includes explicit completion and block
metadata, matching the SDK response shape required by this stricter adapter.

## Verification boundary

Synthetic tests cover typed SDK responses, completed and incomplete output,
multiple/mixed/invalid blocks, malformed and schema-invalid JSON, Unicode and
escaping, safe diagnostics, retry attempt counts, diagnostic reset, and voting.
A later separately authorized live verification should inspect only these safe
metadata fields. This change does not justify increasing tokens, changing models,
adding retries or enabling structured output without further evidence.

## Phase 7F.4c: bounded output budget adjustment

The Phase 7F.4b controlled request returned HTTP 200, `stop_reason=max_tokens`,
and 1000 output tokens under a 1000-token budget. This establishes exhaustion
for that request, not the precise cause of earlier failures.

The Casper-only `MAX_TOKENS` constant increases from 1000 to 2000. This is the
initial bounded candidate requested for evaluation: the response contract asks
for detailed rationale, risks and evidence gaps inside strict JSON. The observed
exhaustion justifies additional headroom, but does not establish a minimum
sufficient budget or guarantee completion at 2000 tokens. Larger output can
increase cost and latency; the existing 30-second timeout remains unchanged.

The same constant already supplies the request budget, diagnostic budget and
safe token-usage range. No settings layer or per-provider abstraction is added.
Other agents, models, retry limits, completion checks, JSON validation, voting
and persistence are unchanged. `max_tokens` remains an incomplete response even
when the returned JSON looks complete. No Structured Output or repair is enabled.

Offline tests cannot establish live sufficiency. A separately authorized retest
should use the same synthetic context, exactly one transmitted Casper request,
and only safe completion diagnostics. Do not automatically escalate the budget
or retry if it fails.
