"""Synthetic broker fixtures only: no real account data, sockets, or credentials."""
import io
import logging
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from magi.toss import TossSession
from magi.market.toss import TossProvider
from magi.market.models import ErrorCode, FXRate, MarketResult, Quote
from magi.broker.base import BrokerError, BrokerProvider
from magi.broker.cli import main as broker_cli
from magi.broker.enrichment import enrich_holding
from magi.broker.ledger import ReadOnlyLedgerDatabase
from magi.broker.models import (AccountList, BrokerAccount, BrokerErrorCode, BrokerResult,
    CashBalance, CashSnapshot, Holding, HoldingsSnapshot)
from magi.broker.reconciliation import ReconciliationEngine, ReconciliationStatus as Status
from magi.broker.service import BrokerService
from magi.broker.toss import TossBrokerProvider
from magi.portfolio import Portfolio
from magi.storage import Database, StorageError

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
ACCOUNT_NUMBER = '98765432109'  # Deliberately synthetic, never a real account.


def auth():
    return {'access_token':'synthetic-broker-token', 'token_type':'Bearer', 'expires_in':100}


def accounts(count=1):
    return {'result':[{'accountSeq':i+100, 'accountNo':ACCOUNT_NUMBER, 'accountType':'BROKERAGE'} for i in range(count)]}


def item(symbol='NVDA', market='US', currency='USD', quantity='10', cost='180'):
    return {'symbol':symbol, 'marketCountry':market, 'currency':currency, 'quantity':quantity,
            'name':'Synthetic asset', 'averagePurchasePrice':cost, 'lastPrice':'225',
            'marketValue':{'purchaseAmount':'1800','amount':'2250'},
            'profitLoss':{'amount':'450','rate':'0.25'}}


def holdings(*items):
    return {'result':{'items':list(items), 'totalPurchaseAmount':{'krw':'0','usd':'1800'},
        'marketValue':{'amount':{'krw':'0','usd':'2250'}},
        'profitLoss':{'amount':{'krw':'0','usd':'450'}}}}


def account():
    return BrokerAccount('100','TOSS',NOW,account_type='BROKERAGE')


def holding(symbol='NVDA', market='US', currency='USD', quantity='10', cost='180'):
    return Holding(symbol,market,currency,Decimal(quantity),'TOSS','100',NOW,average_cost=Decimal(cost))


def snapshot(*rows):
    return HoldingsSnapshot(tuple(rows),(),'TOSS','100',NOW)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.elapsed=0
        self.requests=[]
        for name in ['socket.socket.connect','socket.create_connection','socket.getaddrinfo']:
            guard=patch(name,side_effect=AssertionError('Network forbidden'))
            guard.start(); self.addCleanup(guard.stop)

    def sleep(self, seconds):
        self.elapsed += seconds

    def session(self, responses):
        queue=iter(responses)
        def handle(request):
            self.requests.append(request)
            data=next(queue)
            if isinstance(data,Exception): raise data
            return data if isinstance(data,httpx.Response) else httpx.Response(200,json=data)
        session=TossSession(client_id='synthetic-id',client_secret='synthetic-secret',
            transport=httpx.MockTransport(handle), clock=lambda:self.elapsed,
            now=lambda:NOW+timedelta(seconds=self.elapsed),sleep=self.sleep,jitter=lambda:0)
        self.addCleanup(session.close)
        return session

    def broker(self, responses):
        return TossBrokerProvider(session=self.session(responses))

    def service(self, responses):
        return BrokerService(self.broker(responses),clock=lambda:self.elapsed)

    def command(self, service, *args, portfolio=None):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            try: code=broker_cli(list(args),service=service,portfolio=portfolio)
            except SystemExit as error: code=error.code
        return code,out.getvalue(),err.getvalue()

    def portfolio(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/'ledger.db'
        return Portfolio(Database(path)),path

    def position(self, symbol='NVDA', market='US', currency='USD', quantity='10', cost='180'):
        portfolio,_=self.portfolio()
        portfolio.record_transaction(symbol,'BUY',quantity,cost,market=market,currency=currency)
        return portfolio.get_position(symbol)

    def reconcile(self, positions, rows):
        return ReconciliationEngine().compare(positions,BrokerResult(snapshot(*rows)))

    def test_shared_oauth_between_market_and_broker(self):
        s=self.session([auth(),accounts(),{'result':[{'symbol':'NVDA','lastPrice':'225','currency':'USD','timestamp':NOW.isoformat()}]},holdings(item())])
        b=TossBrokerProvider(session=s); m=TossProvider(session=s)
        a=b.get_accounts().accounts[0]
        m.get_quote('NVDA','US'); b.get_holdings(a)
        self.assertEqual(sum(r.url.path=='/oauth2/token' for r in self.requests),1)
        self.assertEqual(self.requests[-1].headers['X-Tossinvest-Account'],'100')
        b.close(); self.assertFalse(s._client.is_closed)
        m.close(); self.assertFalse(s._client.is_closed)

    def test_expired_token_and_401_reauth(self):
        for use_expiry in (True,False):
            with self.subTest(expiry=use_expiry):
                replies=[auth(),accounts(),auth(),holdings()] if use_expiry else [auth(),accounts(),httpx.Response(401),auth(),holdings()]
                b=self.broker(replies); a=b.get_accounts().accounts[0]
                if use_expiry: self.elapsed+=101
                self.assertEqual(b.get_holdings(a).holdings,())

    def test_repeated_auth_failure_sanitized(self):
        service=self.service([httpx.Response(401,text='synthetic-secret '+ACCOUNT_NUMBER)])
        result=service.get_accounts()
        self.assertEqual(result.error,BrokerErrorCode.AUTHENTICATION)
        self.assertNotIn('synthetic-secret',repr(result))
        self.assertNotIn(ACCOUNT_NUMBER,repr(result))

    def test_accounts_one_multiple_and_empty(self):
        for count in (0,1,3):
            result=self.broker([auth(),accounts(count)]).get_accounts()
            self.assertEqual(len(result.accounts),count)
            for a in result.accounts:
                self.assertIsNone(a.base_currency)
                self.assertIsNone(a.account_name)
                self.assertNotIn(ACCOUNT_NUMBER,str(asdict(a)))
                self.assertNotIn(a.account_id,repr(a))

    def test_account_masked_cli_and_safe_selection(self):
        service=self.service([auth(),accounts(2),holdings()])
        code,out,err=self.command(service,'accounts')
        self.assertEqual(code,0)
        self.assertIn('Account 1 [identifier masked]',out)
        self.assertNotIn(ACCOUNT_NUMBER,out+err)
        code,out,err=self.command(service,'holdings')
        self.assertEqual(code,1)
        self.assertIn('--account',err)
        self.assertEqual(len(self.requests),2)
        self.assertEqual(self.command(service,'holdings','--account','2')[0],0)
        self.assertEqual(self.requests[-1].headers['X-Tossinvest-Account'],'101')

    def test_malformed_account_response(self):
        for data in ({'result':{}},{'result':[{}]}, {'result':[{'accountSeq':True}]},
                     {'result':[{'accountSeq':0}]}, {'result':[{'accountSeq':1,'accountType':[]}]},
                     {'result':[{'accountSeq':1},{'accountSeq':1}]}):
            with self.subTest(data=data):
                self.assertEqual(self.service([auth(),data]).get_accounts().error,BrokerErrorCode.INVALID_RESPONSE)

    def test_account_known_and_unknown_enum(self):
        b=self.broker([auth(),{'result':[{'accountSeq':1,'accountType':'FUTURE_TYPE'}]}])
        self.assertEqual(b.get_accounts().accounts[0].account_type,'FUTURE_TYPE')

    def test_kr_us_mixed_normalization(self):
        rows=self.broker([auth(),holdings(item('nvda'),item('005930','KR','KRW'))]).get_holdings(account()).holdings
        self.assertEqual([(r.symbol,r.market,r.currency) for r in rows],[('NVDA','US','USD'),('005930','KR','KRW')])
        self.assertEqual(rows[0].quantity,Decimal(10))
        self.assertEqual(rows[0].average_cost,Decimal(180))
        self.assertEqual(rows[0].unrealized_return,Decimal('.25'))
        self.assertIsNone(rows[0].available_quantity)

    def test_negative_pnl_and_returns(self):
        row=item(); row['profitLoss']={'amount':'-501234567890.123456789012345678','rate':'-0.10'}
        h=self.broker([auth(),holdings(row)]).get_holdings(account()).holdings[0]
        self.assertEqual(h.unrealized_pnl,Decimal('-501234567890.123456789012345678'))
        self.assertEqual(h.unrealized_return,Decimal('-.1'))

    def test_zero_and_missing_optional_fields(self):
        row={k:v for k,v in item(quantity='0').items() if k in ['symbol','marketCountry','currency','quantity']}
        h=self.broker([auth(),{'result':{'items':[row]}}]).get_holdings(account()).holdings[0]
        self.assertEqual(h.quantity,Decimal(0))
        for field in ['average_cost','current_price','book_cost','market_value','unrealized_pnl','asset_name']:
            self.assertIsNone(getattr(h,field))
        self.assertEqual(self.broker([auth(),holdings()]).get_holdings(account()).holdings,())

    def test_malformed_holdings_rejected(self):
        for row in [item(quantity='NaN'),item(quantity='-1'),item(quantity=1.5),item(currency='KRW'),
                    dict(item(),marketValue=[]),dict(item(),profitLoss={'amount':'Infinity'}),dict(item(),symbol='')]:
            with self.subTest(row=row):
                result=self.service([auth(),holdings(row)]).get_holdings(account())
                self.assertEqual(result.error,BrokerErrorCode.INVALID_RESPONSE)

    def test_summary_native_totals_no_combined_rate(self):
        service=self.service([auth(),holdings(item())])
        service.get_holdings(account())
        result=service.get_account_summary(account()).data
        self.assertEqual(len(self.requests),2)
        self.assertEqual([t.currency for t in result.totals],['KRW','USD'])
        self.assertEqual(result.totals[1].total_market_value,Decimal(2250))
        self.assertEqual(result.asset_count,1)
        self.assertFalse(hasattr(result,'combined_total'))

    def test_missing_totals_remain_none(self):
        result=self.service([auth(),{'result':{'items':[]}}]).get_account_summary(account()).data
        self.assertTrue(all(t.total_market_value is None for t in result.totals))

    def test_cash_unsupported_without_requests(self):
        service=self.service([])
        result=service.get_cash_balances(account())
        self.assertEqual(result.error,BrokerErrorCode.UNSUPPORTED)
        code,out,err=self.command(service,'balances')
        self.assertEqual(code,1)
        self.assertIn('unsupported',err)
        self.assertEqual(self.requests,[])

    def test_provider_independent_multi_currency_cash(self):
        provider=Mock(spec=BrokerProvider)
        provider.supports_cash_balances=True
        provider.get_accounts.return_value=AccountList((account(),),NOW)
        balances=tuple(CashBalance(c,'OTHER','100',NOW,cash_balance=Decimal(v)) for c,v in [('KRW','120000'),('USD','100.25')])
        provider.get_cash_balances.return_value=CashSnapshot(balances,NOW)
        service=BrokerService(provider)
        result=service.get_cash_balances(account()).data
        self.assertEqual(result.balances[1].cash_balance,Decimal('100.25'))
        self.assertIsNone(result.balances[0].available_cash)
        self.assertIsNone(result.balances[0].buying_power)
        code,out,_=self.command(service,'balances')
        self.assertEqual(code,0)
        self.assertIn('KRW: Cash 120000',out)
        self.assertIn('USD: Cash 100.25',out)
        provider.get_cash_balances.return_value=CashSnapshot((),NOW)
        self.assertEqual(BrokerService(provider).get_cash_balances(account()).data.balances,())

    def test_reconcile_exact_quantity_and_cost_mismatch(self):
        position=self.position()
        for quantity,cost,status in [('10','180',Status.MATCH),('12','180',Status.QUANTITY_MISMATCH),('10','181',Status.COST_MISMATCH)]:
            result=self.reconcile([position],[holding(quantity=quantity,cost=cost)])
            row=result.rows[0]
            self.assertEqual(row.status,status)
            self.assertEqual(row.quantity_difference,Decimal(quantity)-Decimal(10))
            self.assertEqual(row.cost_difference,Decimal(cost)-Decimal(180))

    def test_reconcile_missing_cost_not_fabricated(self):
        result=self.reconcile([self.position()],[replace(holding(),average_cost=None)])
        self.assertEqual(result.rows[0].status,Status.MATCH)
        self.assertFalse(result.rows[0].cost_comparable)
        self.assertIsNone(result.rows[0].cost_difference)

    def test_reconcile_broker_and_ledger_only(self):
        result=self.reconcile([self.position('TSLA')],[holding('AAPL')])
        self.assertEqual([r.status for r in result.rows],[Status.BROKER_ONLY,Status.MAGI_ONLY])
        self.assertEqual(result.rows[0].ledger_quantity,Decimal(0))
        self.assertEqual(result.rows[1].broker_quantity,Decimal(0))

    def test_reconcile_market_and_currency_identity(self):
        for h in [holding(market='KR'),holding(currency='KRW')]:
            result=self.reconcile([self.position()],[h])
            self.assertEqual({r.status for r in result.rows},{Status.BROKER_ONLY,Status.MAGI_ONLY})
            self.assertEqual(len(result.rows),2)

    def test_reconcile_ambiguity_and_unknown_market(self):
        result=self.reconcile([self.position()],[holding(),holding()])
        self.assertEqual(result.rows[0].status,Status.AMBIGUOUS)
        self.assertIsNone(result.rows[0].quantity_difference)
        p=self.position(market=None)
        result=self.reconcile([p],[holding()])
        self.assertTrue(all(r.status==Status.AMBIGUOUS for r in result.rows))

    def test_reconcile_empty_and_unavailable(self):
        self.assertEqual(self.reconcile([],[]).rows,())
        self.assertEqual(self.reconcile([self.position()],[]).rows[0].status,Status.MAGI_ONLY)
        failed=BrokerResult(error=BrokerErrorCode.UNAVAILABLE)
        self.assertEqual(ReconciliationEngine().compare([self.position()],failed).status,Status.UNAVAILABLE)
        self.assertEqual(ReconciliationEngine().compare(None,BrokerResult(snapshot())).status,Status.UNAVAILABLE)

    def test_reconcile_stale_never_reports_false_match(self):
        result=BrokerResult(replace(snapshot(holding()),is_stale=True))
        report=ReconciliationEngine().compare([self.position()],result)
        self.assertEqual(report.status,Status.UNAVAILABLE)
        self.assertTrue(report.is_stale)

    def test_reconcile_foreign_account_not_merged(self):
        row=replace(holding(),account_id='999')
        self.assertEqual(self.reconcile([self.position()],[row]).rows[0].status,Status.AMBIGUOUS)

    def test_readonly_reconcile_preserves_db_bytes_and_rows(self):
        portfolio,path=self.portfolio()
        portfolio.record_transaction('NVDA','BUY','10','180',currency='USD',market='US')
        before=path.read_bytes(); transactions=portfolio.get_transactions()
        readonly=Portfolio(ReadOnlyLedgerDatabase(path))
        service=self.service([auth(),accounts(),holdings(item())])
        code,out,err=self.command(service,'reconcile',portfolio=readonly)
        self.assertEqual(code,0,err)
        self.assertIn('Status: MATCH',out)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(portfolio.get_transactions(),transactions)
        with self.assertRaises(StorageError):
            with readonly.database.connect() as connection:
                connection.execute('DELETE FROM portfolio_transactions')

    def test_missing_readonly_db_is_not_created(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/'not-created.db'
        readonly=Portfolio(ReadOnlyLedgerDatabase(path))
        service=self.service([auth(),accounts(),holdings()])
        code,out,err=self.command(service,'reconcile',portfolio=readonly)
        self.assertEqual(code,1)
        self.assertIn('UNAVAILABLE',out)
        self.assertFalse(path.exists())

    def test_cache_hit_expiry_stale_propagates_to_holdings_and_summary(self):
        service=self.service([auth(),holdings(item()),httpx.Response(503),httpx.Response(503),httpx.Response(503),httpx.Response(404)])
        first=service.get_holdings(account()).data
        self.assertEqual(service.get_holdings(account()).data,first)
        self.elapsed+=11
        result=service.get_holdings(account())
        self.assertTrue(result.data.is_stale)
        self.assertTrue(result.data.holdings[0].is_stale)
        self.assertEqual(result.data.fetched_at,first.fetched_at)
        summary=service.get_account_summary(account())
        self.assertTrue(summary.data.is_stale)
        self.assertIsNotNone(summary.error)

    def test_stale_accounts_cannot_select_for_new_request(self):
        service=self.service([auth(),accounts(),httpx.Response(404)])
        service.get_accounts(); self.elapsed+=11
        code,_,err=self.command(service,'holdings')
        self.assertEqual(code,1)
        self.assertIn('Fresh account discovery',err)
        self.assertEqual(len(self.requests),3)

    def test_programming_errors_not_hidden(self):
        provider=Mock(spec=BrokerProvider)
        provider.get_accounts.side_effect=RuntimeError('bug')
        with self.assertRaises(RuntimeError): BrokerService(provider).get_accounts()

    def test_enrichment_keeps_broker_price_and_native_currency(self):
        data=Mock()
        h=replace(holding(),current_price=Decimal(225),market_value=Decimal(2250))
        view=enrich_holding(h,data)
        data.get_quote.assert_not_called()
        self.assertEqual(view.price_source,'BROKER')
        self.assertEqual(view.holding,h)
        self.assertEqual(view.market_value,Decimal(2250))

    def test_enrichment_fetches_missing_quote_and_calculates(self):
        data=Mock()
        data.get_quote.return_value=MarketResult(Quote('NVDA','US','USD',Decimal(225),NOW,'FAKE',NOW))
        view=enrich_holding(holding(),data)
        self.assertEqual(view.market_value,Decimal(2250))
        self.assertEqual(view.unrealized_pnl,Decimal(450))
        self.assertEqual(view.unrealized_return,Decimal('.25'))
        self.assertIsNone(view.holding.current_price)
        self.assertEqual(view.price_source,'MARKET_DATA')

    def test_enrichment_quote_unavailable(self):
        data=Mock(); data.get_quote.return_value=MarketResult(error=ErrorCode.UNAVAILABLE)
        view=enrich_holding(holding(),data)
        self.assertIsNone(view.current_price)
        self.assertIsNone(view.market_value)
        self.assertEqual(view.quote_error,ErrorCode.UNAVAILABLE)

    def test_enrichment_fx_available_unavailable(self):
        h=replace(holding(),current_price=Decimal(225),market_value=Decimal(2250))
        data=Mock()
        data.get_fx_rate.return_value=MarketResult(FXRate('USD','KRW',Decimal(1360),NOW,'FAKE',NOW))
        view=enrich_holding(h,data,'KRW')
        self.assertEqual(view.display_market_value,Decimal(3060000))
        self.assertEqual(view.market_value,Decimal(2250))
        self.assertEqual(view.holding.currency,'USD')
        data.get_fx_rate.return_value=MarketResult(error=ErrorCode.UNAVAILABLE)
        view=enrich_holding(h,data,'KRW')
        self.assertIsNone(view.display_market_value)
        self.assertEqual(view.market_value,Decimal(2250))

    def test_enrichment_mismatched_quote_rejected(self):
        data=Mock(); data.get_quote.return_value=MarketResult(Quote('AAPL','US','USD',Decimal(225),NOW,'FAKE',NOW))
        view=enrich_holding(holding(),data)
        self.assertIsNone(view.current_price)
        self.assertEqual(view.quote_error,ErrorCode.INVALID_RESPONSE)

    def test_no_raw_accounts_credentials_or_tokens_in_output_logs_or_db(self):
        service=self.service([auth(),accounts(),holdings(item())])
        logs=io.StringIO(); logger=logging.getLogger(); previous=logger.level
        handler=logging.StreamHandler(logs); logger.addHandler(handler); logger.setLevel(logging.DEBUG)
        try:
            code,out,err=self.command(service,'holdings')
        finally:
            logger.removeHandler(handler); logger.setLevel(previous)
        self.assertEqual(code,0)
        portfolio,path=self.portfolio(); before=path.read_bytes()
        self.command(service,'reconcile',portfolio=Portfolio(ReadOnlyLedgerDatabase(path)))
        for secret in [ACCOUNT_NUMBER,'synthetic-id','synthetic-secret','synthetic-broker-token']:
            self.assertNotIn(secret,out+err+logs.getvalue())
            self.assertNotIn(secret.encode(),path.read_bytes())
        self.assertEqual(before,path.read_bytes())

    def test_no_trading_endpoints_allowed(self):
        session=self.session([]); broker=TossBrokerProvider(session=session); market=TossProvider(session=session)
        for path in ['/api/v1/orders','/api/v1/orders/1/modify','/api/v1/orders/1/cancel','/api/v1/conditional-orders','/api/v1/buying-power','/api/v1/sellable-quantity']:
            for method in ['GET','POST','DELETE','PATCH']:
                with self.assertRaises(ValueError): session._send(method,path)
            with self.assertRaises(ValueError): broker._get(path)
        with self.assertRaises(ValueError): market._get('/api/v1/accounts',{})
        self.assertEqual(self.requests,[])

    def test_cli_dispatch_help_empty_and_invalid_selector(self):
        import main
        with patch('magi.broker.cli.main',return_value=0) as target:
            self.assertEqual(main.cli(['broker','accounts']),0)
            target.assert_called_once_with(['accounts'])
        with patch('magi.broker.cli.TossBrokerProvider',side_effect=AssertionError('No client')),redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error: broker_cli(['--help'])
            self.assertEqual(error.exception.code,0)
        self.assertEqual(self.command(self.service([]),'holdings','--account','0')[0],2)
        code,out,_=self.command(self.service([auth(),accounts(0)]),'accounts')
        self.assertEqual(code,0); self.assertIn('No broker accounts',out)
        self.assertEqual(self.command(self.service([auth(),accounts(0)]),'holdings')[0],1)

    def test_broker_status_retries_and_invalid_json(self):
        for status in (429,500,502,503,504):
            result=self.service([auth(),*[httpx.Response(status) for _ in range(3)]]).get_accounts()
            self.assertEqual(result.error,BrokerErrorCode.RATE_LIMITED if status==429 else BrokerErrorCode.UNAVAILABLE)
        result=self.service([auth(),httpx.Response(200,content=b'not-json')]).get_accounts()
        self.assertEqual(result.error,BrokerErrorCode.INVALID_RESPONSE)

    def test_broker_timeout_sanitized(self):
        result=self.service([httpx.ReadTimeout('synthetic-secret')]*3).get_accounts()
        self.assertEqual(result.error,BrokerErrorCode.UNAVAILABLE)
        self.assertNotIn('synthetic-secret',repr(result))

    def test_cache_isolated_by_account_and_bounded(self):
        provider=Mock(spec=BrokerProvider)
        provider.get_holdings.side_effect=lambda a: replace(snapshot(holding()),account_id=a.account_id)
        service=BrokerService(provider,max_entries=1)
        first,second=account(),replace(account(),account_id='101')
        service.get_holdings(first); service.get_holdings(first)
        self.assertEqual(provider.get_holdings.call_count,1)
        service.get_holdings(second); service.get_holdings(first)
        self.assertEqual(provider.get_holdings.call_count,3)
        self.assertEqual(len(service._cache),1)

    def test_snapshot_frozen_and_broker_close_clears_token(self):
        from dataclasses import FrozenInstanceError
        session=self.session([auth(),accounts()])
        b=TossBrokerProvider(session=session)
        a=b.get_accounts().accounts[0]
        with self.assertRaises(FrozenInstanceError): a.account_id='999'
        session.close()
        self.assertIsNone(session._token)
        self.assertTrue(session._client.is_closed)

    def test_owning_adapter_closes_its_session(self):
        with TossBrokerProvider(client_id='synthetic-id',client_secret='synthetic-secret',
                transport=httpx.MockTransport(lambda request:httpx.Response(500))) as b:
            self.assertFalse(b._session._client.is_closed)
        self.assertTrue(b._session._client.is_closed)

    def test_cli_summary_no_cross_currency_combination(self):
        code,out,err=self.command(self.service([auth(),accounts(),holdings(item())]),'summary')
        self.assertEqual(code,0,err)
        self.assertIn('KRW:',out); self.assertIn('USD:',out)
        self.assertNotIn('Combined',out)

    def test_cash_stale_rows_explicit_and_never_inferred(self):
        p=Mock(spec=BrokerProvider)
        data=CashSnapshot((CashBalance('USD','OTHER','100',NOW),),NOW)
        p.get_cash_balances.side_effect=[data,BrokerError()]
        service=BrokerService(p,clock=lambda:self.elapsed)
        self.assertIsNone(service.get_cash_balances(account()).data.balances[0].cash_balance)
        self.elapsed+=11
        result=service.get_cash_balances(account())
        self.assertTrue(result.data.is_stale)
        self.assertTrue(result.data.balances[0].is_stale)

    def test_reconciliation_fee_difference_is_observation(self):
        portfolio,_=self.portfolio()
        portfolio.record_transaction('NVDA','BUY','10','180',fees='1',currency='USD',market='US')
        result=self.reconcile([portfolio.get_position('NVDA')],[holding()])
        self.assertEqual(result.rows[0].status,Status.COST_MISMATCH)
        self.assertEqual(result.rows[0].cost_difference,Decimal('-.1'))

    def test_enrichment_staleness_and_fx_direction(self):
        data=Mock()
        data.get_quote.return_value=MarketResult(Quote('NVDA','US','USD',Decimal(225),NOW,'FAKE',NOW,is_stale=True))
        data.get_fx_rate.return_value=MarketResult(FXRate('USD','KRW',Decimal(1360),NOW,'FAKE',NOW))
        self.assertTrue(enrich_holding(holding(),data,'KRW').is_stale)
        data.get_fx_rate.return_value=MarketResult(FXRate('KRW','USD',Decimal('.001'),NOW,'FAKE',NOW))
        view=enrich_holding(holding(),data,'KRW')
        self.assertIsNone(view.display_market_value)
        self.assertEqual(view.fx_error,ErrorCode.INVALID_RESPONSE)

    def test_localization_stable_status_identifiers(self):
        from magi.broker.presentation import STATUS_LABELS
        self.assertEqual(STATUS_LABELS['ko'][Status.QUANTITY_MISMATCH.value],'수량 불일치')
        self.assertEqual(Status.MATCH.value,'MATCH')


if __name__ == '__main__':
    unittest.main()
