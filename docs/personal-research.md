# Personal Research v1.0

The existing `analyze` command now supports a readable report and explicit local
Markdown export. It reuses collection → bounded evidence → three agents →
deterministic voting. There is no new research engine or database write.

## NVDA owner pilot

From the repository root, inspect the offline plan first:

```sh
.venv/bin/python main.py analyze NVDA --market US \
  --question "Assess NVIDIA fundamentals, developments, downside risks and evidence gaps. Cite supplied references; abstain when evidence is insufficient." \
  --family sec --family snapshot --family news --family company \
  --required sec --currency USD --limit 3 --format markdown
```

After reviewing API usage, append `--execute --output nvda-research.md` to that
same command. This is an owner-operated live pilot, **not performed during this
milestone**. The target is resolved through the existing SEC and Marketaux
adapters; no CIK or provider symbol is guessed. Use a new output filename for
subsequent runs. Parent directories must exist. Export never overwrites a file
or symlink and creates the new file with owner-only permissions. A late write
failure returns an error; inspect/remove any partial report before retrying.
Do not rerun automatically: model calls may already have consumed quota.

Default mode remains an offline JSON plan. `--format audit` retains the compact
JSON result. Markdown is printed to stdout; progress and sanitized failures go
to stderr. `--output` requires `--execute --format markdown` and a `.md` suffix.
Exit 0 means a report/plan was produced, **not** that all providers or agents
succeeded. Exit 1 is an application/collection/export failure; argparse errors
use exit 2. A missing or partial required SEC channel stops before agent calls.
Optional news/company failures stay explicit in the report.

## Configuration and request exposure

Existing configuration names (do not put values into commands or reports):

- `SEC_USER_AGENT`: identifying SEC contact string.
- `MARKETAUX_API_TOKEN`: entity search and news access.
- `OPENAI_API_KEY`: Melchior, `gpt-5.5`.
- `GEMINI_API_KEY`: Balthasar, `gemini-2.5-flash`.
- `ANTHROPIC_API_KEY`: Casper, `claude-sonnet-4-6`, 3,072 output tokens.

The existing adapters load local configuration during an owner live run.
This development task does not read or modify `.env`. No Toss credentials are
needed. All three model clients must be constructible; a missing key can stop
execution before collection instead of yielding a partially populated report.

For the exact NVDA command above, healthy fresh-process requests are:

| Provider | Endpoints | Normal requests | Existing retry bounds |
|---|---|---:|---|
| SEC | `https://www.sec.gov/files/company_tickers_exchange.json`; `https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json`; `https://data.sec.gov/submissions/CIK0001045810.json` | 3 (mapping calls reuse RAM cache) | Up to 3 attempts per fetch; cache expiry/failure can increase mapping fetches |
| Marketaux | `https://api.marketaux.com/v1/entity/search`; `https://api.marketaux.com/v1/news/all` | 2; one news page, requested limit 3 | Up to 3 attempts per endpoint |
| NVIDIA | `https://nvidianews.nvidia.com/releases.xml` | 1 | Up to 2 attempts |
| OpenAI | `https://api.openai.com/v1/responses` | 1 | Up to 3 attempts |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent` | 1 | Up to 3 outer attempts; SDK retries disabled |
| Anthropic | `https://api.anthropic.com/v1/messages` | 1 | One attempt; read timeout 60 seconds |

Thus expect six research requests plus three model requests when healthy. These
are code-derived expectations, not a live measurement or total-request cap.
Endpoint addresses assume the SDK's default base URLs; owner SDK endpoint
configuration can override them. No IR HTML endpoints, regulatory endpoints,
article body fetches, quote endpoints, debates or consensus-model calls are part
of this command. Financial snapshot construction is local.

The model requests can incur paid input/output token charges; Marketaux consumes
subscription/quota. Retries can increase cost. This CLI has no dollar spending
cap, no exact token-fit guarantee, and no estimated price quote. Casper's output
cap does not cap input cost; the other agents retain their existing configurations.
Review your provider account limits before running the command once.

## Reading the report

The report shows source-derived names, original ticker/market, research as-of,
selected financial values/periods, bounded news excerpts, temporal fitness,
provenance references/fingerprints, all three agent rationales, risks, evidence
gaps, actual availability and deterministic final action. Source text is escaped
for Markdown/HTML and terminal controls are removed. Only normalized selected
excerpts are exported, never raw provider responses or full articles.

Source-derived facts and agent interpretations are separated. PRIMARY is a
provenance label, not a truth score. Publication dates remain distinct from
retrieval timestamps; SEC/DART date-only conventions are not presented as precise
publication times. Missing data is not filled in. Agent confidence is uncalibrated.

Important limitations:

- No trusted quote is integrated. Prices, live valuation, quote age and trading
  sessions are unavailable; historical/source/model price mentions are not live quotes.
- SEC resolves exchange metadata, but the analysis artifact does not retain it.
  Exchange is therefore explicitly unverified in the report.
- The selected financial projection is bounded, not the complete filing history.
  A missing financial snapshot or missing metric remains a coverage gap.
- Strict response parsing does not verify factual accuracy, cited-reference
  correctness or certainty in model prose. Review claims against the provenance
  appendix. No JSON repair or inferred citations are added.
- Unavailable/stale agents do not vote; ABSTAIN is explicit. Two eligible agreeing
  votes are required. A report may end in insufficient participation or no consensus.
- No additional bullish/bearish thesis is invented: arguments remain attributed
  in the original agent rationales. The report is research, not execution or
  personalized financial advice.

## Korean coverage

`000660 --market KR` preserves SK Hynix's stock-code form. Existing OpenDART
resolution can map stock codes through its official corporation-code archive;
financial collection needs `OPENDART_API_KEY`, an explicit `--year`, and a supported
annual/q1/half/q3 period. KRW values, receipt IDs and KST date-only publication
conventions already exist. No SK Hynix live mapping or filing was verified here.
Official-company collection currently supports NVIDIA and Samsung, not SK Hynix.
Korean Marketaux resolution is provider-dependent. Exchange and trusted local
price timestamps remain unverified. Korean end-to-end readiness is partial.

Next research work: retain authoritative issuer/exchange identity in the artifact,
add a separately reviewed broker-independent market-data bridge, and validate
agent citations. Virtual accounting and recovery development remain paused.
