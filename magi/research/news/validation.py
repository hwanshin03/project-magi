"""Pack graph invariants and derived quality warnings, checked on deserialization too."""
from ..models import Authority
from .models import NewsWarning as W, VerificationStatus as V, Freshness
from .recency import freshness
from .classification import normalize
from .clustering import cluster_events


def validate_pack(pack):
    expected=[normalize(a,pack.ticker,pack.subject,pack.created_at) for a in pack.articles]
    if any(pack.ticker not in a.tickers or a.retrieved_at>pack.created_at for a in pack.articles): raise ValueError('Invalid article scope/time')
    for index,name,key in [(0,'sources','source_id'),(1,'evidence_items','evidence_id'),(2,'events','event_id'),(3,'catalysts','catalyst_id')]:
        values=tuple(sorted((row[index] for row in expected if row[index] is not None),key=lambda x:getattr(x,key)))
        if values!=getattr(pack,name): raise ValueError('News provenance graph differs from normalized articles')
    if cluster_events(pack.events,pack.sources)!=pack.event_clusters: raise ValueError('Invalid event clustering/provenance')


def quality(pack):
    warnings=set();times=[freshness(a.published_at,pack.created_at) for a in pack.articles]
    if not any(f in (Freshness.BREAKING,Freshness.RECENT) for f in times): warnings.add(W.NO_RECENT_NEWS)
    if Freshness.UNKNOWN in times: warnings.add(W.MISSING_PUBLICATION_DATE)
    if Freshness.STALE in times: warnings.add(W.STALE_NEWS)
    if not any(s.authority==Authority.PRIMARY for s in pack.sources): warnings.add(W.NO_PRIMARY_SOURCE)
    publishers={a.publisher for a in pack.articles}
    # Additive first-party views expose family counts separately from raw outlets.
    # Neither count is a materiality score or establishes independent corroboration.
    family_coverage={}
    if any(a.metadata.get('official_item_id') for a in pack.articles):
        from .provenance import source_family
        families={source_family(a) for a in pack.articles}
        family_coverage={'source_family_count':len(families),'source_families':tuple(sorted(families))}
    if family_coverage.get('source_family_count',len(publishers))<2: warnings.add(W.LOW_SOURCE_DIVERSITY)
    if any(c.status==V.DISPUTED for c in pack.event_clusters): warnings.add(W.CONFLICTING_NEWS)
    if any(c.status in (V.RUMOR,V.UNCONFIRMED) for c in pack.event_clusters): warnings.add(W.UNCONFIRMED_EVENT)
    if pack.selection_omissions: warnings.add(W.BOUNDED_SELECTION)
    return tuple(sorted(warnings,key=lambda w:w.value)),{
        'article_count':len(pack.articles),'publisher_count':len(publishers),
        'event_types':tuple(sorted({e.event_type.value for e in pack.events})),
        'has_primary_source':any(s.authority==Authority.PRIMARY for s in pack.sources),
        'has_recent_news':any(f in (Freshness.BREAKING,Freshness.RECENT) for f in times),
        'omitted_article_count':len(pack.selection_omissions),**family_coverage}
