# Agent context integration

This optional boundary connects the deterministic Phase 7E.4 EvidenceSelection to
Melchior, Balthasar and Casper. Phase 7E.5 semantic assistance remains deferred.
No live model verification is claimed.

## Calling the boundary

An application caller supplies an already constructed selection explicitly:

```python
result = agent.think(question, research_selection=selection)
rounds = debate.run(agents, question, initial_responses,
                    research_selection=selection)
```

The caller must pass the selection to both initial analysis and debate when both
should use research. Selection is not stored on the agent. Existing calls without
research retain their original request strings and provider behavior. The CLI does
not automatically fetch research or build selections. There is no new persistence.

The actual path is agent.think → history.agent_request → render_agent_context →
existing role-separated provider API → existing AgentResult parser. Debate forwards
the same optional selection. ResearchContext remains the shared two-field model;
the original EvidencePack render_context API remains available and unchanged.

## Trust and memory

Trusted persona, decision instructions, history policy and research policy occupy
the provider's system/instructions field. The common JSON user content has separate
fields for the user question, untrusted historical memory, selected research,
selected provenance, and limitations. Evidence is never concatenated into history.

JSON escaping keeps embedded SYSTEM labels, XML/HTML/Markdown delimiters and fake
role objects inside source data strings. They cannot create provider message roles
or change the trusted instructions. This is a structural defense, not a guarantee
that a model cannot follow an injected instruction. No live model immunity claim is
made. Provider output continues through existing validation; this phase adds no
execution authority or citation validation to AgentResult.

## Selection and provenance

The renderer consumes the public render_view API, including its existing invariant
validation, without adding ranking, relevance, attention, diversity or budget rules.
All agents see the same common selected core in their stored Phase 7E.4 order.
Specialties do not filter evidence. Mitigation, contrary evidence and disagreement
remain available to every agent.

Selected rows, bounded excerpts, citation IDs, uncertainty codes and disagreement
markers are preserved. A separate registry resolves only selected unit, citation and
conflict references. It carries original IDs and available source/provider/publisher,
authority, URL, language, locator and publication/retrieval fields. It never expands
claim inputs, original article bodies, arbitrary metadata or unselected groups.
Provenance denotes origin, not truth, weighting or confidence.

Omissions are counted by reason, availability is reported by input family, and
unassigned aggregates are counted. These are coverage limitations rather than new
facts. Disagreement fallback without excerpts stays an explicit coverage gap.
Unselected evidence text and identifiers are not exposed through those summaries.

## Time and bounds

Target identity, scope and request as_of are explicit. Selected temporal fitness and
published_since are preserved. Future-relative omitted evidence is not reintroduced.
Date-only values stay date-only, including declared legacy midnight conventions;
exact timestamps retain their offsets. No wall clock is used by this renderer.

The Phase 7E.4 evidence budget remains unchanged. The complete system plus user
content has a separate default 60,000 UTF-8 byte ceiling, including history, question,
provenance and policy overhead. Evidence bytes, evidence ceiling and request ceiling
are reported. Overhead varies with selected provenance and caller text. Overflow
raises before provider invocation; it never silently removes citations or notices.
Callers can supply smaller selections/history when needed. This is not an exact
model token count or a guarantee of fitting every provider's context window.

## Provider and result contracts

OpenAI receives instructions/input, Gemini receives system_instruction/contents,
and Anthropic receives system/user content using existing adapters. All consume the
same evidence representation with their appropriate stored agent order.

AgentResult schema, action enums, confidence, risks, evidence gaps, availability,
retry handling and deterministic voting are unchanged. Same validated results
produce the same vote. Research is not automatically saved in memory, the portfolio
ledger or a database. No broker calls, execution, provider changes, semantic assistance,
embeddings, UI, recommendation redesign or Phase 7E core changes are included.

## Offline validation

Tests exercise legacy calls, all stored views, selected-reference closure, bounded
requests, omissions, disagreement, temporal precision, language preservation,
role-like source injection, history isolation, fake provider requests, failures,
AgentResult parsing, debate forwarding and voting invariance. A full constructed
pipeline reaches all three fake providers. Network and persistence operations are
blocked in integration tests; full-suite validation additionally records unexpected
network attempts. Live model behavior remains unverified.
