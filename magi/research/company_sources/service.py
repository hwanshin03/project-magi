"""Pure normalization and explicit translation families; no persistence or scoring."""
from datetime import datetime
from ..models import Authority, SourceType, identity
from ..news.models import NewsArticle, NewsClassification, EventType, VerificationStatus, Relevance
from ..news.service import build_news_pack
from .models import OfficialCompanyItem, OfficialItemType as Kind
from .catalog import CompanySourceError

EVENTS = {Kind.EARNINGS_RELEASE: EventType.EARNINGS, Kind.PRODUCT_ANNOUNCEMENT: EventType.PRODUCT,
          Kind.DIVIDEND_NOTICE: EventType.DIVIDEND, Kind.IR_EVENT: EventType.OTHER}


def deduplicate_items(items):
    latest = {}
    for item in items:
        if not isinstance(item, OfficialCompanyItem): raise CompanySourceError('INVALID_ITEM')
        previous = latest.get(item.item_id)
        if previous and previous.retrieved_at == item.retrieved_at and previous != item:
            # Same canonical item on two source indexes is harmless only if all
            # content agrees. Conflicting simultaneous representations fail closed.
            from dataclasses import replace
            if replace(previous, source_name=item.source_name) != item:
                raise CompanySourceError('CONFLICTING_ITEM')
            if previous.source_name < item.source_name: continue
        if previous is None or item.retrieved_at >= previous.retrieved_at:
            latest[item.item_id] = item
    return tuple(latest[k] for k in sorted(latest))


def articles_for(items):
    items = deduplicate_items(items)
    groups = {i.item_id: {i.item_id} for i in items}
    for left in items:
        for right in items:
            if (left.provider == right.provider and left.ticker == right.ticker
                    and left.item_type == right.item_type and left.language.split('-')[0] != right.language.split('-')[0]
                    and right.url in left.metadata.get('translation_urls', ())
                    and left.url in right.metadata.get('translation_urls', ())):
                # Reciprocal official links, not headline similarity or matching dates.
                merged = groups[left.item_id] | groups[right.item_id]
                for key in merged: groups[key] = merged
    output = []
    for item in items:
        family = 'COMPANY_OFFICIAL:' + item.market + ':' + item.ticker
        event_key = identity('', 'OFFICIAL_EVENT', sorted(groups[item.item_id]))
        known_event = item.item_type in EVENTS
        metadata = {
            'official_item_id': item.item_id, 'official_source': item.source_name,
            'source_family': family, 'event_family': event_key, 'market': item.market,
            'item_type': item.item_type.value, 'original_language': item.language,
            'translation_item_ids': tuple(sorted(groups[item.item_id] - {item.item_id})),
            'classification_basis': item.metadata.get('category', 'registered_source_type'),
            'source_metadata': item.metadata,
        }
        if not known_event: metadata['event_extraction'] = 'NONE'
        classification = NewsClassification(
            event_type=EVENTS.get(item.item_type, EventType.OTHER),
            status=VerificationStatus.CONFIRMED_OFFICIAL,
            claimant=item.company_name,
            event_namespace=family if known_event else None,
            event_key=event_key if known_event else None,
            event_time=datetime.fromisoformat(item.metadata['event_time']) if item.metadata.get('event_time') else None,
            classification_source='official-source-metadata-v1',
            relevance={item.ticker: Relevance.PRIMARY_SUBJECT.value})
        output.append(NewsArticle(item.provider, item.company_name, item.title, item.summary,
            item.url, item.published_at, item.retrieved_at, item.language,
            (item.ticker,), (item.company_name,),
            source_type=SourceType.EARNINGS_RELEASE if item.item_type == Kind.EARNINGS_RELEASE else SourceType.COMPANY_IR,
            authority=Authority.PRIMARY, external_id=item.external_id or item.item_id,
            metadata=metadata, classification=classification))
    return tuple(output)


def build_company_pack(items, *, ticker, subject, created_at, additional_news=()):
    """Combine independent provenance without guessing links to external reporting.

    Additional news may already carry an explicitly sourced shared event identity.
    Marketaux's default unclassified articles remain unclassified and unchanged.
    """
    items = tuple(items)
    if any(item.ticker != ticker for item in items): raise CompanySourceError('IDENTITY_MISMATCH')
    return build_news_pack((*articles_for(items), *additional_news), ticker=ticker,
                           subject=subject, created_at=created_at)


class CompanySourceService:
    def __init__(self, provider):
        self.provider = provider

    def collect(self, query):
        items = tuple(self.provider.fetch_items(query))
        if len(items) > query.limit: raise CompanySourceError('ITEM_LIMIT')
        if any(i.ticker != query.ticker for i in items): raise CompanySourceError('IDENTITY_MISMATCH')
        items = tuple(i for i in items if (i.published_at is None or i.published_at <= query.as_of)
                      and (query.since is None or i.published_at is not None and i.published_at >= query.since))
        return build_company_pack(items, ticker=query.ticker, subject=query.subject,
                                  created_at=max((query.as_of, *(i.retrieved_at for i in items))))
