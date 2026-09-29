"""Synthetic Marketaux contract/security tests. Every request uses MockTransport."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import traceback
import unittest
from unittest.mock import patch
from urllib.parse import quote

import httpx
from magi.research.models import Authority, Category, SourceType
from magi.research.serialization import dumps, loads
from magi.research.news.base import NewsQuery
from magi.research.news.models import Relevance, Freshness, Sentiment
from magi.research.news.service import NewsService
from magi.research.news.selection import select_news, NewsSelectionPolicy
from magi.research.news.deduplication import deduplicate
from magi.research.news.presentation import render_news, render_news_context
from magi.research.news.recency import freshness
from magi.research.news.providers import MarketauxProvider, MarketauxError
from magi.research.news.providers.transport import TTLCache, ENDPOINTS
from magi.research.news.cli import main as news_cli

NOW = datetime(2026, 9, 27, 13, tzinfo=timezone.utc)
FAKE = 'offline-fixture-credential-A9/+=z'
FIXTURES = Path(__file__).parent / 'fixtures' / 'marketaux'


def fixture(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


class MarketauxTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.socket.connect_ex',
                       'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Network forbidden in offline tests'))
            guard.start()
            self.addCleanup(guard.stop)
        env = patch.dict('os.environ', {'MARKETAUX_API_TOKEN': FAKE, 'PYTHON_DOTENV_DISABLED': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.entities = fixture('entities')['nvda']
        self.news = fixture('news')['one']
        self.requests, self.sleeps = [], []
        self.now = NOW

    def provider(self, handler=None, **kwargs):
        def route(request):
            self.requests.append(request)
            if handler:
                return handler(request)
            return httpx.Response(200, json=self.entities if request.url.path.endswith('/search') else self.news)
        provider = MarketauxProvider(transport=httpx.MockTransport(route), now=lambda: self.now,
            sleep=self.sleeps.append, **kwargs)
        self.addCleanup(provider.close)
        return provider

    def pack(self, provider=None, limit=100):
        return NewsService(provider or self.provider()).collect(NewsQuery('NVDA', 'NVIDIA', NOW, limit=limit))

    def article(self, **kwargs):
        return self.pack(self.provider(**kwargs)).articles[0]

    def test_missing_token(self):
        with patch.dict('os.environ', {'MARKETAUX_API_TOKEN': ''}):
            with self.assertRaisesRegex(MarketauxError, '^MARKETAUX_CONFIGURATION_ERROR$'):
                self.provider()
        self.assertEqual(self.requests, [])

    def test_environment_configuration_only(self):
        with patch('magi.research.news.providers.transport.load_dotenv') as loader:
            p = self.provider()
            p.resolve_entity('NVDA')
        self.assertEqual(loader.call_args.args[0], Path(__file__).resolve().parents[1] / '.env')
        self.assertEqual(self.requests[0].url.params['api_token'], FAKE)

    def test_import_has_no_configuration_or_requests(self):
        code = '''
from contextlib import ExitStack
from unittest.mock import patch
with ExitStack() as stack:
    for target in ('dotenv.load_dotenv', 'socket.socket.connect',
                   'socket.getaddrinfo', 'httpx.HTTPTransport.handle_request'):
        stack.enter_context(patch(target, side_effect=AssertionError('Unexpected import I/O')))
    import magi.research.news.providers
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixed_https_get_endpoints_timeout(self):
        self.pack()
        self.assertEqual({r.url.path for r in self.requests}, ENDPOINTS)
        for request in self.requests:
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.url.host, 'api.marketaux.com')
            self.assertEqual(request.url.scheme, 'https')
            self.assertTrue(all(v == 15 for v in request.extensions['timeout'].values()))

    def test_arbitrary_endpoint_rejected(self):
        p = self.provider()
        for endpoint in ('http://api.marketaux.com/v1/news/all', '/v1/news/all?x=y',
                         'https://other.invalid/', '/v1/entity/stats'):
            with self.subTest(endpoint=endpoint), self.assertRaises(MarketauxError):
                p._http.request(endpoint, {})
        self.assertEqual(self.requests, [])

    def test_redirect_not_followed(self):
        p = self.provider(lambda r: httpx.Response(302, headers={'location': 'https://other.invalid/'}))
        with self.assertRaisesRegex(MarketauxError, 'INVALID_RESPONSE'):
            p.resolve_entity('NVDA')
        self.assertEqual(len(self.requests), 1)

    def test_library_debug_logs_never_expose_request(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        def route(r):
            for name in ('httpx', 'httpcore.http11', 'httpcore.http2', 'httpcore.connection'):
                logger = logging.getLogger(name)
                original = logger.level
                logger.setLevel(logging.DEBUG)
                try:
                    logger.debug('request %s', r.url)
                finally:
                    logger.setLevel(original)
            return httpx.Response(200, json=self.entities)
        self.provider(route).resolve_entity('NVDA')
        self.assertEqual(stream.getvalue(), '')

    def test_exception_url_removed(self):
        def route(r):
            raise httpx.ReadTimeout(str(r.url), request=r)
        with self.assertRaises(MarketauxError) as cm:
            self.provider(route).resolve_entity('NVDA')
        output = ''.join(traceback.format_exception(cm.exception))
        self.assertNotIn(FAKE, output)
        self.assertNotIn('api_token', output)
        self.assertNotIn('https://api.marketaux.com', output)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.sleeps, [1, 2])

    def test_network_failure_bounded(self):
        def route(r):
            raise httpx.ConnectError('synthetic failure', request=r)
        with self.assertRaisesRegex(MarketauxError, 'TEMPORARILY_UNAVAILABLE'):
            self.provider(route).resolve_entity('NVDA')
        self.assertEqual(len(self.requests), 3)

    def test_token_echo_rejected(self):
        encoded = quote(FAKE, safe='')
        for value in (FAKE, encoded, encoded.replace('%2F', '%2f').replace('%2B', '%2b'),
                      quote(encoded, safe=''), 'https://api.marketaux.com/?api_token=' + FAKE):
            with self.subTest(value='redacted synthetic variant'):
                self.news['data'][0]['description'] = value
                with self.assertRaisesRegex(MarketauxError, 'INVALID_RESPONSE'):
                    self.pack()

    def test_unknown_error_code_and_message_not_exposed(self):
        def route(r):
            return httpx.Response(401, json={'error': {'code': str(r.url), 'message': str(r.url)}})
        with self.assertRaises(MarketauxError) as cm:
            self.provider(route).resolve_entity('NVDA')
        self.assertIsNone(cm.exception.provider_code)
        self.assertNotIn('api_token', repr(cm.exception.__dict__))

    def test_token_absent_from_serialization(self):
        content = dumps(self.pack())
        self.assertNotIn(FAKE, content)
        self.assertNotIn(quote(FAKE, safe=''), content)
        self.assertNotIn('api_token', content)
        self.assertNotIn('api.marketaux.com', content)

    def test_endpoint_parameters_cannot_override_token(self):
        with self.assertRaises(MarketauxError):
            self.provider()._http.request('/v1/news/all', {'api_token': 'override'})
        self.assertEqual(self.requests, [])

    def test_nvda_resolution(self):
        result = self.provider().resolve_entity('nvda')
        self.assertEqual(result.status, 'RESOLVED')
        self.assertEqual(result.entity.symbol, 'NVDA')
        self.assertEqual(result.country, 'us')
        self.assertEqual(result.retrieved_at, NOW)
        self.assertEqual(self.requests[0].url.params['types'], 'equity')

    def test_korean_resolution_preserves_zeros_and_provider_symbol(self):
        self.entities = fixture('entities')['korean']
        p = self.provider(country='kr', market='KR')
        result = p.resolve_entity('005930')
        self.assertEqual(result.ticker, '005930')
        self.assertEqual(result.entity.symbol, '005930.FIXTURE')
        self.news['data'][0]['entities'] = self.entities['data']
        articles = p.fetch(NewsQuery('005930', 'Samsung Electronics', NOW))
        self.assertEqual(self.requests[-1].url.params['symbols'], '005930.FIXTURE')
        self.assertEqual(articles[0].tickers, ('005930',))
        mapping = articles[0].metadata['provider_mappings'][0]
        self.assertEqual(mapping['magi_ticker'], '005930')
        self.assertEqual(mapping['name'], 'Samsung Electronics')
        self.assertEqual(mapping['exchange'], 'SYNTHETIC_KR')

    def test_name_search_can_resolve_different_provider_symbol(self):
        self.entities = fixture('entities')['korean']
        self.entities['data'][0]['symbol'] = 'SYNTHETIC_SAMSUNG'
        p = self.provider(country='kr', search='Samsung Electronics')
        self.assertEqual(p.resolve_entity('005930').entity.symbol, 'SYNTHETIC_SAMSUNG')

    def test_does_not_guess_unrelated_single_result(self):
        self.assertEqual(self.provider(country='us').resolve_entity('005930').status, 'NOT_FOUND')

    def test_ambiguous_no_news_request(self):
        self.entities = fixture('entities')['ambiguous']
        p = self.provider()
        self.assertEqual(p.resolve_entity('NVDA').status, 'AMBIGUOUS')
        with self.assertRaisesRegex(MarketauxError, 'ENTITY_AMBIGUOUS'):
            p.get_news('NVDA')
        self.assertEqual(len(self.requests), 1)

    def test_empty_resolution(self):
        self.entities = fixture('entities')['empty']
        self.assertEqual(self.provider().resolve_entity('NVDA').status, 'NOT_FOUND')

    def test_wrong_country_filtered_locally(self):
        self.entities = fixture('entities')['wrong_country']
        self.assertEqual(self.provider().resolve_entity('NVDA').status, 'NOT_FOUND')

    def test_country_market_mismatch(self):
        with self.assertRaisesRegex(MarketauxError, 'PARAMETER_ERROR'):
            self.provider(country='us', market='KR')

    def test_exchange_filter(self):
        self.assertEqual(self.provider(exchange='OTHER').resolve_entity('NVDA').status, 'NOT_FOUND')
        self.assertEqual(self.requests[0].url.params['exchanges'], 'OTHER')

    def test_incomplete_entity_page_is_ambiguous(self):
        self.entities = fixture('entities')['truncated']
        self.assertEqual(self.provider().resolve_entity('NVDA').status, 'AMBIGUOUS')

    def test_search_by_symbols(self):
        self.provider().search_entities(symbols=('NVDA',))
        self.assertEqual(self.requests[0].url.params['symbols'], 'NVDA')

    def test_entity_cache_expiry_and_retrieval_time(self):
        p = self.provider()
        first = p.resolve_entity('NVDA')
        self.now += timedelta(minutes=5)
        self.assertEqual(p.resolve_entity('NVDA'), first)
        self.assertEqual(len(self.requests), 1)
        self.now += timedelta(hours=1)
        self.assertNotEqual(p.resolve_entity('NVDA').retrieved_at, first.retrieved_at)
        self.assertEqual(len(self.requests), 2)

    def test_entity_cache_context(self):
        p = self.provider()
        p.resolve_entity('NVDA')
        p.resolve_entity('NVDA', country='ca')
        p.resolve_entity('NVDA', exchange='OTHER')
        self.assertEqual(len(self.requests), 3)

    def test_ambiguous_cache_not_permanent(self):
        p = self.provider()
        self.entities = fixture('entities')['ambiguous']
        self.assertEqual(p.resolve_entity('NVDA').status, 'AMBIGUOUS')
        self.entities = fixture('entities')['nvda']
        self.now += timedelta(hours=2)
        self.assertEqual(p.resolve_entity('NVDA').status, 'RESOLVED')

    def test_lru_capacity_and_clock_rollback(self):
        cache = TTLCache(capacity=2, ttl=60)
        for i in range(3):
            cache.put(i, NOW, i)
        self.assertIsNone(cache.get(0, NOW))
        self.assertEqual(cache.get(2, NOW), 2)
        self.assertIsNone(cache.get(1, NOW - timedelta(seconds=1)))

    def test_news_query_parameters(self):
        p = self.provider()
        resolution = p.resolve_entity('NVDA')
        p.news((resolution,), published_after=NOW-timedelta(days=1), published_before=NOW,
               languages=('en', 'ko'), countries=('us',), page=2, limit=20)
        params = self.requests[-1].url.params
        self.assertNotIn('sort', params)
        self.assertEqual(params['filter_entities'], 'true')
        self.assertEqual(params['page'], '2')
        self.assertEqual(params['limit'], '20')
        self.assertEqual(params['language'], 'en,ko')
        self.assertEqual(params['published_before'], '2026-09-27T13:00:00')
        self.assertEqual(len(self.requests), 2)

    def test_request_dates_use_utc_seconds_without_offset_or_fraction(self):
        p = self.provider()
        resolution = p.resolve_entity('NVDA')
        after = datetime(2026, 9, 27, 20, 0, 0, 123456,
                         tzinfo=timezone(timedelta(hours=9)))
        before = datetime(2026, 9, 27, 6, 0, 0, 999999,
                          tzinfo=timezone(timedelta(hours=-7)))
        p.news((resolution,), published_after=after, published_before=before)
        params = self.requests[-1].url.params
        self.assertEqual(params['published_after'], '2026-09-27T11:00:00')
        self.assertEqual(params['published_before'], '2026-09-27T13:00:00')

    def test_multiple_resolved_symbols(self):
        p = self.provider()
        first = p.resolve_entity('NVDA')
        self.entities['data'][0] = {**self.entities['data'][0], 'symbol': 'PEER', 'name': 'Peer'}
        second = p.resolve_entity('PEER')
        articles = p.news((first, second))
        self.assertEqual(self.requests[-1].url.params['symbols'], 'NVDA,PEER')
        self.assertEqual(articles[0].tickers, ('NVDA', 'PEER'))

    def test_free_plan_fewer_articles_than_requested(self):
        self.news = fixture('news')['free_three']
        pack = self.pack(limit=50)
        self.assertEqual(len(pack.articles), 3)
        self.assertEqual(len(self.requests), 2)

    def test_no_automatic_pagination(self):
        self.news['meta']['found'] = 5000
        self.pack(limit=50)
        self.assertEqual(len(self.requests), 2)

    def test_provider_response_cannot_exceed_query_limit(self):
        self.news = fixture('news')['free_three']
        self.assertEqual(len(self.pack(limit=1).articles), 1)

    def test_news_cache_keeps_original_timestamps(self):
        p = self.provider()
        query = NewsQuery('NVDA', 'NVIDIA', NOW)
        first = p.fetch(query)
        self.now += timedelta(minutes=1)
        self.assertEqual(p.fetch(query), first)
        self.assertEqual(len(self.requests), 2)
        self.now += timedelta(minutes=5)
        second = p.fetch(query)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(second[0].published_at, first[0].published_at)
        self.assertNotEqual(second[0].retrieved_at, first[0].retrieved_at)

    def test_pack_created_after_request_clock(self):
        p = self.provider()
        self.now += timedelta(seconds=5)
        pack = self.pack(p)
        self.assertEqual(pack.created_at, self.now)
        self.assertEqual(pack.articles[0].retrieved_at, self.now)

    def test_article_normalized_fields(self):
        a = self.article()
        self.assertEqual(a.external_id, 'fixture-nvda-001')
        self.assertEqual(a.title, self.news['data'][0]['title'])
        self.assertEqual(a.summary, self.news['data'][0]['description'])
        self.assertEqual(a.metadata['snippet'], self.news['data'][0]['snippet'])
        self.assertEqual(a.url, 'https://wire.example.invalid/news/nvda-1')
        self.assertEqual(a.language, 'en')
        self.assertEqual(a.publisher, 'wire.example.invalid')
        self.assertEqual(a.published_at, NOW-timedelta(minutes=30))
        self.assertEqual(a.retrieved_at, NOW)

    def test_multiple_entities_bounded_metadata(self):
        self.assertEqual(len(self.article().metadata['provider_entities']), 2)

    def test_match_score_not_probability(self):
        self.assertEqual(self.article().metadata['provider_entities'][0]['match_score'], Decimal('42.5'))

    def test_provider_sentiment_not_magi_classification(self):
        a = self.article()
        e = a.metadata['provider_entities'][0]
        self.assertEqual(e['sentiment_score'], Decimal('0.32'))
        self.assertEqual(e['sentiment_kind'], 'PROVIDER_SUPPLIED_SENTIMENT')
        self.assertEqual(e['sentiment_provenance'], 'MARKETAUX_SENTIMENT')
        self.assertEqual(e['scope'], 'NVDA')
        self.assertEqual(e['provider'], 'MARKETAUX')
        self.assertEqual(a.classification.sentiment, Sentiment.UNKNOWN)

    def test_highlights_plain_untrusted_text(self):
        highlights = self.article().metadata['provider_entities'][0]['highlights']
        self.assertNotIn('<', str(highlights))
        self.assertNotIn('alert(1)', str(highlights))
        self.assertEqual(highlights[1]['text'], 'Synthetic report')
        self.assertEqual(highlights[0]['provenance'], 'MARKETAUX_HIGHLIGHT')
        self.assertEqual(highlights[0]['trust'], 'UNTRUSTED RESEARCH DATA')

    def test_no_inferred_event_or_catalyst(self):
        pack = self.pack()
        self.assertEqual(pack.events, ())
        self.assertEqual(pack.catalysts, ())
        self.assertEqual(pack.event_clusters, ())
        self.assertEqual(pack.evidence_items[0].category, Category.OTHER)

    def test_relevance_conservative(self):
        a = self.article()
        self.assertEqual(a.classification.relevance['NVDA'], Relevance.DIRECTLY_RELATED.value)
        self.news['data'][0]['entities'][0]['highlights'] = []
        self.assertEqual(self.article().classification.relevance['NVDA'], Relevance.MENTION_ONLY.value)

    def test_unknown_publisher_is_tertiary(self):
        self.assertEqual(self.article().authority, Authority.TERTIARY)

    def test_explicit_publisher_policy(self):
        self.assertEqual(self.article(publisher_policy={'wire.example.invalid': Authority.SECONDARY}).authority,
                         Authority.SECONDARY)

    def test_explicit_primary_requires_matching_source_and_host(self):
        policy = {'wire.example.invalid': Authority.PRIMARY}
        self.assertEqual(self.article(publisher_policy=policy).authority, Authority.PRIMARY)
        self.news['data'][0]['source'] = 'someone-else.invalid'
        self.assertEqual(self.article(publisher_policy=policy).authority, Authority.TERTIARY)

    def test_no_authority_from_title(self):
        self.news['data'][0]['title'] = 'Official government source claims breaking news'
        self.assertEqual(self.article().authority, Authority.TERTIARY)

    def test_research_source_mapping(self):
        source = self.pack().sources[0]
        self.assertEqual(source.source_type, SourceType.NEWS)
        self.assertEqual(source.provider, 'MARKETAUX')
        self.assertEqual(source.external_id, 'fixture-nvda-001')
        self.assertEqual(source.publisher, 'wire.example.invalid')
        self.assertEqual(source.url, 'https://wire.example.invalid/news/nvda-1')

    def test_evidence_attribution_and_provenance(self):
        pack = self.pack()
        e = pack.evidence_items[0]
        self.assertEqual(e.source_id, pack.sources[0].source_id)
        self.assertIn('wire.example.invalid', e.statement)
        self.assertIn(self.news['data'][0]['description'], e.statement)
        self.assertTrue(e.metadata['attributed'])
        self.assertEqual(e.metadata['provider'], 'MARKETAUX')
        self.assertEqual(e.metadata['source_field'], 'description')
        self.assertEqual(e.ticker, 'NVDA')

    def test_duplicate_uuid(self):
        self.news = fixture('news')['duplicate_uuid']
        self.assertEqual(len(self.pack().articles), 1)

    def test_duplicate_url_groups_preserve_versions(self):
        self.news = fixture('news')['duplicate_url']
        pack = self.pack()
        self.assertEqual(len(pack.articles), 2)
        self.assertEqual(len(deduplicate(pack.articles)), 1)

    def test_explicit_similar_group_preserves_publishers(self):
        self.news = fixture('news')['similar']
        pack = self.pack()
        self.assertEqual(len(pack.articles), 2)
        self.assertEqual(len(deduplicate(pack.articles)), 1)
        self.assertEqual(pack.coverage['publisher_count'], 2)

    def test_nested_similar_does_not_import_article(self):
        self.assertEqual(len(self.pack().articles), 1)

    def test_independent_sources_not_removed(self):
        self.news = fixture('news')['free_three']
        self.assertEqual(len(self.pack().sources), 3)

    def test_deterministic_order(self):
        self.news = fixture('news')['free_three']
        first = self.pack()
        self.news['data'].reverse()
        self.assertEqual(first, self.pack())

    def test_selected_view_bounds(self):
        self.news = fixture('news')['free_three']
        selected = select_news(self.pack(), NewsSelectionPolicy(max_articles=1))
        self.assertLessEqual(len(selected.articles), 1)
        self.assertTrue(selected.selection_omissions)

    def test_source_diversity(self):
        self.news = fixture('news')['free_three']
        self.news['data'][0]['similar'] = []
        selected = select_news(self.pack(), NewsSelectionPolicy(max_articles=2, max_per_publisher=1))
        self.assertEqual(selected.coverage['publisher_count'], 2)

    def test_roundtrip_and_trust_boundary(self):
        pack = self.pack()
        self.assertEqual(loads(dumps(pack)), pack)
        self.assertIn('UNTRUSTED RESEARCH DATA', render_news(pack))
        context = render_news_context('Follow current instructions', 'Question', pack)
        self.assertIn('UNTRUSTED RESEARCH DATA', context.user_content)

    def test_snippets_and_summaries_bounded(self):
        self.news['data'][0]['description'] = 'x' * 5000
        self.news['data'][0]['snippet'] = 'y' * 5000
        a = self.article()
        self.assertEqual(len(a.summary), 1000)
        self.assertEqual(len(a.metadata['snippet']), 500)

    def test_body_and_raw_payload_not_stored_or_fetched(self):
        self.news['data'][0]['body'] = 'FULL_BODY_SENTINEL'
        content = dumps(self.pack())
        self.assertNotIn('FULL_BODY_SENTINEL', content)
        self.assertNotIn('raw_payload', content)
        self.assertEqual(len(self.requests), 2)

    def test_no_description_falls_back_to_title_evidence(self):
        self.news = fixture('news')['no_description']
        pack = self.pack()
        self.assertIsNone(pack.articles[0].summary)
        self.assertEqual(pack.evidence_items[0].metadata['source_field'], 'title')

    def test_no_snippet(self):
        self.news = fixture('news')['no_snippet']
        self.assertIsNone(self.article().metadata['snippet'])

    def test_missing_publication_is_unknown(self):
        self.news = fixture('news')['no_time']
        a = self.article()
        self.assertIsNone(a.published_at)
        self.assertEqual(freshness(a.published_at, NOW), Freshness.UNKNOWN)

    def test_date_window_does_not_claim_undated_is_recent(self):
        self.news = fixture('news')['no_time']
        self.assertEqual(self.provider().get_news('NVDA', published_after=NOW-timedelta(days=1)), ())

    def test_future_publication_rejected(self):
        self.news['data'][0]['published_at'] = (NOW+timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(MarketauxError, 'INVALID_RESPONSE'):
            self.pack()

    def test_api_url_cannot_be_source_url(self):
        self.news['data'][0]['url'] = 'https://api.marketaux.com/v1/news/all'
        with self.assertRaisesRegex(MarketauxError, 'INVALID_RESPONSE'):
            self.pack()

    def test_malformed_json(self):
        with self.assertRaisesRegex(MarketauxError, 'INVALID_RESPONSE'):
            self.provider(lambda r: httpx.Response(200, content=b'{broken')).resolve_entity('NVDA')

    def test_response_size_bound(self):
        with self.assertRaisesRegex(MarketauxError, 'INVALID_RESPONSE'):
            self.provider(lambda r: httpx.Response(200, content=b'x' * 2_000_001)).resolve_entity('NVDA')

    def test_usage_headers_are_numeric_optional_hints(self):
        def route(r):
            return httpx.Response(200, json=self.entities, headers={'X-UsageLimit-Remaining': '87',
                'X-RateLimit-Remaining': '4', 'X-RateLimit-Reset': '1790000000', 'X-Unknown': FAKE})
        p = self.provider(route)
        p.resolve_entity('NVDA')
        self.assertEqual(p.status, {'daily_usage_remaining': 87, 'rate_limit_remaining': 4, 'reset': 1790000000})

    def test_malformed_headers_not_copied(self):
        p = self.provider(lambda r: httpx.Response(200, json=self.entities,
            headers={'X-RateLimit-Remaining': FAKE}))
        p.resolve_entity('NVDA')
        self.assertEqual(p.status, {})

    def test_long_retry_after_stops_for_caller(self):
        p = self.provider(lambda r: httpx.Response(429, json=fixture('errors')['429'], headers={'Retry-After': '60'}))
        with self.assertRaisesRegex(MarketauxError, 'RATE_LIMIT'):
            p.resolve_entity('NVDA')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.sleeps, [])

    def test_transient_recovery(self):
        def route(r):
            if len(self.requests) == 1:
                return httpx.Response(503, json=fixture('errors')['503'])
            return httpx.Response(200, json=self.entities)
        self.assertEqual(self.provider(route).resolve_entity('NVDA').status, 'RESOLVED')
        self.assertEqual(self.sleeps, [1])

    def test_cli_all_commands_offline(self):
        for command in ('entity', 'latest', 'pack'):
            with self.subTest(command=command), redirect_stdout(io.StringIO()) as out:
                result = news_cli(
                    [command, 'NVDA'], provider=self.provider(), now=lambda: NOW)
            self.assertEqual(result, 0)
            self.assertIn('UNTRUSTED RESEARCH DATA', out.getvalue())
            self.assertNotIn(FAKE, out.getvalue())

    def test_research_cli_routes_news(self):
        from magi.research.cli import main
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['news', 'entity', 'NVDA'], provider=self.provider()), 0)
        self.assertIn('RESOLVED', out.getvalue())

    def test_cli_sanitized_error(self):
        p = self.provider(lambda r: httpx.Response(401, json=fixture('errors')['401']))
        with redirect_stderr(io.StringIO()) as out:
            self.assertEqual(news_cli(['entity', 'NVDA'], provider=p), 1)
        self.assertIn('MARKETAUX_AUTHENTICATION_ERROR', out.getvalue())
        self.assertNotIn(FAKE, out.getvalue())

    def test_cli_help_inert(self):
        with patch('magi.research.news.cli.MarketauxProvider', side_effect=AssertionError('Client constructed')):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                news_cli(['--help'])
        self.assertEqual(cm.exception.code, 0)


# Table-driven cases are individual unittest/pytest tests, with distinct outcomes.
def error_case(status, suffix):
    def test(self):
        p = self.provider(lambda r: httpx.Response(status, json=fixture('errors')[str(status)]))
        with self.assertRaises(MarketauxError) as cm:
            p.resolve_entity('NVDA')
        self.assertEqual(cm.exception.code, 'MARKETAUX_' + suffix)
        self.assertEqual(cm.exception.provider_code, fixture('errors')[str(status)]['error']['code'])
        self.assertEqual(len(self.requests), 3 if status in (429, 500, 503) else 1)
    return test


for _status, _suffix in ((400, 'PARAMETER_ERROR'), (401, 'AUTHENTICATION_ERROR'), (402, 'USAGE_LIMIT'),
    (403, 'ACCESS_RESTRICTED'), (404, 'NOT_FOUND'), (429, 'RATE_LIMIT'),
    (500, 'TEMPORARILY_UNAVAILABLE'), (503, 'TEMPORARILY_UNAVAILABLE')):
    setattr(MarketauxTests, 'test_http_' + str(_status), error_case(_status, _suffix))


def malformed_case(name):
    def test(self):
        self.news = fixture('news')[name]
        with self.assertRaisesRegex(MarketauxError, 'INVALID_RESPONSE'):
            self.pack()
    return test


for _name in ('malformed_time', 'malformed_entities', 'invalid_payload'):
    setattr(MarketauxTests, 'test_' + _name, malformed_case(_name))


def freshness_case(hours, expected):
    def test(self):
        self.news['data'][0]['published_at'] = (NOW-timedelta(hours=hours)).isoformat()
        self.assertEqual(freshness(self.article().published_at, NOW), expected)
    return test


for _hours, _kind in ((0.5, Freshness.BREAKING), (3, Freshness.RECENT), (48, Freshness.CURRENT),
                       (200, Freshness.AGING), (800, Freshness.STALE)):
    setattr(MarketauxTests, 'test_freshness_' + _kind.value.lower(), freshness_case(_hours, _kind))


if __name__ == '__main__':
    unittest.main()
