"""Bounded Marketaux adapter. No issuer guessing, article scraping or persistence."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from ...models import Authority, instant, freeze
from ...security import text
from ..base import NewsQuery
from ..models import NewsArticle, NewsClassification, Relevance, normalize_ticker
from .transport import MarketauxTransport, MarketauxError, TTLCache


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value, bound=300, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError('Expected provider text')
    parser = _PlainText()
    parser.feed(unescape(value[:20000]))
    value = ' '.join(' '.join(parser.parts).split())
    value = ''.join(c for c in value if ord(c) >= 32).replace('<', '').replace('>', '')[:bound]
    if not value and optional:
        return None
    return text(value)


def number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('Invalid provider score')
    try:
        score = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid provider score') from None
    if not score.is_finite() or abs(score) > Decimal('1000000'):
        raise ValueError('Invalid provider score')
    return score


def codes(values):
    if not isinstance(values, (tuple, list)) or len(values) > 20:
        raise ValueError('Invalid filters')
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', v) for v in values):
        raise ValueError('Invalid filters')
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class ProviderEntity:
    symbol: str
    name: str
    type: str
    country: str
    exchange: str | None = None
    exchange_long: str | None = None
    industry: str | None = None


@dataclass(frozen=True)
class EntityResolution:
    ticker: str
    market: str | None
    search: str
    country: str
    status: str
    candidates: tuple[ProviderEntity, ...]
    retrieved_at: datetime

    @property
    def entity(self):
        return self.candidates[0] if self.status == 'RESOLVED' else None


def entity_row(raw):
    if not isinstance(raw, dict):
        raise ValueError('Invalid entity')
    symbol = raw.get('symbol')
    codes((symbol,))
    country = raw.get('country')
    if not isinstance(country, str) or not re.fullmatch(r'[A-Za-z]{2}', country):
        raise ValueError('Invalid entity country')
    return ProviderEntity(symbol, plain(raw.get('name')), plain(raw.get('type')).lower(),
        country.lower(), plain(raw.get('exchange'), optional=True),
        plain(raw.get('exchange_long'), optional=True), plain(raw.get('industry'), optional=True))


class MarketauxProvider:
    name = 'MARKETAUX'

    def __init__(self, *, country='us', market=None, exchange=None, entity_type='equity',
                 search=None, languages=(), countries=(), publisher_policy=None,
                 transport=None, now=None, sleep=None):
        codes((country, entity_type))
        if exchange:
            codes((exchange,))
        if market and market.upper() in ('US', 'KR') and country.lower() != market.lower():
            raise MarketauxError('PARAMETER_ERROR')
        self.country, self.market, self.exchange = country.lower(), market, exchange
        self.entity_type, self.search = entity_type.lower(), search
        self.languages, self.countries = codes(languages), codes(countries)
        self.publisher_policy = dict(publisher_policy or {})
        if any(not isinstance(k, str) or k != k.lower() or v not in (Authority.PRIMARY, Authority.SECONDARY, Authority.TERTIARY)
               for k, v in self.publisher_policy.items()):
            raise ValueError('Invalid publisher authority policy')
        options = dict(transport=transport, now=now)
        if sleep is not None:
            options['sleep'] = sleep
        self._http = MarketauxTransport(**options)
        self._entity_cache = TTLCache(capacity=128, ttl=3600)
        self._news_cache = TTLCache(capacity=64, ttl=300)

    @property
    def status(self):
        return dict(self._http.status)

    def close(self):
        self._http.close()

    def search_entities(self, *, search=None, symbols=(), country=None, exchange=None, entity_type=None):
        country = country or self.country
        exchange = exchange or self.exchange
        entity_type = entity_type or self.entity_type
        codes((country, entity_type))
        params = {'countries': country.lower(), 'types': entity_type.lower(), 'limit': 100, 'page': 1}
        if exchange:
            params['exchanges'] = ','.join(codes((exchange,)))
        if search:
            params['search'] = plain(search, 200)
        if symbols:
            params['symbols'] = ','.join(codes(symbols))
        if not search and not symbols:
            raise MarketauxError('PARAMETER_ERROR')
        payload = self._http.request('/v1/entity/search', params)
        try:
            rows = payload['data']
            if len(rows) > 100:
                raise ValueError('Entity bound')
            entities = tuple(entity_row(r) for r in rows)
            entities = tuple(sorted(set(e for e in entities if e.country == country.lower()
                and e.type == entity_type.lower() and (not exchange or e.exchange == exchange)),
                key=lambda e: (e.symbol, e.name, e.exchange or '', e.country)))
            meta = payload.get('meta', {})
            if not isinstance(meta, dict):
                raise ValueError('Invalid pagination')
            found = meta.get('found', len(rows))
            if type(found) is not int or found < len(rows):
                raise ValueError('Invalid count')
            return entities, found > len(rows)
        except (ValueError, TypeError, KeyError):
            raise MarketauxError('INVALID_RESPONSE') from None

    def resolve_entity(self, ticker, *, search=None, country=None, market=None, exchange=None):
        ticker = normalize_ticker(ticker)
        country, market = (country or self.country).lower(), market or self.market
        exchange, search = exchange or self.exchange, search or self.search or ticker
        if market and market.upper() in ('US', 'KR') and market.lower() != country:
            raise MarketauxError('PARAMETER_ERROR')
        key = (ticker, search, country, market, exchange, self.entity_type)
        now = self._http.now()
        instant(now)
        cached = self._entity_cache.get(key, now)
        if cached is not None:
            return cached
        # Search the original identity. Never synthesize a Korean exchange suffix.
        entities, incomplete = self.search_entities(search=search, country=country, exchange=exchange)
        now = self._http.now()
        instant(now)
        exact = tuple(e for e in entities if e.symbol.upper() == ticker)
        candidates = exact or tuple(e for e in entities if e.symbol.upper().split('.')[0] == ticker
            or e.name.casefold() == search.casefold())
        status = 'AMBIGUOUS' if incomplete or len(candidates) > 1 else 'RESOLVED' if candidates else 'NOT_FOUND'
        result = EntityResolution(ticker, market, search, country, status, candidates, now)
        self._entity_cache.put(key, now, result)
        return result

    def fetch(self, query):
        resolution = self.resolve_entity(query.ticker)
        if resolution.status != 'RESOLVED':
            raise MarketauxError('ENTITY_' + resolution.status)
        return self.news((resolution,), published_after=query.since, published_before=query.as_of,
                         languages=self.languages, countries=self.countries, limit=query.limit)

    def get_news(self, ticker, *, published_after=None, published_before=None, limit=3):
        as_of = published_before or self._http.now()
        return self.fetch(NewsQuery(ticker, ticker, as_of, published_after, limit))

    def news(self, resolutions, *, published_after=None, published_before=None,
             languages=(), countries=(), page=1, limit=3):
        if not resolutions or len(resolutions) > 20 or any(not isinstance(r, EntityResolution)
                or r.status != 'RESOLVED' for r in resolutions):
            raise MarketauxError('PARAMETER_ERROR')
        if type(page) is not int or not 1 <= page <= 100 or type(limit) is not int or not 1 <= limit <= 1000:
            raise MarketauxError('PARAMETER_ERROR')
        instant(published_after, True)
        instant(published_before, True)
        if published_after and published_before and published_after > published_before:
            raise MarketauxError('PARAMETER_ERROR')
        # With no text search, the API defaults to publication-time ordering.
        # "published_desc" is not a documented sort value.
        params = {'symbols': ','.join(sorted({r.entity.symbol for r in resolutions})),
                  'filter_entities': 'true', 'page': page, 'limit': limit}
        for name, value in (('published_after', published_after), ('published_before', published_before)):
            if value:
                # Marketaux accepts UTC whole seconds, without an offset suffix
                # or fractional seconds. Keep aware timestamps internally.
                params[name] = value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        if languages:
            params['language'] = ','.join(codes(languages))
        if countries:
            params['countries'] = ','.join(codes(countries))
        now = self._http.now()
        instant(now)
        # Identity context is part of the cache: same provider symbol may be used
        # in different MAGI queries. Retrieval/publication times remain untouched.
        key = (tuple(sorted(params.items())), tuple(resolutions))
        cached = self._news_cache.get(key, now)
        if cached is not None:
            return cached
        payload = self._http.request('/v1/news/all', params)
        now = self._http.now()
        instant(now)
        try:
            if len(payload['data']) > 1000:
                raise ValueError('Article bound')
            articles = []
            # Explicitly one page; nested similar objects are never imported.
            for raw in payload['data'][:limit]:
                article = self._article(raw, resolutions, now)
                if published_after and (article.published_at is None or article.published_at < published_after):
                    continue
                if published_before and article.published_at and article.published_at > published_before:
                    continue
                articles.append(article)
            articles = tuple(sorted({a.article_id: a for a in articles}.values(), key=lambda a: a.article_id))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise MarketauxError('INVALID_RESPONSE') from None
        self._news_cache.put(key, now, articles)
        return articles

    def _article(self, raw, resolutions, retrieved_at):
        if not isinstance(raw, dict):
            raise ValueError('Invalid article')
        title = plain(raw.get('title'), 1000)
        summary = plain(raw.get('description'), 1000, optional=True)
        url = raw.get('url')
        text(url)
        if urlsplit(url).hostname == 'api.marketaux.com':
            raise ValueError('API URL is not an article')
        published = raw.get('published_at')
        if published is not None:
            if not isinstance(published, str):
                raise ValueError('Invalid date')
            published = datetime.fromisoformat(published.replace('Z', '+00:00'))
            instant(published)
        publisher = plain(raw.get('source'), 250)
        hostname = (urlsplit(url).hostname or '').lower()
        # Require an exact independently configured hostname, also matching source.
        authority = self.publisher_policy.get(hostname, Authority.TERTIARY) if publisher.lower() == hostname else Authority.TERTIARY
        entities = raw.get('entities', [])
        if not isinstance(entities, list) or len(entities) > 100:
            raise ValueError('Invalid entities')
        metadata_entities, relevance = [], {}
        for row in entities[:20]:
            entity = entity_row(row)
            highlights = row.get('highlights', [])
            if not isinstance(highlights, list):
                raise ValueError('Invalid highlights')
            highlights_out = []
            for highlight in highlights[:5]:
                if not isinstance(highlight, dict):
                    raise ValueError('Invalid highlight')
                highlights_out.append({'text': plain(highlight.get('highlight'), 300),
                    'highlighted_in': plain(highlight.get('highlighted_in'), 50, optional=True),
                    'sentiment': number(highlight.get('sentiment')),
                    'provenance': 'MARKETAUX_HIGHLIGHT', 'trust': 'UNTRUSTED RESEARCH DATA',
                    'sentiment_kind': 'PROVIDER_SUPPLIED_SENTIMENT', 'provider': 'MARKETAUX',
                    'source_field': 'entities.highlights.sentiment', 'scope': entity.symbol})
            score = number(row.get('sentiment_score'))
            metadata_entities.append({**entity.__dict__, 'match_score': number(row.get('match_score')),
                'sentiment_score': score, 'sentiment_kind': 'PROVIDER_SUPPLIED_SENTIMENT',
                'sentiment_provenance': 'MARKETAUX_SENTIMENT', 'provider': 'MARKETAUX',
                'source_field': 'entities.sentiment_score', 'scope': entity.symbol,
                'highlights': highlights_out, 'provenance': 'MARKETAUX_ENTITY_METADATA'})
            for resolution in resolutions:
                if (entity.symbol, entity.country, entity.exchange) == (resolution.entity.symbol, resolution.entity.country, resolution.entity.exchange):
                    relevance[resolution.ticker] = (Relevance.DIRECTLY_RELATED if any(
                        h['highlighted_in'] == 'title' for h in highlights_out) else Relevance.MENTION_ONLY).value
        tickers = tuple(r.ticker for r in resolutions)
        for ticker in tickers:
            relevance.setdefault(ticker, Relevance.MENTION_ONLY.value)
        similar = raw.get('similar', [])
        if not isinstance(similar, list):
            raise ValueError('Invalid similar articles')
        similar_ids = tuple(sorted({plain(s.get('uuid'), 100) for s in similar[:20]
                                   if isinstance(s, dict) and s.get('uuid')}))
        meta = {'provider_entities': metadata_entities, 'snippet': plain(raw.get('snippet'), 500, optional=True),
                'provenance': 'MARKETAUX_ENTITY_METADATA', 'event_extraction': 'NONE',
                'similar_external_ids': similar_ids,
                'provider_mappings': [{'magi_ticker': r.ticker, 'market': r.market,
                    'symbol': r.entity.symbol, 'name': r.entity.name, 'exchange': r.entity.exchange,
                    'country': r.entity.country, 'resolved_at': r.retrieved_at.isoformat()} for r in resolutions]}
        return NewsArticle(provider=self.name, publisher=publisher, title=title, summary=summary,
            url=url, published_at=published, retrieved_at=retrieved_at,
            language=plain(raw.get('language'), 16), tickers=tickers,
            company_names=tuple(r.entity.name for r in resolutions), authority=authority,
            external_id=plain(raw.get('uuid'), 100), metadata=meta,
            classification=NewsClassification(classification_source='MARKETAUX_ENTITY_METADATA', relevance=freeze(relevance)))
