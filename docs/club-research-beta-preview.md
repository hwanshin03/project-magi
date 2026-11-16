# Club Research Beta 0.1 — local synthetic preview

This is a usable report-reading slice for Finance & Investment Club at OCC,
not an externally deployable member portal. All reports are fictional. The
owner's private report and unfinished financial-selection changes are excluded.

## Start

From the isolated worktree created for this milestone:

```sh
cd /private/tmp/project-magi-beta-0.1
env -i PATH=/usr/bin:/bin /Users/9oo.__.9oo/Desktop/project-magi/.venv/bin/python -B -m beta_preview
```

Open http://127.0.0.1:8765. Stop with Ctrl-C. The worktree is in temporary storage;
keep this branch/worktree until separately authorized to relocate or commit it.
The existing virtual environment supplies the already pinned MAGI dependencies.
No new dependencies, migrations, installation or build step are required.

Dashboard → Research library → report detail exposes three-agent availability,
decisions, saved deterministic consensus, historical evidence, risks, missing
data and selected citations. Tickers links to the prepared fictional DEMO reports.
An unknown ticker shows an empty state; invalid reports show a safe unavailable
state. New research requests are disabled and no execution endpoint exists.

## Existing engine integration

`beta_preview/fixtures/*.json` are prepared full `AnalysisResult` artifacts.
The existing `loads` validates their graph, identities and semantics; the
existing `render_report` supplies an expandable escaped plain-text report.
The web view resolves selected units/citations through the existing reference
index. It does not select evidence, recalculate metrics or implement voting.

The offline fixture generator uses the existing orchestrator with supplied
synthetic evidence and fixed fake-agent results. It constructs no live agents,
provider clients or broker adapters. It is never called by the web server.
The two fixtures demonstrate HOLD and INSUFFICIENT_PARTICIPATION; the latter
has one available abstaining agent and two unavailable agents, hence no votes.
They are not reproductions or claims about the owner's historical NVDA run.

To regenerate fixtures intentionally, run the same isolated interpreter with
`-B -m beta_preview.synthetic`. Normal startup only reads them.

## Local boundary and content safety

- A visible development identity fixture replaces login only for this local demo.
  It is not authentication. Anyone on the local machine can view these fictional
  reports. There is no real authentication to claim or test as implemented.
- The launcher clears inherited environment configuration before MAGI imports.
  Its process audit hook denies outbound socket connections, DNS, SQLite access,
  credential/private file reads, subprocesses, and broker/portfolio imports.
  This is a defensive application guard, not an OS isolation boundary.
- The listener is fixed to IPv4 loopback, performs no reverse DNS, checks Host,
  and imposes a connection timeout. Do not expose it through a proxy or tunnel.
- Only two fixed fixture filenames are read; there are no uploads or arbitrary
  paths. Symlink/oversize/invalid artifacts and non-demo identities fail closed.
- All source text, model text and Markdown are HTML-escaped. No Markdown-to-HTML
  engine or JavaScript is used. Source links require HTTPS, without credentials
  or control characters. CSP blocks scripts, frames, connections and forms;
  responses disable caching and referrers. Request content is not logged.
- The non-resolving `example.invalid` citation is explicitly fictional. No source
  body, real credentials or owner report is included. No current price or live
  valuation coverage is claimed.

## Verification

Run the scoped offline tests under the same process guard:

```sh
env -i PATH=/usr/bin:/bin /Users/9oo.__.9oo/Desktop/project-magi/.venv/bin/python -B -c 'from beta_preview.safety import install; install(); import unittest; s=unittest.defaultTestLoader.discover("tests", pattern="test_beta_preview.py"); r=unittest.TextTestRunner().run(s); raise SystemExit(not r.wasSuccessful())'
```

The tests validate real saved-artifact round trips, consensus/failure rendering,
provenance, empty/error paths, hostile text/URLs, HTTP headers, fixed Host,
disabled execution, and guard rejection. Guard probes invoke the guard directly;
they do not attempt actual network or database operations. Browser verification
uses only the loopback server. No live-source verification is part of this slice.

## Before hosted invite-only access

The smallest next milestone is an identity-provider-backed, read-only hosted
slice with real invites and revocation, secure sessions, restricted administration,
and server-enforced private report authorization on every route. Verify denied
access for anonymous/revoked users, report isolation and session expiry before
exposing any real report. Replace the development HTTP server with an appropriate
authenticated deployment stack after choosing the hosting/identity provider.

Paid research remains disabled until authorization, per-user quotas, global
call/cost limits, concurrency controls, idempotency, bounded retries, job timeouts
and an administrator kill switch are implemented and independently tested.
The unfinished financial-selection saved-request compatibility work remains
separate and unresolved; this slice uses only the committed engine baseline.

## MOIRAI visual redesign

MOIRAI is the public platform, MAGI the internal three-agent engine, and FIC the operator. The original Beta 0.1 typography-only MOIRAI wordmark was temporary; navigation reserves an operator slot for the future official FIC asset. No FIC logo is invented.

The redesigned dashboard, searchable library, company table and report use navy/gold surfaces, local Geist Sans, Hangul-range Pretendard, and functional Geist Mono. Financial values use tabular numerals; wide tables scroll inside keyboard-focusable regions on smaller screens. The GET search only filters the two prepared fixtures.

CSP retains default-src 'none', same-origin styles, base-uri 'none' and frame-ancestors 'none'. It explicitly permits font-src 'self' for vendored fonts and form-action 'self' for the read-only GET search. Scripts and outbound connections remain denied. Fonts have a fixed filename allowlist; no arbitrary static path is served. Font licenses and content hashes are bundled in static/fonts. No external fonts are fetched by the application.

## Approved Figma implementation — UI 0.2

Isolated worktree: `/private/tmp/project-moirai-ui-0.2`.
Branch: `codex/moirai-figma-ui-0.2`.
Recoverable Beta 0.1 checkpoint: `6a5e1d76b72c3eece870cdb45ec83d415c2eabe5`,
pushed to `origin/codex/club-research-beta-0.1`. UI changes remain uncommitted and unpushed.

Start from this worktree using the same isolated Python command above, then visit
http://127.0.0.1:8765. The original preview must not occupy the same port.

The approved school Figma file `cz6raRFJCB65gJVNA2kPR4`, page
`03 · Refinement Review`, is the visual reference. Dashboard `2013:4`,
company `2013:78`, library `2013:142`, reports `2013:189` / `2013:287`,
feedback `2013:386`, their 768/390px counterparts, and specification `2017:790`
map to responsive HTML/CSS. Desktop uses a 232px sidebar and 40px gutters;
tablet uses 32px gutters, mobile 20px, with stacked cards and named scrolling tables.

Routes: `/`, `/tickers`, `/companies/DEMO`, `/reports`,
`/reports/demo-hold`, `/reports/demo-partial`, `/preview/states`.
The final route shows explicitly labeled static feedback specimens, not an active
loading operation. Unknown paths return 404; invalid saved reports return 503.
Search remains read-only GET, case-insensitive and trimmed; company-name and empty
queries are supported, query text is escaped and retained, and empty results offer reset.

Intentional design differences: preserve validated Example Compute / DEMO identity,
the actual 120000000 USD revenue observation, exact source/reference IDs and synthetic
as-of dates rather than Figma's Demo A/B, four invented metrics and placeholder dates.
Agent risks and evidence gaps remain expanded inside each agent panel. Reports are
longer to retain audit detail. A visible search button and label support native forms.
The partial outcome has a readable label plus its exact machine status. No vote,
model or research-selection semantics changed. Loading is not simulated dynamically.

Brand uses the unchanged approved SVG and bundled Cinzel (SIL OFL 1.1). Existing
Geist, Geist Mono, Pretendard and notices are unchanged. No font CDN or dependency
was added. CSP adds only `img-src 'self'` for the fixed logo endpoint; scripts and
outbound connections remain denied. Exact static filenames are allowlisted.

Validation: 25 pytest tests and 26 subtests; 25 unittest tests. Existing dependency
emits one Pydantic deprecation warning. Browser checks cover 8 routes/states at
1440, 768 and 390px, all without page overflow; search, native keyboard disclosure,
focus visibility, four local fonts and 200% text enlargement were checked. Browser
requests were restricted to loopback; observed external requests: zero. Offline
checks run with the existing network/database/private-file/broker guard. No real
research, model execution, database access or trading was performed.

This is not full accessibility certification: screen-reader announcement behavior
and a broader translated-content review remain manual follow-up work. Authentication
is absent. Neither public nor invite-only hosting is approved. Next milestone:
independent UI/security review, then a separately authorized UI commit.
