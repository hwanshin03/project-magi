# Phase 7B: SEC EDGAR and OpenDART adapters

Official research is opt-in through `python main.py research ...`. Importing any
module performs no network calls. Normal MAGI analysis, agents, prompts, debate,
voting, explanation, portfolio, broker and market-data paths are unchanged.
Research adapters do not access the production database and do not call an LLM.
No investment recommendations are generated. No research persistence is added.

## Configuration

Set `SEC_USER_AGENT` to your real application/contact identity in local environment
configuration. SEC has no API key. The adapter refuses missing/empty identification
rather than inventing a generic identity. User-Agent is identification metadata,
not a secret credential, and is sent only to SEC; it is not included in evidence.
The existing secret detector exempts this one public `.env` field while continuing
to protect API keys and other configuration values.

Set `OPENDART_API_KEY` through local environment configuration. The `.env.example`
contains empty placeholders only. Explicit provider construction loads `.env`
without overriding process variables. No command edits `.env`.

OpenDART requires `crtfc_key` on official requests. The key is inserted only at the
transport boundary. It is never placed in source/citation URLs or error messages.
HTTPX/HTTPCore log filters redact the configured key before handlers receive their
records, and public errors expose a controlled category, never raw exceptions or
provider messages. Do not enable external packet/request dump tooling around live
runs. Raw request/response objects are never returned as research results.

## Architecture and contract

`ResearchProvider` describes resolve_issuer, get_company_profile, list_filings,
get_financial_facts and build_evidence_pack. SEC/DART accept their own query options
rather than exposing identical raw capabilities. Public methods return
`ResearchResult(data=...)` or `ResearchResult(error=ErrorCode...)`; `require()` raises
only a sanitized ResearchError. Missing configuration at construction also raises
ResearchError(CONFIGURATION). Categories distinguish NOT_FOUND, AMBIGUOUS, NO_DATA,
AUTHENTICATION, INVALID_REQUEST, INVALID_RESPONSE, RATE_LIMITED and UNAVAILABLE.

`Issuer` retains provider, provider_issuer_id, company_name, ticker, market,
exchange/modification date where supplied, and retrieval time. Identity is scoped:
`SEC:<ten-digit CIK>` and `DART:<eight-digit corp_code>` are not interchangeable.
Profiles expose an allowlisted immutable field map and their own retrieval time.
FinancialFacts contains issuer identity, normalized sources/evidence and retrieval
time; pack builders create validated Phase 7A EvidencePacks without claims.

EvidenceItem gains optional deeply immutable `metadata` to preserve structured
fact context. Existing Phase 7A JSON without that field loads as empty metadata.
Research envelope version stays 1; this is an additive field. No database migration.

## HTTP behavior

Fixed GET allowlists:

- SEC `www.sec.gov/files/company_tickers_exchange.json`
- SEC `data.sec.gov/submissions/CIK##########.json`
- SEC `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- OpenDART `opendart.fss.or.kr/api/corpCode.xml`
- OpenDART `/api/company.json`, `/api/list.json`, `/api/fnlttSinglAcntAll.json`

No arbitrary URL fetching, redirects, account/trading endpoints, parallel request
fan-out or search engines. Citation document URLs are constructed but not fetched.

Transport uses explicit 15-second HTTP timeouts and at most three attempts.
Only HTTP 429/500/502/503/504 and HTTP transport failures retry. Backoff is 0.25s,
then 0.5s; pacing enforces at least 0.25s between SEC attempts and 0.5s for DART.
Numeric Retry-After up to ten seconds is honored; longer delays return an error
rather than tying up the caller. No concurrency is introduced. Limits apply per
transport instance; callers must not create many concurrent instances.

A 16-entry, 64 MiB aggregate, 300-second in-memory response cache retains original retrieval times.
It is bounded, configurable for tests, and cleared on close. DART API error responses
are not cached as successes. No-data, auth and parameter statuses do not retry.
DART status 020 maps to RATE_LIMITED; 800/900 map to UNAVAILABLE, without speculative
API-level retry. Unexpected payloads produce INVALID_RESPONSE. Responses have a
32 MiB body limit; corporation ZIP extraction separately limits expansion to 64 MiB,
requires one CORPCODE.xml entry and rejects DTD/entity declarations. No ZIP content
is extracted to disk.

## SEC normalization

Ticker input is trimmed/uppercased, with official ticker mapping to CIK; CIK input
is validated and padded to ten digits and resolved through submissions. Company
name and exchange are retained where available. Multiple ticker matches return
AMBIGUOUS; no issuer is guessed. A direct CIK with multiple tickers has no chosen
ticker, rather than silently picking a share class.

Recent submissions preserve accession, filing date, report date, form, primary
document, issuer/CIK, acceptance time and official archive URL. All forms can be
retained, including 10-K, 10-Q, 8-K and amendments/foreign-issuer forms. Forms ending
`/A` are flagged; different accessions remain separate sources. Historical submission
archive pages are not fetched in v1. list_filings is bounded to 100 by default,
with up to 1,000 recent entries available programmatically.

Company facts parse JSON floating-point literals directly into Decimal. Each
observation preserves taxonomy/concept, label/description, unit, periods, accession,
form, fiscal year/period, filed date and frame. Duration facts use start/end; instant
facts use end plus as_of. Missing optional values remain missing. Identity includes
context metadata so units, periods, frames and filings cannot collapse into one
fact. Conservative concept mappings distinguish revenue, profit, balance sheet,
and operating cash flow; unfamiliar concepts use OTHER. No market price or
investment interpretation is inferred from filings.

Known recent filing metadata provides primary-document citations. Older company
facts outside recent submissions cite an official accession index instead. Locators
carry real taxonomy/concept, with no invented page or section. Filing acceptance
instant is preferred; where only a filing date exists, midnight UTC is an explicit
precision convention recorded in metadata, not a claimed publication clock time.

Company-facts output has a default 25,000-observation bound, maximum 50,000. Exceeding
it returns INVALID_REQUEST rather than silently truncating a historical pack.
Use repeated `--concept` filters to narrow large issuers. SEC entity-wide company
facts are not a complete custom/dimensional-XBRL filing parser.

## OpenDART normalization

Corporation codes are resolved from official zipped XML. Six-character stock codes
remain strings, including `005930`; eight-character corp_code identifiers remain
strings too. Exact company-name lookup is supported; ambiguity is an error. No
fuzzy matching or zero-padding a malformed stock code into a guessed issuer.

Company profiles allowlist Korean/English names, stock name/code, CEO, corporation
classification, homepage and IR URL. Registration numbers, addresses and unrelated
corporate identifiers are not copied into evidence. Bare homepage strings without
an HTTP(S) scheme are omitted rather than inventing a URL. Credential-bearing URLs
are rejected. Stock code must agree with the resolved issuer.

Disclosure lists preserve receipt number/date, filing corporation name, report
name, submitter and remarks. `last_reprt_at=N` keeps corrections in the result;
receipt IDs remain distinct. A correction marker reflects the official report name,
not an inferred linkage to an earlier filing. Listing is one explicit page of at
most 100 entries, `--page` 1–10. Default date range follows provider defaults; use
`--start`/`--end` (YYYYMMDD) for reproducibility. Complete historical pagination is
not automatic. Evidence snapshots themselves carry stable receipt IDs, never
"latest filing" identifiers.

Full financial statements use `fnlttSinglAcntAll.json`, with explicit business year,
report code and CFS (default) or OFS. Report mapping:

| CLI | Official code | Meaning |
|---|---|---|
| q1 | 11013 | Q1 |
| half | 11012 | Half-year |
| q3 | 11014 | Q3 |
| annual | 11011 | Annual |

Evidence preserves statement division/type, account ID/name/detail, business year,
report code/label, receipt, currency and each current/prior amount field separately.
Comma-grouped numbers and accounting parentheses parse to Decimal; blank/dash/missing
amounts are None, never zero. Missing currency remains missing. Only actual supplied
period dates populate date fields. Period labels/report codes do not justify guessed
calendar dates, especially for non-calendar fiscal years. Receipt dates never become
accounting period ends. Receipt publication date uses midnight KST with an explicit
date-precision convention; receipt-prefix dates are used only when the financial
endpoint supplies no separate receipt date. Amount fields include comparative and
cumulative fields when supplied; they are never added together or annualized.

## Evidence, authority and trust

SEC filing sources and DART filing sources use PRIMARY. Structured facts are also
PRIMARY because they are issuer-reported facts cited to a specific official filing,
not an independent forecast or third-party truth score. Filing/correction IDs
preserve history; Phase 7A deduplication only removes identical observations.
Pack coverage/warnings are computed as before and mean presence, not completeness.
Issuer identity is included in source metadata plus pack subject/ticker/market.

Official source text remains untrusted research data, including descriptions,
company/report names and locators. No text enters behavioral instructions. Future
agent grounding must use the Phase 7A context renderer and validate citations; it
is deliberately not wired into agents here. Opening balances, portfolio prices,
Toss reconciliation and deterministic voting are unaffected.

## Manual CLI (network only when explicitly invoked)

```sh
python main.py research sec issuer NVDA
python main.py research sec profile NVDA
python main.py research sec filings NVDA
python main.py research sec facts NVDA --concept NetIncomeLoss
python main.py research sec pack NVDA --concept NetIncomeLoss
python main.py research dart issuer 005930
python main.py research dart profile 005930
python main.py research dart filings 005930 --start 20250101 --end 20251231
python main.py research dart financials 005930 --year 2025 --report annual
python main.py research dart pack 005930 --year 2025 --report annual --division CFS
```

`--language ko` is default; `--language en` changes headings only. Data and enums
remain provider-native/language-neutral. Output is inspectable normalized JSON,
not a generated research report or recommendation. CLI output is not saved to disk.
Missing configuration and provider errors show only safe category identifiers.

## Verification and live-test review

All fixtures are synthetic (including issuer names and numbers); they emulate API
schemas and contain no real API keys. XML is zipped in memory in tests. Tests use
MockTransport plus network tripwires. They cover no-result/malformed data, both
provider mappings, revisions, multi-unit/period facts, Decimal precision, missing
amounts, API statuses, transport retries, cache and security boundaries.

No live SEC, OpenDART, documentation, or web request was made during implementation.
The endpoint and schema assumptions therefore still require the separately
authorized NVDA/SEC and 005930/OpenDART smoke tests. Before those tests, review real
SEC identification, DART key availability/IP policy, company-fact selection size,
and DART financial report availability. Start with issuer resolution, then a small
filing/profile query, then one constrained financial query; stop on incompatible
payloads without speculative retries. SEC recent-submission archive pagination,
DART full-history pagination, advanced dimensional facts, amendment preference and
agent grounding remain later work. No additional dependency or DB migration is needed.
