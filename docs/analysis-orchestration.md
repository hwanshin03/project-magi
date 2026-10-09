# Phase 7F: Analysis orchestration

Phase 7F coordinates existing research, deterministic balancing, agent context and
voting APIs. It does not change their analytical rules. All implementation validation
is offline. Controlled live orchestration verification is pending; individual source
verification does not establish verification of this complete pipeline.

## Python boundary

```python
from magi.analysis.models import AnalysisRequest, ResearchInput
from magi.analysis.orchestrator import analyze
from magi.research.balancing.models import InstrumentIdentity, TargetIdentity

request = AnalysisRequest(
    target=TargetIdentity(InstrumentIdentity('US', 'NVDA')),
    question='Review the supplied financial evidence and uncertainties',
    as_of=explicit_timezone_aware_timestamp,
    requested=('sec',),
)
result = analyze(request, inputs=(ResearchInput('sec', (normalized_pack,)),),
                 agents=(melchior, balthasar, casper))
```

The caller no longer constructs EvidenceUniverse, GroupedEvidence, AssessmentSet or
EvidenceSelection. `analyze` calls those existing layers, forwards the resulting
selection through each agent's optional research boundary, and returns an immutable
AnalysisResult with the existing deterministic VotingResult.

Agents are explicitly injected. ResearchServices accepts existing SEC/DART providers,
NewsService, CompanySourceService and RegulatoryService. Supplied normalized inputs
override collection only for their explicit requested channel. No service or agent
is implicitly constructed by the Python orchestration API. Callers own injected
client lifecycles. Collection runs sequentially in fixed channel order, with snapshot
after filings; this is operational ordering, not evidence priority.

## Request and identity

AnalysisRequest reuses TargetIdentity and derives a SelectionRequest from target,
explicit as_of, question/scope and languages. Instrument market/symbol and issuer
namespace/identifier remain distinct. Supplying both requires mapping attribution.
There is no fuzzy matching or invented issuer relationship. SEC/DART collection
checks the provider's resolved exact issuer and instrument against the request.
Missing caller-supplied issuer mapping remains a warning. It is not filled by
similarity or inferred from a headline.

The request holds existing assessment/selection policies, requested and required
collection channels, optional financial reporting parameters, bounded collection
limit for news/regulatory collection, explicit regulatory source keys and optional historical context. It contains
no desired recommendation, confidence target, portfolio action or position size.

Supported channels are `sec`, `dart`, `snapshot`, `company`, `news`, and `regulatory`.
Company and news containers share the existing NEWS input family; SEC/DART share
RESEARCH. Family availability aggregates their supplied snapshots and prefixed
channel error/omission codes. Independent inputs are deduplicated through the
existing snapshot identities. Conflicting duplicate channel results are rejected.

## Collection and failure policy

Existing provider/service APIs retain their security, normalization, retrieval
clocks and provenance. Orchestration never fetches a URL directly. The financial
snapshot calls the existing Phase 7C builder on one supplied/collected financial
pack; missing inputs or unavailable snapshot construction remain explicit gaps.
DART financial collection requires an explicit year. SEC collection keeps its existing
provider fact cap. No metrics are recalculated
here. Regulatory collection preserves partial source failures and skipped-item
limitations. Company collection preserves skipped unsafe-item limitations.

The existing availability states remain distinct:

- NOT_SUPPLIED: family was not provided/requested.
- AVAILABLE: supplied normalized containers have evidence without known gaps.
- EMPTY: successfully supplied normalized containers contain no records.
- UNAVAILABLE: requested collection failed or was not configured, with no container.
- PARTIAL: containers are present along with errors or omissions.

Optional missing or failed channels may continue. Required channels must contain
records and have no known errors/omissions; otherwise execution stops before agents.
A provider failure never fabricates an empty success container. Corrupt normalized
inputs, conflicting identities and pipeline invariant failures stop with controlled
errors. Unexpected agent failures stop; they do not create artificial AgentResults.
Expected provider errors, retry exhaustion and malformed model output retain the
existing agents' UNAVAILABLE results, including original error/attempt information.
They never become HOLD.

## Context, time and voting

The pipeline uses the completed agent context integration without concatenating
research into prompts or memory. All three agents retain the shared core in their
stored order. Citations, budgets, uncertainty, omissions and disagreement notices
remain owned by Phase 7E. Question/scope is explicit; no semantic interpretation or
model-based selection is introduced.

Explicit as_of is never replaced with the current clock or moved after collection.
Phase 7F.3 admits prior-public information collected later, using explicit publication
and preserving date-only ambiguity. Unknown publication falls back conservatively
to retrieval; future-public information remains excluded. Retrieval and normalization
timestamps are retained for provenance, not backdated. See
[temporal eligibility](temporal-eligibility.md) for historical-analysis limits.

VotingEngine remains authoritative, including its two-of-three and partial
availability rules. An optional injected explanation engine receives the completed
vote; its prose cannot replace it. Explanation failure preserves the vote and is
recorded separately. Phase 7F does not add debate rounds; the existing debate API
and optional selection forwarding remain available and unchanged.

Historical context is supplied explicitly and temporarily passed through existing
agent history fields, then restored. No memory/database/portfolio/broker API is
called. Existing legacy CLI behavior is unchanged. No automatic persistence occurs.
The market provenance bridge remains deferred; MARKET stays NOT_SUPPLIED. Existing
standalone market commands remain separate and no market observations are relabeled
as research evidence.

## Result and reproducibility

AnalysisResult references the immutable request and EvidenceSelection, which already
contains assessment, grouping, universe, normalized inputs, availability and lineage.
It stores validated AgentResults and an existing VotingResult, plus optional
explanation/failure state. The audit graph retains input snapshot IDs, manifest ID,
grouping/assessment/selection IDs, omissions and warnings without raw HTTP payloads.

Request and artifact IDs are content-derived. Identical normalized inputs, policies
and agent outputs produce identical artifacts irrespective of input ordering or
exact duplicate replication. Live model outputs are not claimed deterministic.
Artifact identity is not a live execution-instance ID; no random execution ID or
additional runtime-clock metadata is introduced. The optional CLI default becomes
an explicit recorded analysis cutoff, not an execution-instance ID.
Agent outputs/explanation can change the final
artifact identity without changing the deterministic research selection identity.

`magi.analysis.models.dumps/loads` provide explicit lossless JSON export/import using
the existing research tagged codec for research objects. Derived selection fields
and artifact identities are checked on import; voting is recomputed using the
existing engine. These functions do not write files or databases. Explicit exports
contain the supplied normalized audit graph/history/output, so the caller controls
retention. Source security checks reject credential-bearing content.

## CLI

The existing dispatcher now supports:

```text
python main.py analyze NVDA --market US --question "Review evidence" \
  --as-of 2026-09-30T12:00:00+00:00
```

Without `--execute`, this prints an offline plan and constructs no provider/model
clients. If `--as-of` is omitted, the CLI captures its start time once and records
that cutoff in the request. An explicit timestamp is preserved without consulting
the clock. Use an explicit cutoff for reproducible runs. Add repeated
`--family` choices to select channels; defaults are company, news and regulatory.
Use `--required` for channels that must succeed, `--year` and `--report` for financial
collection, `--since` for publication horizon, and `--language` for supported filters.
An explicit `--issuer` also requires `--mapping-reference` and the canonical
zero-padded provider identifier (ten digits for SEC, eight for DART).

`--execute` explicitly opts into existing research and model APIs and may consume
quota. It has not been invoked against live services during implementation. The CLI
uses closed company RSS defaults for NVDA/US and 005930/KR; it does not claim IR HTML
endpoints are live verified. Resources created by the CLI are closed on completion.
Its output is a compact audit summary and deterministic vote, not a persisted record
or trade. There are no broker, trading, portfolio, or live execution flags beyond
analysis API invocation.

Semantic assistance (Phase 7E.5), embeddings, new providers, UI, trading, position
sizing, database migrations and automatic research persistence remain out of scope.
