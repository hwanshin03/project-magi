# Project MAGI

Project MAGI is a Python research and financial-analysis system inspired by the
MAGI System from *Neon Genesis Evangelion*. It collects attributed evidence, prepares
bounded research contexts for three AI agents, and computes a deterministic vote
from their validated responses.

The repository also contains owner-oriented portfolio tools and an isolated virtual
accounting/order-persistence foundation. The proposed closed-Beta paper-trading
platform is **not ready for user orders or deployment**. Project MAGI remains the
internal project name; MOIRAI is a proposed public-facing brand candidate.

## Research and analysis

The implemented analysis pipeline coordinates existing components in memory:

```text
Explicit target, question and as_of
    -> requested research collection / supplied normalized evidence
    -> evidence universe and availability manifest
    -> deterministic grouping, relevance and analytical attention
    -> bounded evidence selection and agent-specific ordering
    -> Melchior / Balthasar / Casper
    -> validated AgentResults -> deterministic VotingResult
    -> immutable AnalysisResult and optional explanation
```

Collection supports SEC, OpenDART, company financial snapshots, company sources,
news and regulatory channels. Optional source failures remain explicit coverage
gaps; a required channel with missing records or known collection gaps stops the
analysis before agent calls. The Python API requires explicit agents and accepts
supplied evidence or injected research services; it never creates live clients
implicitly. The CLI has a separate opt-in live path.

`AnalysisResult` retains normalized inputs, citations, availability, omissions,
policies, selection lineage and agent results. Explicit JSON export/import validates
content-derived identities and recomputes the vote. Research selection is deterministic
for the same inputs and policies; live model responses are not deterministic.
The orchestration API does not automatically save research or results to a database.
See [analysis orchestration](docs/analysis-orchestration.md) and
[finalization safety](docs/analysis-finalization-safety.md).

### Three-agent architecture

These are the provider/model identifiers currently configured in the code:

| Agent | Provider / configured model | Perspective |
| --- | --- | --- |
| Melchior | OpenAI / `gpt-5.5` | Logic, valuation, earnings quality and long-term fundamentals |
| Balthasar | Google Gemini / `gemini-2.5-flash` | Market trends, macro conditions, sentiment and industry developments |
| Casper | Anthropic / `claude-sonnet-4-6` | Downside scenarios, risk management and capital protection |

Agents return structured positions, confidence, rationale, risks and evidence gaps.
Positions include STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL and ABSTAIN. Availability
is AVAILABLE, STALE or UNAVAILABLE; a failed request never becomes an invented HOLD.
Balthasar and Casper additionally validate provider completion/content metadata and
retain bounded diagnostics without raw response text. HTTP 200 alone does not prove
that a usable decision was returned.
Response validation checks structure and completion, not factual correctness.

[VotingEngine](magi/voting.py) uses an unweighted, fixed two-of-three rule:

- Only AVAILABLE, non-ABSTAIN responses vote; confidence does not change vote weight.
- STRONG_BUY/BUY share the BUY category; STRONG_SELL/SELL share SELL; HOLD is separate.
- Two agreeing votes produce BUY_APPROVED, SELL_APPROVED or HOLD.
- Fewer than two eligible voters produce INSUFFICIENT_PARTICIPATION; otherwise a
  split without two agreeing votes produces NO_CONSENSUS.

An LLM explanation cannot override that result. These are analytical outputs, not
order authorization or brokerage instructions.

The legacy interactive workflow (`python main.py` with no arguments) runs initial
responses, three debate rounds, deterministic voting and an explanation. It uses
local historical analysis memory and can write to `data/magi.db`. The research-aware
`analyze` path coordinates collection and one response per agent; it does not add
debate rounds or automatic memory persistence. The Python debate API can receive an
explicit research selection. See [agent context integration](docs/agent-research-context.md).

### Evidence pipeline

| Capability | Implemented behavior and important limits |
| --- | --- |
| Identity and provenance | Instrument market/symbol, issuer namespace/ID and provider symbols remain separate. Explicit mappings and citations preserve attribution; UUIDs and fingerprints do not establish trust or access rights. |
| Official financial evidence | SEC EDGAR submissions/company facts and OpenDART disclosures/statements normalize filing identities, periods, units and Decimal values. SEC is not a complete custom/dimensional-XBRL parser; DART requires explicit year/report choices and has bounded pagination. |
| Financial snapshots | Deterministic metrics, comparable-period growth and margins retain their cited inputs. Missing, conflicting or incomparable values remain explicit; missing amounts do not become zero. |
| SEC analytical projection | Keeps the normalized historical catalog while selecting relevant comparable periods and preserving revisions with an exclusion audit. It does not take arbitrary first-N or latest-N facts. |
| News and regulatory evidence | Attributed items, explicit event metadata, bounded excerpts, language and legal/publication status remain distinct from investment interpretation. Provider sentiment is metadata, not a recommendation. |
| Balanced selection | Qualified references, lineage, deterministic grouping, target relevance, analytical-attention categories, diversity constraints, hard budgets and omission/disagreement audits feed a shared core with agent-specific ordering. |
| Temporal eligibility | Explicit `as_of` uses public-availability rules, preserves retrieval provenance and date-only ambiguity, and excludes future-public evidence. Historical reconstruction is limited by available source metadata. |
| Agent contexts and serialization | Bounded selected evidence and provenance are separate from trusted instructions and historical memory. Tagged JSON snapshots support validated round trips; source text remains untrusted data. |

Repeated reports or translations do not automatically establish independent
corroboration. PRIMARY describes first-party provenance, not truth, investment
importance or voting weight. Some summaries support Korean/English presentation;
original source language is preserved. There is no general automatic translation
or semantic-LLM evidence-selection layer.

Standalone market data is separate: the analysis pipeline's market-provenance bridge
remains deferred. Quotes, adjusted candles and research evidence are not trusted
virtual execution prices.

### Source coverage and historical live checks

Adapter implementation, synthetic offline tests, bounded historical live checks and
operational readiness are different statuses. Sources can be empty, unavailable or
partially collected; support is not uniform across companies or endpoints.

| Source area | Current support / verification boundary |
| --- | --- |
| SEC / OpenDART financial adapters | Implemented with synthetic fixture coverage and explicit financial/reporting limits. This is not universal issuer coverage or a guarantee that every endpoint/query is available. |
| Marketaux news | Implemented; bounded historical live verification covered NVDA/US and Samsung Electronics 005930/KR. Korean-language coverage was not established by that sample. Internal `005930` remains distinct from provider symbol `005930.KS`. |
| NVIDIA News RSS / Samsung Korea Newsroom RSS | Historically LIVE VERIFIED for accepted items. Unsupported item hosts are skipped, so successful ingestion does not imply complete feed coverage. |
| Samsung Global Newsroom RSS | Historically LIVE PARTIAL; an unsafe canonical URL was skipped without weakening URL security. |
| NVIDIA IR / Samsung IR earnings / Samsung announcement HTML | Offline-implemented parsing contracts, LIVE-UNVERIFIED; these are not live-supported sources. |
| BIS / SEC Regulatory / Korean FSC | Historically LIVE VERIFIED through bounded RegulatoryBundle checks. Optional legal/effective-date and relationship metadata can remain absent. |

The company-source adapters currently cover NVIDIA and Samsung Electronics, not a
universal IR crawler. Bounded excerpts and normalized provenance do not constitute
an article-body archive or blanket redistribution permission. Data licensing,
provider availability and agent validity require ongoing review; a historical
success does not certify production reliability or complete end-to-end coverage.

See [official financial adapters](docs/official-research-adapters.md),
[financial snapshots](docs/company-research-snapshot.md),
[Marketaux](docs/marketaux-news.md), its [live-verification record](docs/marketaux-live-verification.md),
[official company sources](docs/official-company-sources.md) and
[regulatory sources](docs/regulatory-research-sources.md).

## Owner-oriented portfolio and data tools

These existing tools are separate from the virtual Beta subsystem:

- Local analysis memory and an account-aware historical portfolio ledger support
  manual BUY/SELL bookkeeping, explicit opening balances, weighted-average cost
  basis and realized P&L. Opening balances do not invent unknown historical trades.
- Opt-in Toss adapters provide quotes, candles, FX, read-only account/holding
  visibility and reconciliation. Brokerage order execution is not implemented;
  Toss cash-balance retrieval is explicitly unsupported.
- Position valuation supports stale/missing-data reporting, native-currency totals
  and optional FX display. It is not complete virtual portfolio equity accounting.

CLI families are `portfolio`, `market`, `broker` and `research`, dispatched by
[main.py](main.py). Some commands call live APIs; bookkeeping and explicit position
imports can write the owner's local ledger. They are not Beta entry points.
Command details: [local memory](docs/memory-portfolio.md), [portfolio CLI](docs/portfolio-cli.md),
[account-aware ledger](docs/account-aware-portfolio.md),
[opening balances](docs/opening-balances.md), [market data](docs/market-data.md),
[broker visibility](docs/broker-read-only.md) and [portfolio valuation](docs/live-portfolio.md).

## Virtual accounting and paper-trading status

| Milestone | Implemented internal capability | Boundary |
| --- | --- | --- |
| Phase 8.2 | Explicit-path isolated SQLite ledger, one-time funding, Decimal-safe cash and BUY/SELL accounting, weighted-average basis, realized P&L, durable fill idempotency and deterministic replay | Synthetic/internal accounting inputs; no trusted trade-execution service |
| Phase 8.3b | Immutable provider-neutral price/source claims, distinct event/provider-observation/retrieval times, price categories, official/finality metadata, synthetic calendar and timezone/DST validation | No approved price provider, operational exchange calendar or execution authority |
| Phase 8.3c | Pure deterministic session selection, explicit cutoff/deadline policy, immutable order intents and cancellation/expiration/terminal-state validation | Synthetic contracts; FILLED is blocked. Five minutes is not an approved operational default |
| Phase 8.3d.1 Package A | Explicit virtual v1-to-v2 schema migration, strict codecs, immutable order/command/event structures, scoped read/replay and rollback/integrity checks | No order-admission or state-write API, active reservations or settlement |

**Legacy virtual v1 clients intentionally reject migrated v2 databases.** The new
persistence layer provides v2 reads/replay, not continued v1 writing support. Migration
requires an explicit trusted internal operation; imports and reads never auto-migrate.

The virtual package requires an explicit isolated database path outside the checkout
and has no owner-database fallback or broker dependency. These code boundaries do
not establish a deployed security perimeter. The closed Beta still requires a
separate environment, database, credentials and authenticated user/team authorization;
owner databases, credentials and brokerage capabilities must remain outside it.

## Repository map

```text
main.py                         CLI dispatch and legacy interactive workflow
prompts/                        Agent personas
magi/{melchior,balthasar,casper}.py  Provider-backed agents
magi/{decision,debate,voting,consensus}.py  Results, debate, voting and explanation
magi/analysis/                  Research-to-agent orchestration and result artifacts
magi/research/                  Financial/news/company/regulatory evidence and balancing
magi/{memory,portfolio,accounts}.py  Owner memory and historical bookkeeping
magi/market/ and magi/broker/    Opt-in data and read-only broker tools
magi/virtual/                   Isolated accounting, contracts and persistence foundation
tests/                         Offline tests and synthetic fixtures
docs/                          Architecture, implementation and verification records
```

## Development setup

Use a Python 3.12 interpreter; the recorded validation environment uses Python
3.12.14. The repository does not yet declare a formal supported-version matrix.
Check `python3 --version` first: the operating system's default Python may be older.
The fresh-checkout commands below assume `python3` resolves to Python 3.12:

```sh
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

[requirements.txt](requirements.txt) pins the runtime SDKs and HTTP/configuration
libraries. Pure virtual tests and the offline analysis plan need no API keys.
Live features use only the relevant configuration:

| Feature | Environment variables |
| --- | --- |
| Model-backed analysis | `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` |
| SEC financial research | `SEC_USER_AGENT` — application/contact identification, not an API key |
| OpenDART research | `OPENDART_API_KEY` |
| Marketaux news | `MARKETAUX_API_TOKEN` |
| Toss market/broker visibility | `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` |

[.env.example](.env.example) contains empty placeholders. Supply values through the
local environment or an untracked `.env`; do not overwrite existing configuration,
commit secrets, copy an owner database or put credentials in source code. Some
legacy imports and persistence-content validation read local configuration, so use
a credential-free environment for isolated development checks.

### Offline analysis plan

```sh
python main.py analyze NVDA --market US --question "Review evidence and risks"
```

Without `--execute`, this prints OFFLINE_PLAN without research/model API calls or
database creation. Add an explicit timezone-aware `--as-of` for a reproducible cutoff;
otherwise the CLI records its start time. Default channels are company, news and
regulatory; the existing `--family` options select other supported channels.
`--execute` is the explicit live-analysis opt-in and may consume API quota. It does
not submit trades. Running `python main.py` with no arguments starts the live-capable
legacy interactive workflow described above.

## Testing

From an activated development environment, run the focused persistence suite:

```sh
python -m unittest discover -s tests -p 'test_virtual_order_persistence.py'
```

It uses synthetic orders and temporary virtual databases, including v1 accounting
fixtures for migration-preservation checks. Importing `tests/test_openai.py`,
`tests/test_gemini.py` or `tests/test_claude.py` does not run their manual API examples;
executing those files as scripts can call live models.

The broader suite covers providers through synthetic fixtures/MockTransport, agent
validation/voting, owner accounting and virtual storage. Full regression should use
an empty credential environment, temporary-only databases and real-network denial
guards. Existing mock Toss fixtures are test-only; they do not authorize real broker
initialization. Pytest tooling is separate from the pinned runtime requirements.

The latest recorded full offline regression is Phase 8.3d.1: **1,563 tests and 1,176
subtests passed** in pytest/unrestricted discovery; unittest passed the same test
count. Syntax checks covered 162 files, imports covered 121 MAGI modules, and dependency
checks passed. These are historical results, not a full-suite rerun for this README
update. See the [validation record](docs/virtual-order-persistence.md#package-a-validation-record).

## Current limitations and safety boundaries

- No authenticated Beta users, team-membership authorization, trusted admission or
  durable user-facing order-submission service.
- No active cash/share reservations, atomic order/reservation operations or settlement.
- No approved trusted execution-price verifier, operational exchange-calendar authority,
  cleared execution-data licensing or automatic virtual fills.
- No virtual corporate-action processing, complete cash/holdings/dividend equity
  valuation, competition operations or production recovery commitments.
- No Research Library UI, web frontend or public Beta deployment.
- No semantic-LLM evidence assistance, Daily Picks or five-day forecast. Evidence
  coverage and model output remain fallible; an agent can abstain or be unavailable.

Research text, official-source labels and model opinions do not authorize financial
writes. Structural context separation reduces instruction confusion but does not
prove prompt-injection immunity. Credentials and private account data must stay out
of shared artifacts, logs and future Beta environments.

## Closed-Beta roadmap

| Area | Status / next dependency |
| --- | --- |
| Research, evidence selection and three-agent orchestration | Implemented; source coverage and live operational reliability remain limited |
| Isolated virtual accounting, pure contracts/lifecycle and Package A persistence | Implemented internally; not deployed as a trading product |
| Package B cash/share reservations | Proposed next implementation milestone; requires separate approval and a versioned schema extension |
| Atomic state/reservation integration and restart reconciliation | Planned after durable reservations, with concurrency/crash tests |
| Trusted settlement | Blocked on price/calendar/time authority, authorization and atomic accounting integration |
| Authentication, team access and research/report interface | Proposed; requires scoped persistence and owner/Beta isolation |
| Closed-Beta operations and deployment | Proposed; requires security/concurrency review, recovery commitments and explicit release approval |

No delivery dates or launch commitment are implied by this roadmap.

## Architecture documentation

Phase documents record their own milestone boundaries; some older status paragraphs
predate later integration. This overview reflects the current committed interfaces.
Historical live-verification records describe bounded observations, not continuous
provider certification.

- Research: [foundation](docs/research-foundation.md),
  [SEC projection](docs/sec-analytical-projection.md),
  [temporal eligibility](docs/temporal-eligibility.md),
  [evidence selection/views](docs/evidence-selection-views.md) and
  [agent context](docs/agent-research-context.md).
- Closed Beta: [architecture and isolation](docs/beta-paper-trading-architecture.md).
- Virtual accounting: [Phase 8.2 core](docs/virtual-accounting-core.md).
- Price/calendar: [proposed execution policy](docs/virtual-execution-price-policy.md)
  and [implemented contracts](docs/virtual-execution-contracts.md).
- Orders: [pure lifecycle](docs/virtual-order-lifecycle.md),
  [reservation/atomicity design](docs/virtual-order-reservations-design.md) and
  [Package A persistence](docs/virtual-order-persistence.md).
