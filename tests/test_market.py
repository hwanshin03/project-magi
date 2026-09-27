"""Deterministic Toss wire fixtures; no real credentials or sockets."""
import io
import logging
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from magi.market.base import MarketError
from magi.market.cli import main as market_cli
from magi.market.models import ErrorCode, convert
from magi.market.service import MarketDataService
from magi.market.toss import TossProvider
from magi.market.valuation import PortfolioValuationService
from magi.portfolio import Portfolio
from magi.storage import Database

NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def token():
    return {'access_token': 'fake-token-only', 'token_type': 'Bearer', 'expires_in': 100}


def quote(symbol='NVDA', currency='USD', price='184.20'):
    return {'result': [{'symbol': symbol, 'currency': currency, 'lastPrice': price, 'timestamp': NOW.isoformat()}]}


def candle(day=26, currency='USD'):
    return {'timestamp': f'2026-09-{day:02d}T00:00:00+09:00', 'currency': currency,
            'openPrice': '180.10', 'highPrice': '190', 'lowPrice': '179', 'closePrice': '184.20', 'volume': '12345'}


def fx(base='USD', quote_currency='KRW', rate='1359'):
    return {'result': {'baseCurrency': base, 'quoteCurrency': quote_currency, 'rate': rate,
                       'validFrom': NOW.isoformat(), 'validUntil': (NOW + timedelta(minutes=1)).isoformat()}}


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.elapsed = 0
        self.requests = []
        self.sleeps = []
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def provider(self, responses):
        queue = iter(responses)
        def handler(request):
            self.requests.append(request)
            item = next(queue)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, httpx.Response):
                return item
            return httpx.Response(200, json=item)
        provider = TossProvider(client_id='fake-client', client_secret='fake-secret',
            transport=httpx.MockTransport(handler), clock=lambda: self.elapsed,
            now=lambda: NOW + timedelta(seconds=self.elapsed), sleep=self.sleep, jitter=lambda: 0)
        self.addCleanup(provider.close)
        return provider

    def service(self, provider):
        return MarketDataService(provider, clock=lambda: self.elapsed, now=lambda: NOW + timedelta(seconds=self.elapsed))

    def test_token_parsing_reuse_and_form(self):
        p = self.provider([token(), quote(), quote()])
        p.get_quote('NVDA', 'US'); p.get_quote('NVDA', 'US')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[0].url.path, '/oauth2/token')
        self.assertIn(b'grant_type=client_credentials', self.requests[0].content)
        self.assertEqual(self.requests[1].headers['Authorization'], 'Bearer fake-token-only')
        self.assertNotIn('fake-secret', repr(p))

    def test_expired_token_reauth(self):
        p = self.provider([token(), quote(), token(), quote()])
        p.get_quote('NVDA', 'US')
        self.elapsed = 100
        p.get_quote('NVDA', 'US')
        self.assertEqual(sum(r.url.path == '/oauth2/token' for r in self.requests), 2)

    def test_401_refresh_once(self):
        p = self.provider([token(), httpx.Response(401), token(), quote()])
        self.assertEqual(p.get_quote('NVDA', 'US').price, Decimal('184.20'))
        self.assertEqual(len(self.requests), 4)

    def test_repeated_401_stops(self):
        p = self.provider([token(), httpx.Response(401), token(), httpx.Response(401)])
        self.assertEqual(self.service(p).get_quote('NVDA', 'US').error, ErrorCode.AUTHENTICATION)
        self.assertEqual(len(self.requests), 4)

    def test_auth_sanitization_no_output(self):
        out = io.StringIO()
        p = self.provider([httpx.Response(401, text='fake-secret fake-token-only')])
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        logger = logging.getLogger('httpx')
        prior_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            with redirect_stdout(out), redirect_stderr(out):
                result = self.service(p).get_quote('NVDA', 'US')
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prior_level)
        self.assertEqual(result.error, ErrorCode.AUTHENTICATION)
        self.assertEqual(out.getvalue(), '')
        for secret in ('fake-client', 'fake-secret', 'fake-token-only'):
            self.assertNotIn(secret, repr(result) + logs.getvalue())

    def test_missing_credentials_never_requests(self):
        p = self.provider([])
        p._client_id = None
        self.assertEqual(self.service(p).get_quote('NVDA', 'US').error, ErrorCode.AUTHENTICATION)
        self.assertEqual(self.requests, [])

    def test_malformed_token(self):
        for change in ({'expires_in': -1}, {'expires_in': True}, {'token_type': 'wrong'}, {'access_token': 'bad\nheader'}):
            with self.subTest(change=change):
                p = self.provider([dict(token(), **change)])
                self.assertEqual(self.service(p).get_quote('NVDA', 'US').error, ErrorCode.INVALID_RESPONSE)

    def test_us_quote_normalization_and_optional_fields(self):
        q = self.provider([token(), quote()]).get_quote('nvda', 'us')
        self.assertEqual((q.symbol, q.market, q.currency), ('NVDA', 'US', 'USD'))
        self.assertEqual(q.price, Decimal('184.20'))
        for field in ('asset_name', 'previous_close', 'absolute_change', 'percent_change', 'open', 'high', 'low', 'volume'):
            self.assertIsNone(getattr(q, field))
        with self.assertRaises(FrozenInstanceError):
            q.price = Decimal('1')

    def test_kr_quote_keeps_zeroes(self):
        q = self.provider([token(), quote('005930', 'KRW', '85000')]).get_quote('005930', 'KR')
        self.assertEqual((q.symbol, q.currency, q.price), ('005930', 'KRW', Decimal('85000')))

    def test_missing_timestamp_is_stale(self):
        data = quote(); data['result'][0].pop('timestamp')
        q = self.service(self.provider([token(), data])).get_quote('NVDA', 'US').data
        self.assertTrue(q.is_stale)
        self.assertIsNone(q.timestamp)

    def test_old_source_timestamp_is_stale_even_new_fetch(self):
        data = quote(); data['result'][0]['timestamp'] = (NOW - timedelta(hours=1)).isoformat()
        service = self.service(self.provider([token(), data]))
        self.assertTrue(service.get_quote('NVDA', 'US').data.is_stale)
        service.get_quote('NVDA', 'US')
        self.assertEqual(len(self.requests), 2)

    def test_explicit_market_currency_mismatch_rejected(self):
        result = self.service(self.provider([token(), quote('005930', 'KRW')])).get_quote('005930', 'US')
        self.assertEqual(result.error, ErrorCode.INVALID_RESPONSE)

    def test_invalid_quote_fields(self):
        for data in ({}, {'result': []}, {'result': {}}, quote(price='NaN'), quote(price='-1'), quote(price=1.2)):
            with self.subTest(data=data):
                self.assertEqual(self.service(self.provider([token(), data])).get_quote('NVDA', 'US').error, ErrorCode.INVALID_RESPONSE)

    def test_batch_normalization(self):
        data = {'result': quote()['result'] + quote('005930', 'KRW')['result']}
        quotes = self.provider([token(), data]).get_quotes([('nvda','US'),('005930','KR')])
        self.assertEqual([q.symbol for q in quotes], ['NVDA', '005930'])
        self.assertEqual(self.requests[-1].url.params['symbols'], 'NVDA,005930')

    def test_batch_chunking_internal(self):
        symbols = ['X' + str(i) for i in range(201)]
        pages = [{'result': [quote(s)['result'][0] for s in symbols[:200]]}, quote(symbols[200])]
        values = self.provider([token(), *pages]).get_quotes([(s, 'US') for s in symbols])
        self.assertEqual(len(values), 201)
        self.assertEqual(len(self.requests), 3)

    def test_daily_candles_sorted_decimal_volume(self):
        data = {'result': {'candles': [candle(26), candle(25)], 'nextBefore': None}}
        series = self.provider([token(), data]).get_daily_candles('nvda', 'US')
        self.assertEqual([c.timestamp.day for c in series.candles], [25, 26])
        self.assertEqual(series.candles[0].volume, Decimal('12345'))
        self.assertEqual(series.candles[0].open, Decimal('180.10'))
        self.assertEqual(self.requests[-1].url.params['adjusted'], 'true')

    def test_minute_candles_and_unsupported_interval(self):
        p = self.provider([token(), {'result': {'candles': [candle()]}}])
        self.assertEqual(p.get_minute_candles('NVDA', 'US').interval, '1m')
        with self.assertRaises(MarketError) as error:
            p.get_minute_candles('NVDA', 'US', interval='5m')
        self.assertEqual(error.exception.code, ErrorCode.UNSUPPORTED)

    def test_empty_candles(self):
        result = self.service(self.provider([token(), {'result': {'candles': []}}])).get_daily_candles('NVDA','US')
        self.assertEqual(result.data.candles, ())
        self.assertIsNone(result.error)

    def test_malformed_candles(self):
        for data in ({'result': {}}, {'result': {'candles': [{}]}}, {'result': {'candles': [dict(candle(), highPrice='1')]}}):
            with self.subTest(data=data):
                self.assertEqual(self.service(self.provider([token(),data])).get_daily_candles('NVDA','US').error, ErrorCode.INVALID_RESPONSE)

    def test_candle_pagination_deduplicates_boundary(self):
        a = {'result': {'candles': [candle(26), candle(25)], 'nextBefore': candle(25)['timestamp']}}
        b = {'result': {'candles': [candle(25), candle(24)], 'nextBefore': None}}
        result = self.provider([token(),a,b]).get_daily_candles('NVDA','US',3)
        self.assertEqual([c.timestamp.day for c in result.candles], [24,25,26])
        self.assertEqual(self.requests[-1].url.params['before'], candle(25)['timestamp'])

    def test_candle_nonadvancing_pagination_fails(self):
        page = {'result': {'candles': [candle()], 'nextBefore': candle()['timestamp']}}
        result = self.service(self.provider([token(),page,page])).get_daily_candles('NVDA','US',3)
        self.assertEqual(result.error, ErrorCode.INVALID_RESPONSE)

    def test_fx_decimal_direction_and_conversion(self):
        rate = self.provider([token(), fx()]).get_fx_rate('usd','krw')
        self.assertEqual(rate.rate, Decimal('1359'))
        self.assertEqual(convert(Decimal('184.20'), 'USD', rate), Decimal('250327.80'))
        with self.assertRaises(ValueError):
            convert(1, 'KRW', rate)

    def test_reverse_fx_is_requested_not_inverted(self):
        rate = self.provider([token(), fx('KRW','USD','0.0007')]).get_fx_rate('KRW','USD')
        self.assertEqual(rate.rate, Decimal('.0007'))
        self.assertEqual(self.requests[-1].url.params['baseCurrency'], 'KRW')

    def test_fx_wrong_direction_or_zero_rejected(self):
        for data in (fx('KRW','USD'), fx(rate='0')):
            self.assertEqual(self.service(self.provider([token(),data])).get_fx_rate('USD','KRW').error, ErrorCode.INVALID_RESPONSE)

    def test_fx_unavailable(self):
        self.assertEqual(self.service(self.provider([token(),httpx.Response(404)])).get_fx_rate('USD','KRW').error, ErrorCode.NOT_FOUND)

    def test_cache_hit_expiry(self):
        service = self.service(self.provider([token(),quote(),quote(price='185')]))
        first = service.get_quote('NVDA','US').data
        self.assertEqual(service.get_quote('nvda','us').data, first)
        self.assertEqual(len(self.requests), 2)
        self.elapsed += 16
        self.assertEqual(service.get_quote('NVDA','US').data.price, Decimal('185'))

    def test_stale_fallback_preserves_fetch_time(self):
        service = self.service(self.provider([token(),quote(), *[httpx.Response(503) for _ in range(3)]]))
        first = service.get_quote('NVDA','US').data
        self.elapsed += 16
        result = service.get_quote('NVDA','US')
        self.assertTrue(result.data.is_stale)
        self.assertEqual(result.data.fetched_at, first.fetched_at)
        self.assertEqual(result.error, ErrorCode.UNAVAILABLE)

    def test_fx_validity_expiry_and_stale_fallback(self):
        service = self.service(self.provider([token(),fx(),httpx.Response(404)]))
        first = service.get_fx_rate('USD','KRW').data
        self.elapsed += 61
        result = service.get_fx_rate('USD','KRW')
        self.assertTrue(result.data.is_stale)
        self.assertEqual(result.data.rate, first.rate)

    def test_retry_statuses_bounded(self):
        for status in (429,500,502,503,504):
            with self.subTest(status=status):
                before = len(self.requests)
                p = self.provider([token(), *[httpx.Response(status) for _ in range(3)]])
                result = self.service(p).get_quote('NVDA','US')
                self.assertEqual(result.error, ErrorCode.RATE_LIMITED if status==429 else ErrorCode.UNAVAILABLE)
                self.assertIsNone(result.data)
                self.assertEqual(len(self.requests)-before, 4)

    def test_retry_success_and_timeout(self):
        for error in (httpx.ReadTimeout('fake-secret'), httpx.ConnectError('fake-secret'), httpx.RemoteProtocolError('fake-secret')):
            result = self.service(self.provider([token(),error,quote()])).get_quote('NVDA','US')
            self.assertIsNotNone(result.data)

    def test_network_exhaustion_sanitized(self):
        result = self.service(self.provider([httpx.ConnectError('fake-secret')]*3)).get_quote('NVDA','US')
        self.assertEqual(result.error, ErrorCode.UNAVAILABLE)
        self.assertNotIn('fake-secret', repr(result))

    def test_retry_after_respected_or_long_wait_declined(self):
        p = self.provider([token(),httpx.Response(429,headers={'Retry-After':'4'}),quote()])
        p.get_quote('NVDA','US')
        self.assertIn(4, self.sleeps)
        p = self.provider([token(),httpx.Response(429,headers={'Retry-After':'60'})])
        self.assertEqual(self.service(p).get_quote('NVDA','US').error, ErrorCode.RATE_LIMITED)

    def test_malformed_json_no_retries(self):
        result = self.service(self.provider([token(),httpx.Response(200,content=b'not-json fake-secret')])).get_quote('NVDA','US')
        self.assertEqual(result.error, ErrorCode.INVALID_RESPONSE)
        self.assertEqual(len(self.requests), 2)

    def test_programming_errors_propagate(self):
        p = Mock(); p.get_quote.side_effect = RuntimeError('bug')
        with self.assertRaises(RuntimeError):
            self.service(p).get_quote('NVDA','US')

    def test_endpoint_allowlist_and_redirect(self):
        p = self.provider([token(),httpx.Response(302,headers={'Location':'https://example.com'})])
        self.assertEqual(self.service(p).get_quote('NVDA','US').error, ErrorCode.UNAVAILABLE)
        for method, path in [('POST','/api/v1/orders'),('GET','/api/v1/accounts')]:
            with self.assertRaises(ValueError):
                p._send(method,path)
        self.assertEqual(len(self.requests), 2)

    def test_cli_commands_and_help(self):
        for args, data in [(['quote','nvda','--market','US'],quote()),
                           (['quote','005930','--market','KR'],quote('005930','KRW')),
                           (['fx','USD','KRW'],fx()),
                           (['history','NVDA','--market','US','--days','30'],{'result':{'candles':[candle()]}}),
                           (['candles','NVDA','--market','US','--interval','1m'],{'result':{'candles':[]}})]:
            with self.subTest(args=args), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(market_cli(args,service=self.service(self.provider([token(),data]))), 0)
                self.assertIn('fetched_at',out.getvalue())
        with patch('magi.market.cli.TossProvider', side_effect=AssertionError('No client')), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                market_cli(['--help'])
            self.assertEqual(error.exception.code, 0)

    def test_cli_failure_and_dispatch(self):
        import main
        service = self.service(self.provider([httpx.Response(401,text='fake-secret')]))
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(market_cli(['quote','NVDA','--market','US'],service=service),1)
        self.assertNotIn('fake-secret',err.getvalue())
        with patch('magi.market.cli.main',return_value=0) as routed:
            self.assertEqual(main.cli(['market','fx','USD','KRW']),0)
            routed.assert_called_once_with(['fx','USD','KRW'])

    def portfolio(self, symbol='NVDA', currency='USD', market='US', price='180'):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        portfolio = Portfolio(Database(Path(directory.name)/'test.db'))
        portfolio.record_transaction(symbol,'BUY','10',price,currency=currency,market=market)
        return portfolio

    def test_portfolio_us_gain_loss_no_ledger_changes(self):
        for price, expected in [('184.20','42.00'),('170','-100')]:
            portfolio = self.portfolio()
            before = portfolio.get_transactions()
            valuation = PortfolioValuationService(portfolio,self.service(self.provider([token(),quote(price=price)])))
            result = valuation.get_position_with_market_data('NVDA',market='US')
            self.assertEqual(result.position.unrealized_pnl,Decimal(expected))
            with localcontext() as context:
                context.prec = 80
                self.assertEqual(result.position.unrealized_return,Decimal(expected)/Decimal('1800'))
            self.assertEqual(portfolio.get_transactions(),before)
            self.assertIsNone(portfolio.get_position('NVDA').current_price)

    def test_portfolio_kr_native(self):
        portfolio = self.portfolio('005930','KRW','KR','80000')
        valuation = PortfolioValuationService(portfolio,self.service(self.provider([token(),quote('005930','KRW','85000')])))
        result = valuation.get_position_with_market_data('005930',market='KR')
        self.assertEqual(result.position.unrealized_pnl,Decimal('50000'))
        self.assertEqual(result.position.currency,'KRW')

    def test_portfolio_display_fx_separate_native(self):
        portfolio = self.portfolio()
        valuation = PortfolioValuationService(portfolio,self.service(self.provider([token(),quote(),fx()])))
        result = valuation.get_position_with_market_data('NVDA',market='US',display_currency='KRW')
        self.assertEqual(result.display_price,Decimal('250327.80'))
        self.assertEqual(result.display_market_value,Decimal('2503278.00'))
        self.assertEqual(result.position.currency,'USD')
        self.assertEqual(result.position.current_price,Decimal('184.20'))

    def test_portfolio_fx_unavailable_keeps_native(self):
        valuation = PortfolioValuationService(self.portfolio(),self.service(self.provider([token(),quote(),httpx.Response(404)])))
        result = valuation.get_position_with_market_data('NVDA',market='US',display_currency='KRW')
        self.assertEqual(result.position.market_value,Decimal('1842.00'))
        self.assertIsNone(result.display_market_value)
        self.assertEqual(result.fx_error,ErrorCode.NOT_FOUND)

    def test_portfolio_quote_unavailable_keeps_state(self):
        portfolio = self.portfolio(); before = portfolio.get_position('NVDA')
        valuation = PortfolioValuationService(portfolio,self.service(self.provider([httpx.Response(401)])))
        result = valuation.get_position_with_market_data('NVDA',market='US')
        self.assertEqual(result.position,before)
        self.assertIsNone(result.position.current_price)

    def test_service_batch_uses_cached_and_missing_quotes(self):
        service = self.service(self.provider([token(),quote(),quote('AAPL')]))
        first = service.get_quote('NVDA','US').data
        batch = service.get_quotes([('nvda','US'),('AAPL','US')])
        self.assertEqual(batch[0].data,first)
        self.assertEqual(batch[1].data.symbol,'AAPL')
        self.assertEqual(self.requests[-1].url.params['symbols'],'AAPL')

    def test_service_batch_outage_preserves_cached_other_agent_data(self):
        service = self.service(self.provider([token(),quote(),httpx.Response(404)]))
        service.get_quote('NVDA','US')
        batch = service.get_quotes([('NVDA','US'),('AAPL','US')])
        self.assertIsNotNone(batch[0].data)
        self.assertIsNone(batch[1].data)
        self.assertEqual(batch[1].error,ErrorCode.NOT_FOUND)

    def test_cache_capacity_and_batch_eviction(self):
        p = self.provider([token(),quote(),quote('AAPL'),quote()])
        service = MarketDataService(p,clock=lambda:self.elapsed,now=lambda:NOW,max_entries=1)
        service.get_quote('NVDA','US')
        batch = service.get_quotes([('AAPL','US'),('NVDA','US')])
        self.assertEqual([r.data.symbol for r in batch],['AAPL','NVDA'])
        self.assertEqual(len(service._cache),1)
        service.get_quote('NVDA','US')
        self.assertEqual(len(self.requests),4)

    def test_long_rate_limit_cooldown_applies_to_next_call(self):
        p = self.provider([token(),httpx.Response(429,headers={'Retry-After':'60'})])
        service = self.service(p)
        service.get_quote('NVDA','US')
        self.assertEqual(service.get_quote('NVDA','US').error,ErrorCode.RATE_LIMITED)
        self.assertEqual(len(self.requests),2)

    def test_advertised_limit_and_reset_respected(self):
        response = httpx.Response(200,json=quote(),headers={
            'X-RateLimit-Limit':'1','X-RateLimit-Remaining':'0','X-RateLimit-Reset':'3'})
        p = self.provider([token(),response,quote()])
        p.get_quote('NVDA','US'); p.get_quote('NVDA','US')
        self.assertIn(3,self.sleeps)

    def test_stale_valuation_explicit(self):
        service = self.service(self.provider([token(),quote(),httpx.Response(404)]))
        service.get_quote('NVDA','US')
        self.elapsed += 16
        result = PortfolioValuationService(self.portfolio(),service).get_position_with_market_data('NVDA',market='US')
        self.assertTrue(result.is_stale)
        self.assertTrue(result.quote.is_stale)
        self.assertIsNotNone(result.position.market_value)

    def test_invalid_cli_input_never_requests(self):
        p = self.provider([])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(market_cli(['history','NVDA','--market','US','--days','0'],service=self.service(p)),2)
        self.assertEqual(self.requests,[])

    def test_service_minute_rejects_daily_interval_without_request(self):
        service = self.service(self.provider([]))
        self.assertEqual(service.get_minute_candles('NVDA','US',interval='1d').error,ErrorCode.UNSUPPORTED)
        self.assertEqual(self.requests,[])

    def test_conversion_supports_large_ledger_values(self):
        rate = self.provider([token(),fx()]).get_fx_rate('USD','KRW')
        value = Decimal('1234567890123456789012345678901234567890')
        with localcontext() as context:
            context.prec = 80
            self.assertEqual(convert(value,'USD',rate),value * rate.rate)

    def test_localized_labels_do_not_change_identifiers(self):
        from magi.market.presentation import LABELS
        self.assertEqual(LABELS['ko']['price'],'현재가')
        self.assertEqual(LABELS['en']['price'],'Current Price')


if __name__ == '__main__':
    unittest.main()
