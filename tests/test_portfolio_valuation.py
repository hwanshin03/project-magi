"""Offline valuation, query-only ledger, and presentation contract tests."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace, FrozenInstanceError
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D, localcontext
from pathlib import Path
from unittest.mock import Mock, patch
from magi.accounts import PortfolioAccountIdentity
from magi.broker.ledger import ReadOnlyLedgerDatabase
from magi.broker.models import BrokerAccount, AccountList, BrokerResult, HoldingsSnapshot, Holding
from magi.market.models import Quote, FXRate, MarketResult, ErrorCode
from magi.portfolio import Portfolio
from magi.portfolio_valuation import PortfolioValuationService
from magi.portfolio_live_cli import main as live_cli
from magi.storage import Database
from magi.valuation_presentation import render, money, percentage, gain_state, classify

NOW = datetime(2026,9,27,tzinfo=timezone.utc)


class ValuationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'ledger.db'
        self.portfolio = Portfolio(Database(self.path))
        self.market = Mock()
        self.quotes = {}
        self.market.get_quote.side_effect = lambda symbol,market: self.quotes.get(symbol,MarketResult(error=ErrorCode.UNAVAILABLE))
        self.market.get_fx_rate.side_effect = lambda base,quote: MarketResult(FXRate(base,quote,
            D('1400') if base=='USD' else D('0.00071428571428571428571428571428571428571428571428571428571428571428571428571428571429'),
            NOW,'FAKE',NOW))
        self.account = BrokerAccount('synthetic','TOSS',NOW)
        self.identity = PortfolioAccountIdentity.from_broker(self.account)
        self.scope = dict(broker_provider='TOSS',broker_account_ref=self.identity.account_ref)
        for target in ('socket.socket.connect','socket.socket.connect_ex','socket.create_connection','socket.getaddrinfo'):
            guard=patch(target,side_effect=AssertionError('No network allowed'));guard.start();self.addCleanup(guard.stop)

    def add(self,symbol='NVDA',quantity='2',cost='100',price='120',currency='USD',opening=False):
        market='US' if currency=='USD' else 'KR'
        if opening:
            self.portfolio.record_opening_balance(symbol,quantity,cost,as_of=NOW,currency=currency,market=market,
                confirmed=True,asset_name='Asset '+symbol,**self.scope)
        else:
            self.portfolio.record_transaction(symbol,'BUY',quantity,cost,currency=currency,market=market,executed_at=NOW)
        self.quotes[symbol]=MarketResult(Quote(symbol,market,currency,D(price),NOW,'FAKE',NOW))

    def view(self,**kwargs):
        return PortfolioValuationService(self.portfolio,self.market,now=lambda:NOW).value(**kwargs)

    def test_usd_gain(self):
        self.add();p=self.view().positions[0]
        self.assertEqual((p.market_value,p.unrealized_pnl,p.unrealized_return),(D(240),D(40),D('.2')))

    def test_krw(self):
        self.add(currency='KRW');self.assertEqual(self.view().native_totals[0].currency,'KRW')

    def test_fractional_exact(self):
        self.add(quantity='0.000311',cost='227.263665',price='249.67')
        p=self.view().positions[0];self.assertEqual(p.remaining_book_cost,D('0.070678999815'))
        self.assertEqual(p.market_value,D('0.07764737'))

    def test_loss(self):
        self.add(price='80');self.assertEqual(self.view().positions[0].unrealized_pnl,D('-40'))

    def test_zero(self):
        self.add(price='100');self.assertEqual(self.view().positions[0].unrealized_return,D(0))

    def test_opening_unknown_history(self):
        self.add(opening=True);p=self.view().positions[0]
        self.assertIsNone(p.original_purchase_date);self.assertIsNone(p.pre_tracking_realized_pnl)
        self.assertEqual(p.history_completeness.value,'OPENING_BALANCE_HISTORY')
        self.assertIn('2026-09-27',p.tracking_start)

    def test_buy_purchase_known(self):
        self.add();self.assertIn('2026-09-27',self.view().positions[0].original_purchase_date)

    def test_later_sell_realized_separate(self):
        self.add(opening=True)
        self.portfolio.record_transaction('NVDA','SELL','1','130',currency='USD',market='US',executed_at=NOW+timedelta(days=1),**self.scope)
        p=self.view().positions[0]
        self.assertEqual((p.realized_pnl,p.unrealized_pnl),(D(30),D(20)))
        self.assertIsNone(p.pre_tracking_realized_pnl)

    def test_quote_unavailable(self):
        self.add();self.quotes.clear();p=self.view().positions[0]
        self.assertIsNone(p.market_value);self.assertEqual(p.remaining_book_cost,D(200))

    def test_one_quote_fails(self):
        self.add();self.add('KO');del self.quotes['KO']
        v=self.view();self.assertEqual(len(v.positions),2)
        self.assertEqual(v.positions[0].market_value,D(240));self.assertIsNone(v.native_totals[0].market_value)
        self.assertFalse(v.native_totals[0].complete)

    def test_all_quotes_fail(self):
        self.add();self.add('KO');self.quotes.clear()
        self.assertTrue(all(p.market_value is None for p in self.view().positions))

    def test_stale_quote(self):
        self.add();self.quotes['NVDA']=MarketResult(replace(self.quotes['NVDA'].data,is_stale=True))
        v=self.view();self.assertTrue(v.native_totals[0].is_stale)
        self.assertIn('STALE MARKET DATA',render(v));self.assertIn('오래된 시세 데이터',render(v,language='ko'))

    def test_wrong_quote_currency(self):
        self.add();self.quotes['NVDA']=MarketResult(replace(self.quotes['NVDA'].data,currency='KRW'))
        self.assertIsNone(self.view().positions[0].market_value)

    def test_invalid_float_price(self):
        self.add();self.quotes['NVDA']=MarketResult(replace(self.quotes['NVDA'].data,price=1.2))
        self.assertIsNone(self.view().positions[0].market_value)

    def test_usd_to_krw(self):
        self.add();p=self.view(display_currency='KRW').positions[0]
        self.assertEqual(p.converted_market_value,D(336000));self.assertEqual(p.current_price,D(120))
        self.assertEqual((p.fx_rate,p.fx_source,p.fx_timestamp),(D(1400),'FAKE',NOW))

    def test_krw_to_usd_direction(self):
        self.add(currency='KRW',price='1400');p=self.view(display_currency='USD').positions[0]
        self.market.get_fx_rate.assert_called_once_with('KRW','USD')
        with localcontext() as c:
            c.prec=80;self.assertEqual(p.converted_market_value,D(2800)*p.fx_rate)

    def test_loss_conversion_signed(self):
        self.add(price='80');self.assertEqual(self.view(display_currency='KRW').positions[0].converted_unrealized_pnl,D(-56000))

    def test_same_currency_no_fx(self):
        self.add();p=self.view(display_currency='USD').positions[0]
        self.assertEqual(p.converted_market_value,p.market_value);self.market.get_fx_rate.assert_not_called()

    def test_no_double_conversion(self):
        self.add();a=self.view(display_currency='KRW');b=self.view(display_currency='KRW')
        self.assertEqual(a,b);self.assertEqual(a.positions[0].remaining_book_cost,D(200))

    def test_fx_unavailable(self):
        self.add();self.market.get_fx_rate.side_effect=None;self.market.get_fx_rate.return_value=MarketResult(error=ErrorCode.UNAVAILABLE)
        v=self.view(display_currency='KRW');self.assertEqual(v.positions[0].market_value,D(240))
        self.assertIsNone(v.converted_total.market_value);self.assertIsNone(v.converted_total.book_cost)

    def test_fx_stale(self):
        self.add();self.market.get_fx_rate.side_effect=lambda a,b: MarketResult(FXRate(a,b,D(1400),NOW,'FAKE',NOW,is_stale=True))
        v=self.view(display_currency='KRW');self.assertTrue(v.converted_total.is_stale)
        self.assertIn('STALE FX DATA',render(v))

    def test_fx_wrong_direction(self):
        self.add();self.market.get_fx_rate.side_effect=lambda a,b: MarketResult(FXRate(b,a,D(1400),NOW,'FAKE',NOW))
        self.assertIsNone(self.view(display_currency='KRW').converted_total.market_value)

    def test_native_single_totals(self):
        for cur in ('USD','KRW'):
            with self.subTest(cur=cur):
                self.add(cur,currency=cur)
                total=next(t for t in self.view().native_totals if t.currency==cur)
                self.assertEqual((total.book_cost,total.market_value),(D(200),D(240)))

    def test_mixed_totals_no_fake_sum(self):
        self.add();self.add('KR',currency='KRW');v=self.view()
        self.assertEqual({t.currency for t in v.native_totals},{'USD','KRW'});self.assertIsNone(v.converted_total)

    def test_unified_krw(self):
        self.add();self.add('KR',currency='KRW');self.assertEqual(self.view(display_currency='KRW').converted_total.market_value,D(336240))

    def test_unified_usd(self):
        self.add();self.add('KR',currency='KRW');v=self.view(display_currency='USD')
        with localcontext() as c:
            c.prec=80;self.assertEqual(v.converted_total.market_value,sum((p.converted_market_value for p in v.positions),D(0)))

    def test_shared_fx_fetched_once(self):
        self.add();self.add('KO');self.view(display_currency='KRW');self.market.get_fx_rate.assert_called_once()

    def test_immutable_model(self):
        self.add()
        with self.assertRaises(FrozenInstanceError): self.view().positions[0].quantity=D(99)

    def test_en_ko_semantics(self):
        self.add(opening=True);v=self.view()
        for language,labels in [('en',('Current Price','Opening balance history','UNKNOWN','Realized P/L since tracking')),
                                ('ko',('현재가','초기 보유 잔고 기준','알 수 없음','MAGI 추적 시작'))]:
            for label in labels: self.assertIn(label,render(v,language=language))
        self.assertNotIn(self.identity.account_ref,render(v))

    def test_money_formats(self):
        self.assertEqual(money(D('225.07'),'USD'),'$225.07')
        self.assertEqual(money(D('130'),'USD',signed=True),'+$130.00')
        self.assertEqual(money(D('-42.18'),'USD',signed=True),'-$42.18')
        self.assertEqual(money(D('306200.49'),'KRW'),'₩306,200')
        self.assertEqual(money(D('-57400'),'KRW'),'-₩57,400')
        self.assertEqual(money(D('176900'),'KRW',signed=True),'+₩176,900')

    def test_percentage(self):
        self.assertEqual(percentage(D('.1656')),'+16.56%');self.assertEqual(percentage(D('-.0732')),'-7.32%')

    def test_gain_states(self):
        self.assertEqual([gain_state(x).value for x in (D(1),D(-1),D(0),None)],['POSITIVE','NEGATIVE','NEUTRAL','UNKNOWN'])

    def test_dust_filtered_only_in_presentation(self):
        self.add(quantity='.001');before=self.path.read_bytes();v=self.view()
        self.assertEqual(classify(v.positions[0],D(1)).value,'DUST')
        text=render(v,dust_threshold=D(1),hide_dust=True)
        self.assertNotIn('(NVDA)',text);self.assertIn('$0.12',text)
        self.assertEqual(v.native_totals[0].market_value,D('.120'));self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(len(self.portfolio.get_open_positions()),1)

    def test_dust_disabled(self):
        self.add(quantity='.001');self.assertEqual(classify(self.view().positions[0]).value,'NORMAL')

    def test_account_scope(self):
        self.add(opening=True);self.add('KO')
        self.assertEqual(len(self.view(**self.scope).positions),1)
        self.assertEqual(len(self.view().positions),2)

    def broker(self,quantity='2',stale=False):
        service=Mock();service.get_accounts.return_value=BrokerResult(AccountList((self.account,),NOW))
        h=Holding('NVDA','US','USD',D(quantity),'TOSS',self.account.account_id,NOW,average_cost=D(100))
        service.get_holdings.return_value=BrokerResult(HoldingsSnapshot((h,),(),'TOSS',self.account.account_id,NOW,is_stale=stale))
        return service

    def test_reconciliation_mismatch_does_not_replace_quantity(self):
        self.add(opening=True);v=PortfolioValuationService(self.portfolio,self.market,self.broker('3')).value()
        self.assertEqual(v.positions[0].quantity,D(2));self.assertEqual(v.positions[0].reconciliation,'QUANTITY_MISMATCH')
        self.assertIn('Portfolio reconciliation mismatch',render(v))

    def test_stale_reconciliation_warns(self):
        self.add(opening=True);v=PortfolioValuationService(self.portfolio,self.market,self.broker(stale=True)).value()
        self.assertEqual(v.positions[0].reconciliation,'UNVERIFIED');self.assertEqual(v.positions[0].market_value,D(240))

    def test_live_cli_readonly(self):
        self.add(opening=True);before=self.path.read_bytes();out=io.StringIO()
        with redirect_stdout(out):
            code=live_cli(['--language','ko','--currency','KRW'],portfolio=Portfolio(ReadOnlyLedgerDatabase(self.path)),market=self.market,broker=self.broker())
        self.assertEqual(code,0);self.assertIn('현재가',out.getvalue());self.assertEqual(before,self.path.read_bytes())

    def test_missing_database_not_created(self):
        missing=self.path.parent/'absent.db'
        with redirect_stderr(io.StringIO()):
            code=live_cli([],portfolio=Portfolio(ReadOnlyLedgerDatabase(missing)),market=self.market)
        self.assertEqual(code,1);self.assertFalse(missing.exists());self.market.get_quote.assert_not_called()

    def test_dispatch_live_only(self):
        from magi.portfolio_cli import main
        with patch('magi.portfolio_live_cli.main',return_value=0) as live:
            self.assertEqual(main(['portfolio','live','--language','ko']),0)
            live.assert_called_once_with(['--language','ko'],portfolio=None)

    def test_default_cli_shares_session(self):
        self.add(opening=True);output=io.StringIO()
        with patch('magi.toss.TossSession') as session, \
                patch('magi.market.toss.TossProvider') as market_provider, \
                patch('magi.market.service.MarketDataService',return_value=self.market), \
                patch('magi.broker.toss.TossBrokerProvider') as broker_provider, \
                patch('magi.broker.service.BrokerService',return_value=self.broker()), redirect_stdout(output):
            self.assertEqual(live_cli([],portfolio=self.portfolio),0)
            session.assert_called_once_with()
            shared=session.return_value.__enter__.return_value
            market_provider.assert_called_once_with(session=shared)
            broker_provider.assert_called_once_with(session=shared)
            session.return_value.__exit__.assert_called_once()

    def test_empty_live_never_constructs_provider(self):
        with patch('magi.toss.TossSession') as session, redirect_stdout(io.StringIO()):
            self.assertEqual(live_cli([],portfolio=self.portfolio),0)
            session.assert_not_called()

    def test_invalid_account_no_fetch(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(live_cli(['--broker','TOSS'],portfolio=self.portfolio,market=self.market),1)
        self.market.get_quote.assert_not_called()

    def test_quote_validation_exception_keeps_other_positions(self):
        self.add();self.add('KO')
        def quote(symbol,market):
            if symbol=='KO': raise ValueError('unsupported')
            return self.quotes[symbol]
        self.market.get_quote.side_effect=quote
        v=self.view();self.assertEqual(len(v.positions),2);self.assertIsNone(v.positions[1].current_price)

    def test_readonly_database_rejects_mutation(self):
        self.add();before=self.path.read_bytes()
        from magi.storage import StorageError
        with self.assertRaises(StorageError):
            with ReadOnlyLedgerDatabase(self.path).connect() as connection:
                connection.execute('DELETE FROM portfolio_transactions')
        self.assertEqual(self.path.read_bytes(),before)

    def test_fx_expired(self):
        self.add()
        self.market.get_fx_rate.side_effect=lambda a,b: MarketResult(FXRate(a,b,D(1400),NOW-timedelta(days=1),'FAKE',NOW,valid_until=NOW))
        self.assertTrue(self.view(display_currency='KRW').positions[0].fx_is_stale)

    def test_default_korean_krw_native_visible(self):
        self.add();out=io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(live_cli([],portfolio=self.portfolio,market=self.market),0)
        text=out.getvalue()
        self.assertIn('MAGI 포트폴리오',text);self.assertIn('$120.00',text);self.assertIn('약 ₩168,000',text)
        self.market.get_fx_rate.assert_called_once_with('USD','KRW')

    def test_explicit_language_currency_overrides(self):
        self.add()
        for language,cur in [('en','USD'),('ko','USD'),('en','KRW')]:
            self.market.get_fx_rate.reset_mock();out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(live_cli(['--language',language,'--currency',cur],portfolio=self.portfolio,market=self.market),0)
            self.assertIn('Current Price' if language=='en' else '현재가',out.getvalue())
            self.assertIn('$120.00',out.getvalue())
            if cur=='USD': self.market.get_fx_rate.assert_not_called()
            else: self.market.get_fx_rate.assert_called_once_with('USD','KRW')

    def test_native_krw_default_unchanged(self):
        self.add(currency='KRW');before=self.path.read_bytes();out=io.StringIO()
        with redirect_stdout(out):
            live_cli([],portfolio=self.portfolio,market=self.market)
        self.assertIn('현재가: ₩120',out.getvalue());self.market.get_fx_rate.assert_not_called()
        self.assertEqual(before,self.path.read_bytes())

    def test_toss_price_role_and_future_reference(self):
        from magi.portfolio_valuation import PriceRole
        self.add();self.quotes['NVDA']=MarketResult(replace(self.quotes['NVDA'].data,provider='TOSS'))
        broker=self.view().positions[0]
        self.assertEqual(broker.price_role,PriceRole.BROKER_PRICE)
        self.assertEqual(broker.market_data_provider,'TOSS')
        text=render(self.view(),language='ko')
        self.assertIn('가격 출처: 토스증권',text);self.assertIn('BROKER_PRICE',text)
        self.assertNotIn('REFERENCE_MARKET_PRICE',text)
        self.quotes['NVDA']=MarketResult(replace(self.quotes['NVDA'].data,provider='FUTURE'))
        self.assertEqual(self.view().positions[0].price_role,PriceRole.UNSPECIFIED)
        reference=PortfolioValuationService(self.portfolio,self.market,
            price_roles={'FUTURE':PriceRole.REFERENCE_MARKET_PRICE}).value().positions[0]
        self.assertEqual(reference.price_role,PriceRole.REFERENCE_MARKET_PRICE)
        self.assertEqual(broker.price_role,PriceRole.BROKER_PRICE)
        self.assertEqual(reference.current_price,broker.current_price)

    def test_fx_provenance_and_estimate_language(self):
        self.add();v=self.view(display_currency='KRW');p=v.positions[0]
        self.assertEqual(p.fx_timestamp,NOW);self.assertEqual(p.current_price,D(120))
        text=render(v,language='ko')
        for label in ('1 USD = 1400 KRW','FAKE',str(NOW),'환율 최신성: 최신','표시용 환산 추정치','약 ₩'):
            self.assertIn(label,text)
        self.assertIn('not an execution',render(v))
        stale=replace(v,positions=(replace(p,fx_is_stale=True),))
        self.assertIn('환율 최신성: 오래된 환율 데이터',render(stale,language='ko'))

    def test_neutral_money_indicator(self):
        self.assertEqual(money(D(0),'USD',signed=True),'$0.00 (Neutral)')
        self.assertEqual(money(D(0),'KRW',signed=True,language='ko'),'₩0 (중립)')
