"""Read-only HTTP presentation of two validated, prepared synthetic artifacts."""
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import urlsplit, parse_qs

from magi.analysis.models import loads
from magi.analysis.presentation import render_report
from magi.research.balancing.inputs import resolve

ROOT = Path(__file__).parent
CATALOG = {'demo-partial': 'Incomplete panel', 'demo-hold': 'Complete panel'}
ROLES = {'Melchior': 'Fundamentals', 'Balthasar': 'Market & news', 'Casper': 'Downside risk'}


def text(value):
    return escape(str(value), quote=True)


def source_link(url):
    """No active Markdown or arbitrary URL schemes, credentials or control characters."""
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)
                or any(ord(c) < 33 or c == '\\' for c in url)):
            return '<span>Source link withheld: unsafe URL</span>'
    except (TypeError, ValueError):
        return '<span>Source link unavailable</span>'
    return f'<a href="{text(url)}" rel="noreferrer noopener" target="_blank">{text(url)} ↗</a>'


def load_reports():
    reports = {}
    for key in CATALOG:
        try:
            path = ROOT / 'fixtures' / (key + '.json')
            if path.is_symlink() or path.stat().st_size > 1_000_000:
                raise ValueError('Unsupported fixture')
            result = loads(path.read_text(encoding='utf-8'))
            instrument = result.request.target.instrument
            if (instrument is None or (instrument.symbol, instrument.market) != ('DEMO', 'US')
                    or any((a.provider, a.model) != ('synthetic', 'fixture-only') for a in result.agent_results)):
                raise ValueError('Only synthetic demo reports are enabled')
            # Validation and canonical Markdown stay owned by the existing engine.
            reports[key] = (result, render_report(result))
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            reports[key] = None
    return reports


def badge(label):
    return f'<span class="badge">{text(label)}</span>'


def search():
    return '<form class="search" action="/reports" method="get" role="search"><label for="ticker-search">Find prepared research</label><div><input id="ticker-search" name="ticker" type="search" placeholder="Ticker, e.g. DEMO" maxlength="20" autocomplete="off"><button type="submit">Search ↗</button></div><p>Local synthetic coverage: DEMO / US</p></form>'


def layout(title, body, active=''):
    links = ''.join(f'<a {"aria-current=page" if active == path else ""} href="{path}">{name}</a>'
                    for path, name in [('/', 'Overview'), ('/reports', 'Research library'), ('/tickers', 'Tickers')])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{text(title)} · MOIRAI · Research</title><link rel="stylesheet" href="/assets/style.css"></head>
<body><a class="skip" href="#content">Skip to research</a><div class="demo-bar"><span>SYNTHETIC LOCAL PREVIEW</span> Fictional data only · No live research</div>
<header><div class="logo-slot" aria-label="MOIRAI platform wordmark"><a class="brand" href="/"><span class="brand-name">MOIRAI <small>FINANCIAL RESEARCH</small></span></a></div>
<nav aria-label="Main navigation">{links}</nav><span class="fic-slot" aria-label="Reserved location for official FIC logo">Operated by FIC<small>Finance &amp; Investment Club at OCC</small></span><span class="member">Club member <small>Development identity fixture</small></span></header>
<main id="content">{body}</main><footer>MOIRAI · Powered by MAGI · Operated by Finance &amp; Investment Club at OCC · BETA 0.1<br>
Local preview only. Authentication is not implemented. This is not a secure deployed member portal.<br>
Synthetic investment opinions are demonstrations, not investment advice.</footer></body></html>'''


def cards(reports):
    rows = []
    for key, saved in reports.items():
        if saved is None:
            rows.append(f'<article class="card"><h3>{text(CATALOG[key])}</h3><p>Report unavailable: validation failed.</p></article>')
            continue
        result, _ = saved
        count = sum(a.availability.value == 'AVAILABLE' for a in result.agent_results)
        rows.append(f'''<article class="card"><div class="card-top">{badge('DEMO / US')}<span>FY2025 · Historical</span></div>
<h3><a href="/reports/{key}">Example Compute</a></h3><p>{text(CATALOG[key])} · fictional company</p>
<div class="outcome">{text(result.vote.final_action.value)}</div>
<p>{count} of 3 agents available · Synthetic panel</p><time datetime="{text(result.request.as_of.isoformat())}">As of {text(result.request.as_of.strftime("%d %b %Y · %H:%M UTC"))}</time><a class="text-link" href="/reports/{key}">Read report →</a></article>''')
    return '<div class="cards">' + ''.join(rows) + '</div>' if rows else '<div class="notice">No prepared reports yet. Live research requests are disabled.</div>'


def detail(key, saved):
    result, markdown = saved
    agent_cards = []
    for a in result.agent_results:
        risks = ''.join(f'<li>{text(x)}</li>' for x in a.key_risks) or '<li>No risks supplied; this does not establish safety.</li>'
        gaps = ''.join(f'<li>{text(x)}</li>' for x in a.evidence_gaps) or '<li>None supplied.</li>'
        agent_cards.append(f'''<article class="card agent agent-{a.agent.lower()}"><p class="eyebrow">{text(ROLES[a.agent])}</p><h3>{text(a.agent)}</h3>
{badge(a.availability.value)}<div class="outcome">{text(a.position.value if a.position else 'NO VOTE')}</div>
<p>{text(a.reasoning or 'No validated response. This agent did not participate in the vote.')}</p>
<p class="muted">Vote eligible: {text(a.vote_eligible)} · {text(a.provider)} / {text(a.model)}</p>
<p>Failure: {text(a.error or 'None')} · Diagnostic: {text(a.diagnostic_code or 'Not supplied')}</p>
<h4>Risks &amp; counterarguments</h4><ul>{risks}</ul><h4>Missing evidence</h4><ul>{gaps}</ul></article>''')
    observations, sources, seen = [], [], set()
    for group in result.selection.common_core:
        for ref in (*group.units, *group.citations, *group.conflicts):
            if ref.reference_id in seen:
                continue
            seen.add(ref.reference_id)
            obj = resolve(result.universe, ref)
            if ref.object_kind == 'EvidenceItem':
                observations.append(f'''<tr><td><span class="metric-name">{text(obj.statement)}</span><details class="reference"><summary>Evidence reference</summary><span class="mono">Evidence: {text(obj.evidence_id)}<br>Source: {text(obj.source_id)}</span></details></td><td class="number">{text(obj.value)} {text(obj.unit)}</td><td class="period">{text(obj.period_start)} → {text(obj.period_end)}</td></tr>''')
            if hasattr(obj, 'url'):
                sources.append(f'''<article class="evidence"><h3>{text(getattr(obj, 'title', 'Source'))}</h3>
<p>{source_link(obj.url) if obj.url else 'No source URL supplied'}</p>
<p>Published: {text(getattr(obj, 'published_at', None))} · Retrieved: {text(getattr(obj, 'retrieved_at', None))}</p>
<p>Authority: {text(getattr(obj, 'authority', 'Not supplied'))} — provenance only, not correctness.</p>
<p class="mono">Source: {text(ref.object_id)}<br>Reference: {text(ref.reference_id)}</p></article>''')
    availability = ''.join(f'<li>{text(a.family.value)}: {text(a.state.value)} {text(", ".join(a.error_codes))}</li>' for a in result.universe.availability)
    warnings = ''.join(f'<li>{text(w)}</li>' for w in result.warnings)
    body = f'''<a class="back" href="/reports">← Research library</a><p class="eyebrow">SYNTHETIC REPORT · {text(CATALOG[key])}</p>
<h1>Example Compute <span class="ticker">DEMO / US</span></h1>
<p class="lead">Fictional company · Research as-of <time datetime="{text(result.request.as_of.isoformat())}">{text(result.request.as_of.strftime("%d %b %Y · %H:%M UTC"))}</time> · Historical annual evidence</p>
<div class="notice">All financial values, agent interpretations and citations below are invented fixtures. No live models ran. Source URLs are deliberately non-resolving examples.</div>
<section class="consensus"><div><p class="eyebrow">Saved deterministic consensus</p><h2>{text(result.vote.final_action.value)}</h2></div>
<p>BUY {result.vote.buy_votes} · HOLD {result.vote.hold_votes} · SELL {result.vote.sell_votes}<br>Required agreement: {result.vote.required_votes}</p></section>
<section><h2>Three perspectives</h2><p>Agent interpretations are not verified facts. Availability and voting eligibility are distinct.</p><div class="cards">{''.join(agent_cards)}</div></section>
<section><h2>Source-derived observations <small>synthetic</small></h2><p>Bounded selected evidence only; no financial facts are inferred from agent prose.</p><div class="table-scroll" role="region" aria-label="Historical financial evidence" tabindex="0"><table><caption>Selected financial evidence · fictional FY2025 data</caption><thead><tr><th scope="col">Observation</th><th scope="col" class="number">Reported value</th><th scope="col">Historical period</th></tr></thead><tbody>{''.join(observations) or '<tr><td colspan="3">No selected financial evidence. No values are invented to fill gaps.</td></tr>'}</tbody></table></div></section>
<section class="notice"><h2>Missing data &amp; limitations</h2><p>No trusted live quote, price timestamp, freshness or current valuation is available. Historical research dates are not live-price timestamps. Agent numerical claims and citation correctness are not independently verified.</p><ul>{availability}{warnings}</ul></section>
<section><h2>Sources &amp; provenance</h2>{''.join(sources) or '<p>No selected source references.</p>'}</section>
<details><summary>Full MAGI analysis report</summary><p>Escaped plain-text Markdown; embedded HTML and scripts are never rendered.</p><pre>{text(markdown)}</pre></details>
<p class="mono">Artifact: {text(result.artifact_id)}<br>Selection: {text(result.selection.selection_id)}</p>'''
    return layout('Example Compute report', body, '/reports')


def page(path, reports):
    url = urlsplit(path)
    if url.path == '/':
        body = f'''<div class="hero"><div><p class="eyebrow">MOIRAI / RESEARCH WORKSPACE</p><h1>A clearer view.<br>A stronger research process.</h1><p class="lead">Fundamentals, market context, and downside risk.<br>Three perspectives, with the evidence in view.</p><a class="button" href="/reports">Open research library <span>↗</span></a></div><aside class="workspace"><p class="eyebrow">YOUR RESEARCH WORKSPACE</p><h2>Start with a company.</h2>{search()}</aside></div>
<div class="metrics"><div><strong>{sum(v is not None for v in reports.values()):02}</strong><span>Prepared reports<small>Synthetic teaching examples</small></span></div><div><strong>03</strong><span>Analytical perspectives<small>One transparent evidence trail</small></span></div><div><strong>00</strong><span>Live provider calls<small>Read-only local workspace</small></span></div></div>
<section><div class="section-heading"><div><p class="eyebrow">THE RESEARCH DESK</p><h2>Latest prepared reports</h2></div><a href="/reports">View library ↗</a></div>{cards(reports)}</section>
<section><div class="section-heading"><div><p class="eyebrow">POWERED BY MAGI</p><h2>Independent perspectives. Visible limitations.</h2></div></div><div class="perspectives"><article><span>01 / MELCHIOR</span><h3>Fundamental analysis</h3><p>Financial evidence, business quality, and gaps in the investment case.</p></article><article><span>02 / BALTHASAR</span><h3>Market &amp; news</h3><p>Market context and source-attributed developments, with clear provenance.</p></article><article><span>03 / CASPER</span><h3>Downside risk</h3><p>Counterarguments, uncertainty, and what the available evidence cannot establish.</p></article></div></section>
<section class="notice request-gate"><div><h2>Prepared reports, ready to inspect</h2><p>Local preview with a development identity fixture. Authentication is not implemented. Invite-only access, quotas and cost controls are required before live requests can be enabled.</p></div><button disabled>New research · coming later</button></section>'''
        return 200, layout('Overview', body, '/')
    if url.path == '/reports':
        ticker = parse_qs(url.query).get('ticker', ['DEMO'])[0].strip().upper()
        visible = reports if ticker == 'DEMO' else {}
        return 200, layout('Research library', '<p class="eyebrow">PREPARED SYNTHETIC REPORTS</p><h1>Research library</h1><p class="lead">Read the evidence. Understand the perspectives.</p>' + search() + cards(visible), '/reports')
    if url.path == '/tickers':
        return 200, layout('Tickers', '<p class="eyebrow">COMPANY COVERAGE</p><h1>Company library</h1><p class="lead">A focused universe. Evidence you can inspect.</p>' + search() + '<div class="table-scroll" role="region" aria-label="Company coverage" tabindex="0"><table><caption>Local synthetic coverage · no real security coverage is established</caption><thead><tr><th scope="col">Company</th><th scope="col">Ticker / market</th><th scope="col">Coverage</th><th scope="col">Research</th></tr></thead><tbody><tr><td>Example Compute<br><small>Fictional company</small></td><td>DEMO / US</td><td>Synthetic · historical FY2025</td><td><a class="text-link" href="/reports?ticker=DEMO">View prepared reports ↗</a></td></tr></tbody></table></div>', '/tickers')
    key = url.path.removeprefix('/reports/')
    if url.path.startswith('/reports/') and key in reports:
        if reports[key] is None:
            return 503, layout('Report unavailable', '<h1>Report unavailable</h1><p>This prepared report failed validation. No partial or unvalidated content is shown.</p><a href="/reports">Return to library</a>')
        return 200, detail(key, reports[key])
    return 404, layout('Not found', '<h1>Report not found</h1><p>This link does not identify a prepared report.</p><a href="/reports">Return to library</a>')


class LocalServer(HTTPServer):
    def server_bind(self):
        # HTTPServer's default bind performs reverse DNS. This preview needs none.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        allowed_hosts = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
        if self.headers.get_all('Host') not in ([host] for host in allowed_hosts):
            self.reply(403, b'Local preview host required', 'text/plain')
            return
        fonts = {'Geist.ttf': 'font/ttf', 'GeistMono.ttf': 'font/ttf', 'PretendardVariable.woff2': 'font/woff2'}
        font_name = self.path.removeprefix('/assets/fonts/')
        if self.path.startswith('/assets/fonts/') and font_name in fonts:
            self.reply(200, (ROOT / 'static/fonts' / font_name).read_bytes(), fonts[font_name])
            return
        if self.path == '/assets/style.css':
            self.reply(200, (ROOT / 'static/style.css').read_bytes(), 'text/css')
            return
        try:
            status, body = page(self.path, self.server.reports)
        except Exception:
            status, body = 503, layout('Unavailable', '<h1>Report unavailable</h1><p>The report could not be displayed safely.</p>')
        self.reply(status, body.encode('utf-8'), 'text/html')

    def do_POST(self):
        self.reply(405, b'Read-only preview. Research execution is disabled.', 'text/plain')

    def reply(self, status, content, mime):
        self.send_response(status)
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass  # Never log user-controlled paths, headers, or report content.


def serve():
    reports = load_reports()
    with LocalServer(('127.0.0.1', 8765), Handler) as server:
        server.reports = reports
        server.timeout = 10
        print('Synthetic MAGI preview: http://127.0.0.1:8765 — local only', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
