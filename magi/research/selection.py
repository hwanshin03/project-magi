"""Deterministic deduplication and bounded citation-closed views; no retrieval."""
from dataclasses import dataclass, replace, fields
from datetime import datetime, timezone
from typing import Optional, Tuple
from .models import (Authority, Category, SourceType, EvidencePack, ResearchSource,
                     EvidenceItem, ticker, instant)


def deduplicate(values):
    """Collapse identical records/retrievals only; conflicting ID reuse is an error.

    Earliest retrieval wins deterministically. Independent source identities remain.
    Changed text/numbers at the same locator remain distinct evidence IDs.
    """
    found={}
    for value in values:
        if not isinstance(value,(ResearchSource,EvidenceItem)): raise ValueError('Expected sources or evidence')
        key=(type(value),value.source_id if isinstance(value,ResearchSource) else value.evidence_id)
        old=found.get(key)
        if old is not None:
            if any(getattr(old,f.name)!=getattr(value,f.name) for f in fields(value) if f.name!='retrieved_at'):
                raise ValueError('Conflicting duplicate ID; preserve as separate version/snapshot')
            value=min((old,value),key=lambda v:v.retrieved_at)
        found[key]=value
    return tuple(found[key] for key in sorted(found,key=lambda k:(k[0].__name__,k[1])))


@dataclass(frozen=True)
class SelectionPolicy:
    authorities: Tuple[Authority,...] = ()
    categories: Tuple[Category,...] = ()
    source_types: Tuple[SourceType,...] = ()
    ticker: Optional[str] = None
    published_since: Optional[datetime] = None
    max_sources: int = 20
    max_evidence: int = 100

    def __post_init__(self):
        for name,kind in [('authorities',Authority),('categories',Category),('source_types',SourceType)]:
            values=getattr(self,name)
            if not isinstance(values,(tuple,list)) or any(not isinstance(v,kind) for v in values): raise ValueError('Invalid selection enum')
            object.__setattr__(self,name,tuple(values))
        ticker(self.ticker);instant(self.published_since,True)
        for value in (self.max_sources,self.max_evidence):
            if type(value) is not int or value<0: raise ValueError('Invalid selection bound')


def select(pack,policy):
    if not isinstance(pack,EvidencePack) or not isinstance(policy,SelectionPolicy): raise ValueError('Invalid selection input')
    sources={s.source_id:s for s in pack.sources}
    candidates=[]
    for e in pack.evidence_items:
        s=sources[e.source_id]
        if policy.authorities and s.authority not in policy.authorities: continue
        if policy.categories and e.category not in policy.categories: continue
        if policy.source_types and s.source_type not in policy.source_types: continue
        if policy.ticker and (e.ticker or s.ticker)!=policy.ticker: continue
        if policy.published_since and (s.published_at is None or s.published_at<policy.published_since): continue
        candidates.append(e)
    # Recency, then stable ID. Authority filters never compute truth weights.
    candidates.sort(key=lambda e:e.evidence_id)
    candidates.sort(key=lambda e:sources[e.source_id].published_at or datetime.min.replace(tzinfo=timezone.utc),reverse=True)
    chosen=[];used=set()
    for e in candidates:
        if len(chosen)>=policy.max_evidence: break
        if e.source_id not in used and len(used)>=policy.max_sources: continue
        chosen.append(e);used.add(e.source_id)
    ids={e.evidence_id for e in chosen}
    # Keep a claim only if ALL references survive. Never drop its opposing evidence
    # to promote a conflicted claim into a supported claim.
    claims=tuple(c for c in pack.claims if set((*c.supporting_evidence_ids,*c.contrary_evidence_ids,*c.unresolved_evidence_ids))<=ids)
    relations=tuple(r for r in pack.relations if set(r.evidence_ids)<=ids)
    return replace(pack,pack_id='',sources=tuple(sources[k] for k in used),evidence_items=tuple(chosen),claims=claims,relations=relations)


AGENT_POLICIES = {
    'Melchior':SelectionPolicy(categories=(Category.FINANCIAL,Category.VALUATION,Category.PROFITABILITY,Category.CASH_FLOW,Category.GROWTH,Category.GUIDANCE)),
    'Balthasar':SelectionPolicy(categories=(Category.MACRO,Category.INDUSTRY,Category.MARKET_PRICE,Category.MARKET_VOLUME,Category.CATALYST,Category.SENTIMENT)),
    'Casper':SelectionPolicy(categories=(Category.RISK,Category.BALANCE_SHEET,Category.REGULATORY,Category.COMPETITION)),
}
