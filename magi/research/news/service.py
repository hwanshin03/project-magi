"""Pure pack construction. No default provider, network client or persistence path."""
from .models import NewsEvidencePack, normalize_ticker
from .classification import normalize
from .clustering import cluster_events
from .deduplication import deduplicate


def build_news_pack(articles,*,ticker,subject,created_at,selection_omissions=()):
    ticker=normalize_ticker(ticker)
    # Exact repeats disappear; duplicate groups retain versions and independent provenance.
    articles=tuple(a for g in deduplicate(articles) for a in g.members if ticker in a.tickers)
    rows=[normalize(a,ticker,subject,created_at) for a in articles]
    sources=tuple(r[0] for r in rows);evidence=tuple(r[1] for r in rows);events=tuple(r[2] for r in rows if r[2] is not None)
    catalysts=tuple(r[3] for r in rows if r[3] is not None)
    return NewsEvidencePack(ticker,subject,created_at,articles,sources,evidence,events,
        cluster_events(events,sources),catalysts,selection_omissions)


class NewsService:
    """Explicit injected provider only; no implicit live requests."""
    def __init__(self,provider): self.provider=provider

    def collect(self,query):
        articles=tuple(self.provider.fetch(query))
        if len(articles)>query.limit: raise ValueError('Provider exceeded requested article bound')
        if query.since:
            articles=tuple(a for a in articles if a.published_at is not None and a.published_at>=query.since)
        # as_of bounds publication, while a live request may finish after it.
        # Keep the injected provider retrieval clock rather than backdating data.
        created_at=max((query.as_of, *(a.retrieved_at for a in articles)))
        return build_news_pack(articles,ticker=query.ticker,subject=query.subject,created_at=created_at)
