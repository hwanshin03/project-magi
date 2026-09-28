"""Bounded deterministic views. Atomic event/duplicate groups retain contrary reports."""
from dataclasses import dataclass
from .models import EventType, Relevance, VerificationStatus, Direction
from ..models import Authority
from .deduplication import deduplicate
from .relevance import relevance
from .service import build_news_pack


@dataclass(frozen=True)
class NewsSelectionPolicy:
    max_articles: int = 20
    max_evidence: int = 40
    max_catalysts: int = 20
    max_clusters: int = 20
    max_per_publisher: int = 10
    preferred_events: tuple = ()
    prefer_risks: bool = False

    def __post_init__(self):
        for name in ('max_articles','max_evidence','max_catalysts','max_clusters','max_per_publisher'):
            v=getattr(self,name)
            if type(v) is not int or not 0<=v<=1000: raise ValueError('Invalid selection bound')
        if not isinstance(self.preferred_events,(tuple,list)) or any(not isinstance(v,EventType) for v in self.preferred_events): raise ValueError('Invalid event preferences')
        object.__setattr__(self,'preferred_events',tuple(sorted(set(self.preferred_events),key=lambda e:e.value)))
        if type(self.prefer_risks) is not bool: raise ValueError('Invalid risk preference')


FUTURE_VIEWS={
    'melchior':NewsSelectionPolicy(preferred_events=(EventType.EARNINGS,EventType.GUIDANCE,EventType.CUSTOMER,EventType.CONTRACT)),
    'balthasar':NewsSelectionPolicy(preferred_events=(EventType.PRODUCT,EventType.INDUSTRY,EventType.MACRO,EventType.PARTNERSHIP)),
    'casper':NewsSelectionPolicy(preferred_events=(EventType.REGULATORY,EventType.LEGAL,EventType.SUPPLY_CHAIN),prefer_risks=True),
}


def select_news(pack,policy=NewsSelectionPolicy()):
    articles={a.article_id:a for a in pack.articles};parents={k:k for k in articles}
    def root(k):
        while parents[k]!=k:
            parents[k]=parents[parents[k]];k=parents[k]
        return k
    def merge(keys):
        keys=tuple(keys)
        for k in keys[1:]: parents[root(k)]=root(keys[0])
    source_articles={s.source_id:s.metadata['article_id'] for s in pack.sources}
    for c in pack.event_clusters: merge(source_articles[s] for s in c.source_ids)
    for group in deduplicate(pack.articles): merge(a.article_id for a in group.members)
    groups={}
    for k,a in articles.items(): groups.setdefault(root(k),[]).append(a)
    remaining=list(groups.values());selected=[];publishers={};used_events=set()
    rel_order=list(Relevance);authority={Authority.PRIMARY:0,Authority.SECONDARY:1,Authority.AUTHORITATIVE_DATA:2,Authority.TERTIARY:3,Authority.INTERNAL_HISTORY:4}
    def rank(group):
        risk=any(a.classification.direction in (Direction.NEGATIVE,Direction.MIXED) or a.classification.status in (VerificationStatus.RUMOR,VerificationStatus.UNCONFIRMED,VerificationStatus.DISPUTED) for a in group)
        return (min(publishers.get(a.publisher,0) for a in group),
                0 if any(a.classification.event_type not in used_events for a in group) else 1,
                0 if policy.prefer_risks and risk else 1,
                0 if any(a.classification.event_type in policy.preferred_events for a in group) else 1,
                min(rel_order.index(relevance(a,pack.ticker)) for a in group),
                min(authority[a.authority] for a in group),
                -max((a.published_at.timestamp() for a in group if a.published_at),default=float('-inf')),
                tuple(sorted(a.article_id for a in group)))
    while remaining:
        group=min(remaining,key=rank);remaining.remove(group)
        counts=dict(publishers)
        for a in group: counts[a.publisher]=counts.get(a.publisher,0)+1
        candidate=build_news_pack((*selected,*group),ticker=pack.ticker,subject=pack.subject,created_at=pack.created_at)
        if (len(candidate.articles)>policy.max_articles or len(candidate.evidence_items)>policy.max_evidence
                or len(candidate.catalysts)>policy.max_catalysts or len(candidate.event_clusters)>policy.max_clusters
                or any(n>policy.max_per_publisher for n in counts.values())): continue
        selected.extend(group);publishers=counts;used_events.update(a.classification.event_type for a in group)
    kept={a.article_id for a in selected}
    omitted=tuple(sorted(set(pack.selection_omissions)| (set(articles)-kept)))
    return build_news_pack(selected,ticker=pack.ticker,subject=pack.subject,created_at=pack.created_at,selection_omissions=omitted)
