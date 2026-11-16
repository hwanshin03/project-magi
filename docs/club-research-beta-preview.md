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

MOIRAI is the public platform, MAGI the internal three-agent engine, and FIC the operator. The typography-only MOIRAI wordmark is temporary; navigation reserves an operator slot for the future official FIC asset. No FIC logo is invented.

The redesigned dashboard, searchable library, company table and report use navy/gold surfaces, local Geist Sans, Hangul-range Pretendard, and functional Geist Mono. Financial values use tabular numerals; wide tables scroll inside keyboard-focusable regions on smaller screens. The GET search only filters the two prepared fixtures.

CSP retains default-src 'none', same-origin styles, base-uri 'none' and frame-ancestors 'none'. It explicitly permits font-src 'self' for vendored fonts and form-action 'self' for the read-only GET search. Scripts and outbound connections remain denied. Fonts have a fixed filename allowlist; no arbitrary static path is served. Font licenses and content hashes are bundled in static/fonts. No external fonts are fetched by the application.
