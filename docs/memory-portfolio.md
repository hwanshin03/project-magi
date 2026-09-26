# Local memory and portfolio ledger v1

MAGI stores completed analysis snapshots and manually recorded trades locally in
`data/magi.db`, resolved relative to the repository rather than the working directory.
The directory is created automatically. New directories use mode 0700 and new databases
use mode 0600. SQLite databases and their journal/WAL sidecars are ignored by Git.
No new third-party dependencies are needed.

## SQLite schema

The **complete executable schema**, including every column, constraint, index, and
append-only trigger, is [`magi/schema.sql`](../magi/schema.sql). `PRAGMA user_version`
is 1. Unknown schema versions are rejected, not reset or automatically rewritten.

| Table | Columns |
| --- | --- |
| `analysis_runs` | `run_id` TEXT primary key; `timestamp` TEXT; `question` TEXT; `final_action` TEXT; `voting_version` TEXT (`2of3-v1`); `voting_json` TEXT; `explanation` nullable TEXT |
| `analysis_agents` | `run_id` TEXT foreign key; `agent` TEXT; `provider` TEXT; `model` TEXT; nullable `position` TEXT; `confidence` REAL; `reasoning` TEXT; `key_risks_json` TEXT; `evidence_gaps_json` TEXT; nullable `changed_position` INTEGER; `availability` TEXT; nullable `error` TEXT; `attempts` INTEGER; nullable `http_status` INTEGER. Composite primary key: `(run_id, agent)`. |
| `portfolio_transactions` | `sequence` INTEGER autoincrement primary key; `transaction_id` unique TEXT; `timestamp` TEXT; `recorded_at` TEXT; `symbol` TEXT; nullable `asset_name` TEXT; `market` TEXT; `currency` TEXT; `action` TEXT; `quantity` TEXT; `price_per_share` TEXT; `fees` TEXT; `notes` TEXT; nullable `linked_analysis_run_id` TEXT foreign key; nullable `external_reference` TEXT |

`voting_json` contains `final_action`, `eligible_voters`, `excluded_agents` (names and
reasons), three vote counts, `required_votes`, `winning_agents`, and `consensus_reached`.
The associated `analysis_agents` rows contain the full immutable agent snapshots.
Confidence is numeric metadata, not money and not a voting weight.

Indexes cover recent analyses, final action, agent/position, execution-ordered
instrument histories, and linked analyses. UPDATE and DELETE triggers reject changes
to all three tables. These prevent accidental mutation; the database is not encrypted
or tamper-proof against someone who can directly replace files or drop its triggers.

## Analysis memory

Every completed CLI analysis attempts to append a UUID run, a UTC completion timestamp,
the original question, all final agent results, the deterministic vote, and the LLM
explanation. If explanation generation fails, the run is still saved with a null
explanation. All-unavailable runs are saved with their insufficient-participation vote.
A run and its three agent rows are written atomically. Existing runs are never updated.
On reads, agent validation and the versioned v1 vote invariants are rechecked; malformed
or inconsistent records cause a sanitized storage error rather than entering prompts.

```python
from magi.memory import AnalysisMemory

memory = AnalysisMemory()
run = memory.get_analysis(run_id)
recent = memory.recent(limit=10)
matching = memory.search(keyword="valuation", symbol="NVDA")
buys = memory.search(final_action="BUY_APPROVED")
melchior_buys = memory.search(agent="Melchior", position="BUY")
```

Explicit keyword/symbol searches are literal case-insensitive question substring
matches, with optional combined filters. No security-master ticker inference exists.
Automatic relevance retrieval uses question-token overlap, ignores common generic
words, prefers newest matching runs, and returns at most three. Empty/no-match searches
provide no history. This is a transparent lexical baseline, not semantic retrieval.

Historical snapshots are JSON reference data in the user-content channel, clearly
labeled `UNTRUSTED_HISTORICAL_CONTEXT`. Persona, response contract, and instructions not
to follow historical text are placed in the provider's higher-priority instruction
field (OpenAI `instructions`, Gemini `system_instruction`, Anthropic `system`). The
current question remains separate and unchanged. Every agent makes a fresh request in
all three debate rounds. Stored decisions are never merged into voting input; only
current final `AgentResult` objects vote. History can inform model reasoning, but is
not an authority. Instruction separation mitigates prompt injection; it does not prove
that a model can never be influenced by malicious prose.

## Portfolio service

Portfolio v1 is a Python service, not a trade-entry UI. Recording a transaction only
records an externally executed trade; it never executes anything or contacts a broker.

```python
from magi.portfolio import Portfolio

portfolio = Portfolio()
trade = portfolio.record_transaction(
    "NVDA", "BUY", "10", "180", currency="USD", market="US",
    executed_at="2026-09-20", fees="1.00", notes="Long-term entry rationale",
    linked_analysis_run_id=run_id,  # optional; omit for an independent trade
)
portfolio.record_transaction(
    "NVDA", "SELL", "5", "210", currency="USD", market="US",
    executed_at="2026-09-25", fees="1.00",
)
position = portfolio.get_position("NVDA", current_price="202.50")
transactions = portfolio.get_transactions("NVDA")
all_transactions = portfolio.get_transactions()
open_positions = portfolio.get_open_positions()
closed_positions = portfolio.get_closed_positions()
first_buy = portfolio.get_first_purchase_date("NVDA")
last_trade = portfolio.get_latest_transaction("NVDA")
history = portfolio.get_position_history("NVDA")
entry_analysis = portfolio.get_linked_analysis(trade.transaction_id)

# Prices are caller-supplied and keyed by (symbol, currency, market).
marked = portfolio.get_open_positions({("NVDA", "USD", "US"): "202.50"})
losers = [p for p in marked if p.unrealized_pnl is not None and p.unrealized_pnl < 0]
```

Each transaction has a new UUID and separate execution and recording timestamps.
Date-only input means midnight UTC; full timestamps must include a timezone and are
normalized to UTC. Ordering is execution timestamp, then append sequence for ties.
Symbols are stripped and uppercased; aliases and exchange-specific normalization are
not inferred. Currency is required. Holdings are partitioned by `(symbol, currency,
market)`; ambiguous symbol-only requests fail and require explicit filters. There is
no FX conversion or cross-currency aggregation. Use an empty market filter (`market=""`)
for transactions without a specified market.

Quantities must be positive; prices and fees nonnegative and finite. Only BUY and SELL
are supported. `Decimal` values are stored as TEXT and never converted to SQLite REAL.
Pass strings or `Decimal` for exact input. Float inputs are accepted through `str(value)`
for convenience, but cannot recover precision already lost before calling the API.
Inputs permit up to 28 significant digits and 18 decimal places; accounting uses a
80-digit local Decimal context. Returns are ratios (`0.1` means 10%), not percentages.

Writes use `BEGIN IMMEDIATE` to serialize validation and insertion. The entire
instrument history, including a candidate backdated trade, must replay without an
oversell. Short positions and corrections/reversals are not supported in v1. A rejected
trade inserts nothing. Equal-time trades use insertion order. Explicit corrections,
import deduplication, and multi-account rules require a later design; an external
reference is retained but not currently treated as a deduplication key.

## Accounting rules

**Weighted-average book cost is internal portfolio analytics, NOT tax-lot accounting
or tax reporting.** The raw ledger remains available for future FIFO or specific-lot
implementations.

Let `Q` be current shares and `B` the remaining book cost:

- BUY: add `quantity × price + fees` to `B`; add quantity to `Q`.
- Average book cost: `B / Q` when `Q > 0`, otherwise zero.
- SELL cost released: `B × sold_quantity / Q`, computed before selling. A full close
  releases all remaining `B`, avoiding a residual from repeating decimal division.
- Net sale proceeds: `sold_quantity × sale_price − sell_fees`.
- Realized P/L: cumulative net sale proceeds minus cumulative released book cost.
- Realized return: realized P/L divided by cumulative released book cost.
- Supplied-price market value: remaining quantity times the supplied price.
- Unrealized P/L: market value minus remaining book cost.
- Unrealized return: unrealized P/L divided by remaining book cost.

Zero-denominator returns are `None`. Without a supplied price, current price, market
value, unrealized P/L, and unrealized return are all `None`. No price is inferred from
past trades. A supplied price is assumed to be in the position's currency.

Total capital invested is cumulative BUY outlay including fees, not net cash invested.
Total sale proceeds are cumulative proceeds after sell fees. First purchase date and
realized performance cover the full ledger, including closed and reopened positions.
`get_position_history()` returns the state after each trade; it does not pretend that
historical market prices are available.

## Failure and security behavior

A missing database/directory is created. Locked, corrupt, inaccessible, failed-read,
failed-write, unsupported-version, or invalid-record cases yield generic `StorageError`
messages, never raw driver errors, SQL, filesystem paths, or environment values.
The SQLite lock timeout is one second by default. Retrieval failure disables historical
context for that analysis; persistence is still attempted independently at the end.
Persistence failure prints a warning, with the deterministic action retained and
reprinted. Failed explanation requests do not erase votes or prevent saving.

Only explicit business fields are persisted. API clients, credential configuration,
environment dictionaries, and `.env` contents are never serialized. Before any insert,
all text is checked for configured secret environment values, all current `.env` values,
and common API-key/bearer-token patterns. Sensitive text is rejected before writing,
rather than silently altering the historical snapshot. The same check protects reads.
No raw rejected text is logged. Unknown arbitrary secrets cannot be recognized with
certainty; do not treat this local unencrypted database as a credential vault.

## Offline verification

Run the full offline suite, excluding the three pre-existing live connectivity scripts:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_[dmprv]*.py' -v
```

CLI tests use temporary databases; no test writes to `data/magi.db`. Provider-facing
tests block network access and use fake responses. Never run the older live scripts
(`test_openai.py`, `test_gemini.py`, `test_claude.py`) as part of offline verification.

Before Phase 6, review backup/encryption policy, schema migration/version dispatch,
lexical relevance quality, timestamp/ticker/account conventions, correction records,
import deduplication, and future currency-specific rounding. Future market prices,
dividends, splits, account IDs, outcomes, and scoring should be separate versioned
extensions; no such features or automatic trading are implemented here.
