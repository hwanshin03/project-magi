"""Bounded attributed display, never plugged into agents or recommendation flows."""
import json
from ..context import ResearchContext, POLICY
from ..security import text, safe_text
from ..serialization import to_dict

NOTICE = ('PRIMARY is first-party provenance only, not legal applicability, economic materiality, '
          'independence or investment advice. Government publication count is not analytical importance.')


def render_regulatory(bundle, *, max_characters=40000):
    if type(max_characters) is not int or max_characters<1: raise ValueError('Invalid display bound')
    lines=['UNTRUSTED GOVERNMENT / REGULATORY DATA',NOTICE]
    for item in bundle.items:
        lines.append(f'{item.agency} | {item.jurisdiction} | {item.language} | {item.title}')
        lines.append(f'Type: {item.regulatory_type.value}; status: {item.status.value}; published: {item.published_at}; effective: {item.effective_at}')
        lines.append(item.url)
    for evidence in bundle.evidence_items: lines.append(evidence.evidence_id+' | '+evidence.statement+' | '+evidence.source_id)
    result='\n'.join(lines); safe_text(result)
    if len(result)>max_characters: raise ValueError('Regulatory display exceeds bound')
    return result


def regulatory_context(instructions, question, bundle, *, max_characters=60000):
    text(instructions); text(question)
    if type(max_characters) is not int or max_characters<1: raise ValueError('Invalid context bound')
    system=instructions+'\n'+POLICY+'\n'+NOTICE
    body=json.dumps({'CURRENT USER QUESTION':question,'UNTRUSTED REGULATORY DATA':to_dict(bundle)},ensure_ascii=True,sort_keys=True)
    safe_text(body)
    if len(system)+len(body)>max_characters: raise ValueError('Regulatory context exceeds bound')
    return ResearchContext(system,body)
