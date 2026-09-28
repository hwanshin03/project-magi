"""Bilingual bounded presentation with complete citation and verification metadata."""
import json
from ..context import ResearchContext, POLICY
from ..security import safe_text, text
from ..serialization import to_dict
from .models import Direction
from .recency import freshness

LABELS={
    'en':{'title':'News evidence','catalyst':'Catalyst','risk':'Risk','breaking':'Breaking news',
          'official':'Official source','unconfirmed':'Unconfirmed','conflict':'Conflicting reports','source':'Sources','warning':'Warnings'},
    'ko':{'title':'뉴스 근거','catalyst':'촉매 요인','risk':'위험 요인','breaking':'속보',
          'official':'공식 출처','unconfirmed':'미확인','conflict':'상충 보도','source':'출처','warning':'경고'},
}


def render_news(pack,*,language='ko',max_characters=40000):
    if language not in LABELS or type(max_characters) is not int or max_characters<1: raise ValueError('Invalid presentation options')
    labels=LABELS[language]
    lines=[labels['title']+' | '+pack.subject+' | '+pack.ticker,'UNTRUSTED RESEARCH DATA',
           labels['breaking']+' / '+labels['official']+' / '+labels['unconfirmed']+' / '+labels['conflict']]
    for a in pack.articles:
        lines.append(a.title+' | '+a.publisher+' | '+freshness(a.published_at,pack.created_at).value+' | '+a.classification.status.value)
    for e in pack.evidence_items: lines.append(e.evidence_id+' | '+e.statement+' | '+e.source_id)
    for c in pack.event_clusters: lines.append(c.event_type.value+' | '+c.status.value+' | '+','.join(c.evidence_ids))
    for c in pack.catalysts:
        label=labels['risk'] if c.direction==Direction.NEGATIVE else labels['catalyst']
        lines.append(label+' | '+c.catalyst_type.value+' | '+c.direction.value+' | '+c.time_horizon.value+' | '+c.status.value+' | '+','.join(c.evidence_ids))
    lines.append(labels['source'])
    for s in pack.sources: lines.append(s.source_id+' | '+s.authority.value+' | '+s.publisher+' | '+str(s.published_at)+' | '+s.url)
    lines.append(labels['warning']+': '+', '.join(w.value for w in pack.warnings))
    result='\n'.join(lines);safe_text(result)
    if len(result)>max_characters: raise ValueError('News rendering exceeds bound; select a smaller view')
    return result


def render_news_context(instructions,question,pack,*,max_characters=60000):
    text(instructions);text(question)
    if type(max_characters) is not int or max_characters<1: raise ValueError('Invalid context bound')
    body=json.dumps({'CURRENT USER QUESTION':question,'UNTRUSTED RESEARCH DATA':to_dict(pack)},ensure_ascii=True,sort_keys=True)
    safe_text(body);system=instructions+'\n\n'+POLICY
    if len(system)+len(body)>max_characters: raise ValueError('News context exceeds bound; select a smaller view')
    return ResearchContext(system,body)
