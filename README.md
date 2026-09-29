# Project MAGI

An AI decision-making system inspired by the MAGI System from *Neon Genesis Evangelion*.

Project MAGI combines multiple state-of-the-art large language models into a collaborative decision-making system. Instead of relying on a single AI, multiple agents analyze the same problem, debate with one another, and produce a final consensus.

---

# AI Members

### 🧠 Melchior
- Model: OpenAI GPT-5.5
- Role: Logic & Analytical Reasoning

### 📊 Balthasar
- Model: Google Gemini
- Role: Market Data & Trend Analysis

### ⚖️ Casper
- Model: Anthropic Claude
- Role: Risk Assessment & Conservative Judgment

---

# Current Architecture

```
                User Question
                      │
                      ▼
      ┌─────────────────────────────────┐
      │      Initial Responses          │
      └─────────────────────────────────┘
          │        │              │
          ▼        ▼              ▼
     Melchior   Balthasar      Casper
          │        │              │
          └────────┴──────────────┘
                     │
                     ▼
             Debate Engine
          (Multiple Discussion Rounds)
                     │
                     ▼
            Consensus Engine
                     │
                     ▼
              Final Decision
```

---

# Current Features

- ✅ OpenAI API integration
- ✅ Google Gemini API integration
- ✅ Anthropic Claude API integration
- ✅ Three independent AI agents
- ✅ Multi-round debate engine
- ✅ Consensus generation
- ✅ Modular architecture
- ✅ Secure API key management (.env)

---

# Example Workflow

```
Question

↓

Melchior Response

↓

Balthasar Response

↓

Casper Response

↓

Debate Round 1

↓

Debate Round 2

↓

Debate Round 3

↓

Final Consensus
```

---

# Example Question

```
Should I buy more SK Hynix at the current valuation for a 5-year investment?
```

The system generates:

- Initial independent opinions
- Multi-round debate
- Revised arguments
- Final consensus

---

# Repository Structure

```
Project MAGI

magi/
    melchior.py
    balthasar.py
    casper.py
    debate.py
    consensus.py

tests/
    test_openai.py
    test_gemini.py
    test_claude.py

main.py
README.md
requirements.txt
```

---

# Roadmap

## Version 1.3

- Persona specialization
- Better prompt engineering
- Improved debate quality

## Version 1.4

- Memory system
- Persistent conversation history
- User preference learning

## Version 2.0

- Real-time stock prices
- Financial statement analysis
- News integration
- Economic indicators

## Version 3.0

- Portfolio management
- Buy / Hold / Sell recommendations
- Confidence scoring
- Risk scoring

## Future Vision

- Web application
- Desktop application
- Voice interaction
- Multi-user support
- Cloud deployment
- Autonomous investment assistant

---

# Technology Stack

- Python
- OpenAI API
- Google Gemini API
- Anthropic Claude API
- Git
- GitHub
- VS Code

---

# Status

Current Version:

**v1.2**

Completed:

- OpenAI Integration
- Gemini Integration
- Claude Integration
- Debate Engine
- Consensus Engine

Project Status:

**Actively under development**
## Local memory and portfolio ledger

Completed analyses now append to the local SQLite database at `data/magi.db`.
MAGI retrieves up to three recent matching analyses as untrusted historical context;
its final action still comes exclusively from fresh deterministic two-of-three voting.
Storage failures do not invalidate that action.

A Python portfolio service records manually supplied BUY/SELL transactions and derives
holdings, weighted-average book cost, and realized/unrealized analytics with Decimal.
It does not execute trades or fetch market prices. This is not tax-lot accounting.

See [the memory and portfolio guide](docs/memory-portfolio.md) for the complete schema,
service examples, accounting definitions, security boundaries, and offline test command.

## Portfolio CLI

Record trades that already occurred and inspect the local ledger:

```sh
python main.py portfolio buy NVDA 10 180 --currency USD --market US
python main.py portfolio sell NVDA 4 210 --market US
python main.py portfolio show NVDA --market US --current-price 202.50
python main.py portfolio list
python main.py portfolio history NVDA --market US
python main.py portfolio recent --limit 20
```

These are bookkeeping commands, not order execution. Prices are never fetched.
Currency defaults to USD; markets are not inferred. Running `python main.py` without
arguments still starts the normal MAGI analysis workflow.

See the [Portfolio CLI guide](docs/portfolio-cli.md) for complete options, examples,
analysis-run linking, and error behavior.

## Read-only market data (Phase 6A)

An opt-in provider-independent market layer supports Toss US/Korean quotes, daily
and one-minute candles, and directional USD/KRW FX. Set `TOSS_CLIENT_ID` and
`TOSS_CLIENT_SECRET` in your environment or local `.env`; never commit their values.

```sh
python main.py market quote NVDA --market US
python main.py market quote 005930 --market KR
python main.py market fx USD KRW
python main.py market history NVDA --market US --days 30
python main.py market candles NVDA --market US --interval 1m
```

These commands make live read-only requests. Normal analysis and portfolio commands
retain their existing behavior. No accounts or orders are accessed. See
[market data documentation](docs/market-data.md) for schemas, cache/staleness,
optional portfolio valuation, offline tests, and first-request review notes.

## Broker read-only visibility (Phase 6B)

An opt-in broker layer separates current external holdings from MAGI's historical
transaction ledger. It reuses Toss OAuth and never imports transactions or trades.

```sh
python main.py broker accounts
python main.py broker holdings --account 1
python main.py broker summary --account 1
python main.py broker reconcile --account 1
python main.py broker balances
```

Accounts use masked numbered selectors. Reconciliation reads the existing ledger
without modifying it. USD/KRW values remain separate; Toss cash balances are explicitly
unsupported because no suitable non-order endpoint is exposed. These commands are
manual live operations except the unsupported Toss balances command and help.
See [Broker read-only guide](docs/broker-read-only.md) for privacy, schema, reconciliation,
shared-token usage, limitations, and first-live-test preparation.

## Account-aware ledger (Phase 6C)

Portfolio transactions now include broker/account identity. Schema v1 migrates
transactionally to v2, retaining existing trades as MANUAL/DEFAULT. Positions and
reconciliation stay account-scoped; optional unified Python views preserve account
breakdowns and native currencies. `portfolio accounts`, `--broker`, `--account`, and
`list --all-accounts` expose local account views. No automatic import or trading is
implemented. See the [account-aware portfolio guide](docs/account-aware-portfolio.md).

## Explicit opening balances (Phase 6D)

Schema v3 adds append-only OPENING_BALANCE events for positions whose earlier trades
are unknown. `broker import-preview` is read-only; `broker import-position SYMBOL
--account SAFE_REF --confirm` explicitly initializes one local account-scoped position.
Original purchase dates, fees and historical BUYs are never invented. Existing broker
requests remain read-only; no order endpoint or automatic import is added.
See the [opening-balance guide](docs/opening-balances.md) for migration, accounting,
confirmation, duplicate protection and the manual-history alternative.

### Live portfolio valuation

Phase 6E adds `python main.py portfolio live` with English/Korean display, native
currency totals, optional USD/KRW conversion, and read-only broker reconciliation.
See [live portfolio documentation](docs/live-portfolio.md) for syntax, history
semantics, stale-data behavior, and presentation-only dust thresholds.

### Research evidence foundation

Phase 7A adds offline, immutable research sources, evidence, claims, citation
validation, bounded views, and versioned JSON snapshots. It does not fetch research
or change agent decisions. See [research foundation](docs/research-foundation.md).

### Official research adapters

Phase 7B adds explicit SEC EDGAR and OpenDART research commands, official filing
citations and Decimal-safe financial evidence. Agents are not connected to these
sources yet. See [official adapter documentation](docs/official-research-adapters.md)
for configuration, commands, retry behavior, offline fixtures and known limits.

### Deterministic company snapshots

Phase 7C selects citation-backed financial metrics from official EvidencePacks,
calculates comparable growth and margins with Decimal, and renders compact Korean
or English summaries. It adds no LLM interpretation or persistence. See
[company research snapshots](docs/company-research-snapshot.md) for policies and CLI examples.

### Offline news evidence foundation

Phase 7D adds provider-neutral news articles, attributed evidence, events, catalysts,
duplicate/event groups, bounded views and Korean/English presentation. It includes
synthetic offline fixtures only; no live news provider, agent connection or news
persistence is enabled. See [news evidence foundation](docs/news-evidence-foundation.md).

### Marketaux live-news adapter (Phase 7D.1)

An explicitly invoked Marketaux adapter resolves provider entities and builds
bounded, attributed news evidence packs. It has no agent or portfolio integration.
Implementation tests use synthetic offline fixtures; live verification requires
separate authorization. See the [Marketaux news guide](docs/marketaux-news.md)
for commands, token handling, US/Korean mapping, limits, and licensing review.
