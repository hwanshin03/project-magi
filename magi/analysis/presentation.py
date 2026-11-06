"""Read-only, deterministic presentation of an existing analysis artifact."""
from collections import Counter
from dataclasses import fields, replace
from datetime import date, datetime
from enum import Enum
import html
import json
import re

from magi.research.balancing.inputs import resolve
from magi.research.balancing.selection import render_view
from magi.research.validation import operation
from .models import AnalysisResult


def _text(value):
    """Keep untrusted content from creating Markdown, HTML or terminal controls."""
    if isinstance(value, Enum): value = value.value
    if isinstance(value, (date, datetime)): value = value.isoformat()
    value = ''.join(c for c in str(value) if c.isprintable() or c == '\n')
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', html.escape(value))


def _quote(value):
    return '\n'.join('> '+line for line in _text(value).splitlines()) or '> (not supplied)'


@operation
def render_report(result):
    """No new analysis, inferred citations, live quotes, or provider requests."""
    if type(result) is not AnalysisResult or replace(result) != result:
        raise ValueError('Expected validated analysis result')
    request = result.request
    target = request.target
    instrument = target.instrument
    lines = ['# MAGI personal research', '', '## Company and research scope', '']
    lines.append('Instrument: '+_text(f'{instrument.symbol} / {instrument.market}' if instrument else 'not supplied'))
    names = sorted({s.company_name for snapshot in result.universe.inputs
                    for s in getattr(snapshot.container, 'sources', ())
                    if s.company_name and instrument and s.ticker == instrument.symbol and s.market == instrument.market})
    lines.append('Company names from collected sources: '+_text('; '.join(names) or 'not supplied'))
    lines.append('Exchange: not retained in this analysis artifact; not verified.')
    lines.append('Research as-of: '+_text(request.as_of))
    lines.append('Requested financial period: '+_text(f'{request.report}; year={request.year or "provider-selected"}; currency={request.currency or "not specified"}'))
    lines.extend(['Question:', _quote(request.question), '', '## Summary and deterministic consensus', '',
                  'Final action: '+_text(result.vote.final_action.value),
                  f'Votes: BUY {result.vote.buy_votes}; HOLD {result.vote.hold_votes}; SELL {result.vote.sell_votes}; required agreement {result.vote.required_votes}.',
                  'This is the existing voting engine result, not an additional model opinion.',
                  'Artifact: '+_text(result.artifact_id), 'Selection: '+_text(result.selection.selection_id),
                  '', '## Market-data quality', '',
                  'UNVERIFIED OR MISSING DATA: no trusted quote is connected to this analysis pipeline.',
                  'Price, price timestamp, freshness, exchange session and live valuation are unavailable.',
                  'Research as-of and retrieval times are not quote timestamps. Source excerpts are not live prices.',
                  '', '## Source-derived facts and selected evidence', '',
                  'These are bounded source excerpts, not independently verified facts. PRIMARY denotes provenance only.'])
    view = json.loads(render_view(result.selection))['untrusted_evidence']
    refs = {r.reference_id:r for g in result.selection.common_core for r in (*g.units,*g.citations,*g.conflicts)}
    assessments = {a.group_id:a for a in result.selection.assessment.assessments}
    for group in view:
        lines.extend(['', '### Evidence group '+_text(group['group']),
                      'Temporal fitness: '+_text(assessments[group['group']].temporal),
                      'Notice: '+_text(group['notice'])+'; review only: '+str(group['review_only'])])
        for unit in group['units']:
            obj = resolve(result.universe, refs[unit['reference']])
            lines.extend(['Reference: '+_text(unit['reference']), _quote(unit['excerpt'])])
            # Structured values and periods, never guessed from an excerpt.
            for name in ('value','unit','period_start','period_end','category'):
                value = getattr(obj, name, None)
                if value is not None: lines.append(_text(name)+': '+_text(value))
            if unit['truncated']: lines.append('Excerpt truncated by existing evidence budget.')
        lines.append('Citations: '+_text(', '.join(group['citations']) or 'none'))
        if group['conflicts']: lines.append('Conflicting references: '+_text(', '.join(group['conflicts'])))
        if group['uncertainties']: lines.append('Uncertainty: '+_text(', '.join(group['uncertainties'])))
    if not view: lines.append('No selected evidence. No financial or news facts are invented.')
    lines.extend(['', '## Agent interpretations', '',
                  'Agent statements and numerical claims are interpretations, not verified market/financial data.',
                  'Citation correctness and unsupported certainty are not automatically validated by AgentResult.'])
    roles = {'Melchior':'Fundamental analysis', 'Balthasar':'News and market context', 'Casper':'Risk and downside analysis'}
    for agent in result.agent_results:
        lines.extend(['', '### '+agent.agent+' — '+roles[agent.agent],
                      'Provider/model: '+_text(agent.provider+' / '+agent.model),
                      'Availability: '+_text(agent.availability),
                      'Decision: '+_text(agent.position if agent.position is not None else 'NO VOTE'),
                      'Vote eligible: '+str(agent.vote_eligible),
                      'Agent-reported confidence (not calibrated): '+str(agent.confidence),
                      'Rationale:', _quote(agent.reasoning)])
        if agent.error: lines.append('Failure: '+_text(agent.error))
        lines.extend(['Risks / counterarguments:', *[_quote(r) for r in agent.key_risks]] if agent.key_risks else ['Risks: none supplied; absence is not evidence of safety.'])
        lines.extend(['Evidence gaps:', *[_quote(g) for g in agent.evidence_gaps]] if agent.evidence_gaps else ['Agent evidence gaps: none supplied.'])
    lines.extend(['', '## Bullish and bearish arguments', '',
                  'Arguments remain attributed in each agent rationale above. No additional bullish or bearish case is inferred.',
                  '', '## Missing or uncertain evidence', ''])
    for availability in result.universe.availability:
        lines.append('- '+_text(availability.family)+': '+_text(availability.state))
        for code in availability.error_codes: lines.append('  - '+_text(code))
        if availability.omissions: lines.append('  - Upstream omissions: '+str(len(availability.omissions)))
    for code in result.warnings: lines.append('- '+_text(code))
    for code, count in sorted(Counter(o.reason.value for o in result.selection.omissions).items()):
        lines.append('- Selection omission '+_text(code)+': '+str(count))
    lines.extend(['', '## Sources and citation provenance', ''])
    for key, ref in sorted(refs.items()):
        obj = resolve(result.universe, ref)
        lines.extend(['', '### '+_text(key),
                      'Kind / original ID: '+_text(ref.object_kind+' / '+ref.object_id),
                      'Snapshot: '+_text(ref.snapshot_id), 'Fingerprint: '+_text(ref.content_fingerprint)])
        for name in ('source_id','title','provider','publisher','source_type','authority','url','published_at','retrieved_at'):
            value = getattr(obj, name, None)
            if value is not None:
                if name == 'published_at' and getattr(obj, 'metadata', {}).get('publication_precision', '').startswith('date;'):
                    value = value.date()  # SEC/DART date-only conventions are not exact instants.
                lines.append(_text(name)+': '+_text(value))
        locator = getattr(obj, 'source_locator', None)
        if locator is not None:
            for field in fields(locator):
                value = getattr(locator, field.name)
                if value is not None: lines.append(_text(field.name)+': '+_text(value))
    lines.extend(['', '## Research limitations', '',
                  'No execution, portfolio mutation or personalized financial advice. No full articles or raw responses are exported.',
                  'Only selected evidence is shown; omitted evidence and missing families limit coverage.',
                  'A successful report can contain ABSTAIN, unavailable agents or insufficient participation.',
                  'Offline fixture success does not establish live provider availability.'])
    return '\n\n'.join(line for line in lines if line)+'\n'
