"""Explicit opening balances: synthetic broker snapshots and temporary databases only."""
import io
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import Mock, patch

from magi.accounts import PortfolioAccountIdentity
from magi.broker.cli import main as broker_cli
from magi.broker.importing import BrokerPositionImporter, ImportRejected
from magi.broker.ledger import ReadOnlyLedgerDatabase
from magi.broker.models import BrokerResult, BrokerErrorCode, AccountList, BrokerAccount
from magi.broker.reconciliation import ReconciliationEngine, ReconciliationStatus as Status
from magi.memory import AnalysisMemory
from magi.portfolio import Portfolio, PortfolioError, OpeningBalance, TradeAction, HistoryCompleteness
from magi.portfolio_cli import main as portfolio_cli
from magi.portfolio_presentation import OPENING_LABELS, HISTORY_WARNINGS
from magi.storage import Database, StorageError, timestamp
from magi.voting import VotingEngine
from test_broker import account, holding, snapshot, NOW
from test_voting import results


class OpeningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = Database(self.root/'ledger.db')
        self.portfolio = Portfolio(self.db)
        self.identity = PortfolioAccountIdentity.from_broker(account())
        self.scope = dict(broker_provider=self.identity.provider,broker_account_ref=self.identity.account_ref)
        self.result = BrokerResult(snapshot(holding(quantity='4.063741',cost='193.080604')))
        self.importer = BrokerPositionImporter(self.portfolio,now=lambda:NOW)
        for target in ('socket.socket.connect','socket.create_connection','socket.getaddrinfo'):
            guard = patch(target,side_effect=AssertionError('Network forbidden'))
            guard.start(); self.addCleanup(guard.stop)

    def opening(self, quantity='10', cost='100', **kwargs):
        options=dict(as_of=NOW,currency='USD',market='US',confirmed=True,**self.scope)
        options.update(kwargs)
        return self.portfolio.record_opening_balance('NVDA',quantity,cost,**options)

    def trade(self, action, quantity, price, **kwargs):
        return self.portfolio.record_transaction('NVDA',action,quantity,price,currency='USD',market='US',
            executed_at=kwargs.pop('executed_at',NOW+timedelta(days=1)),**self.scope,**kwargs)

    def position(self, **kwargs):
        return self.portfolio.get_position('NVDA',currency='USD',market='US',**self.scope,**kwargs)

    def preview(self, result=None, **kwargs):
        return self.importer.preview(result or self.result,self.identity,'NVDA',**kwargs)

    def imported(self, **kwargs):
        return self.importer.import_position(self.result,self.identity,'NVDA',confirmed=True,**kwargs)

    def command(self, command, *extra, portfolio=True, service=None):
        service = service or Mock()
        service.get_accounts.return_value=BrokerResult(AccountList((account(),),NOW))
        service.get_holdings.return_value=self.result
        output=io.StringIO()
        with patch('magi.broker.importing.utcnow',return_value=NOW), redirect_stdout(output),redirect_stderr(output):
            code=broker_cli([command,'NVDA','--account',self.identity.account_ref,*extra],service=service,
                            portfolio=self.portfolio if portfolio else None)
        return code,output.getvalue(),service

    def test_opening_basic_no_fake_trade_fee_or_analysis(self):
        event=self.opening()
        self.assertIsInstance(event,OpeningBalance)
        self.assertEqual(event.action,TradeAction.OPENING_BALANCE)
        self.assertEqual(event.source,'BROKER_SNAPSHOT')
        self.assertEqual(event.opening_book_cost,Decimal(1000))
        self.assertIsNone(event.price_per_share)
        self.assertIsNone(event.fees)
        self.assertIsNone(event.linked_analysis_run_id)
        self.assertIsNone(self.portfolio.get_linked_analysis(event.transaction_id))
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM portfolio_transactions').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM portfolio_opening_balances').fetchone()[0],1)
        self.assertEqual(self.portfolio.get_transactions(),(event,))

    def test_fractional_usd_cost_is_exact(self):
        event=self.imported()
        with localcontext() as context:
            context.prec=80
            self.assertEqual(event.opening_book_cost,Decimal('4.063741')*Decimal('193.080604'))
        self.assertEqual(self.position().shares_held,Decimal('4.063741'))
        self.assertEqual(self.position().average_book_cost,Decimal('193.080604'))

    def test_krw_and_usd_remain_separate(self):
        self.opening()
        self.opening('0.000311','70000',currency='KRW',market='KR')
        self.assertEqual(len(self.portfolio.get_open_positions()),2)
        krw=self.portfolio.get_position('NVDA',market='KR',currency='KRW',**self.scope)
        self.assertEqual(krw.book_cost,Decimal('21.770000'))
        with self.assertRaises(PortfolioError): self.portfolio.get_unified_position('NVDA')

    def test_unknown_history_and_dates_opening_only(self):
        self.opening()
        p=self.position()
        self.assertEqual(p.history_completeness,HistoryCompleteness.OPENING_BALANCE_HISTORY)
        self.assertEqual(p.tracking_start_date,timestamp(NOW))
        self.assertIsNone(p.first_purchase_date)
        self.assertIsNone(p.original_first_purchase_date)
        self.assertIsNone(p.first_recorded_buy_date)
        self.assertIsNone(p.pre_tracking_realized_pnl)
        self.assertIsNone(self.portfolio.get_first_purchase_date('NVDA',**self.scope))
        self.assertEqual(p.realized_pnl,Decimal(0))
        self.assertEqual(p.total_shares_purchased,Decimal(0))
        self.assertEqual(p.total_capital_invested,Decimal(0))

    def test_opening_then_buy_weighted_cost_and_true_buy_date(self):
        self.opening()
        buy=self.trade('BUY','10','200',fees='2')
        p=self.position()
        self.assertEqual(p.shares_held,Decimal(20))
        self.assertEqual(p.book_cost,Decimal(3002))
        self.assertEqual(p.average_book_cost,Decimal('150.1'))
        self.assertEqual(p.first_recorded_buy_date,buy.timestamp)
        self.assertIsNone(p.first_purchase_date)
        self.assertIsNone(self.portfolio.get_first_purchase_date('NVDA',**self.scope))
        self.assertEqual(p.total_capital_invested,Decimal(2002))
        self.assertEqual(p.total_shares_purchased,Decimal(10))

    def test_partial_sell_tracked_realized_and_unrealized(self):
        self.opening()
        self.trade('SELL','4','150',fees='2')
        p=self.position(current_price='120')
        self.assertEqual(p.shares_held,Decimal(6))
        self.assertEqual(p.book_cost,Decimal(600))
        self.assertEqual(p.realized_pnl,Decimal(198))
        self.assertEqual(p.unrealized_pnl,Decimal(120))
        self.assertEqual(p.market_value,Decimal(720))
        self.assertIsNone(p.pre_tracking_realized_pnl)

    def test_full_sell_and_later_reopen_keep_incomplete_history(self):
        self.opening()
        self.trade('SELL','10','150')
        p=self.position()
        self.assertEqual((p.shares_held,p.book_cost,p.average_book_cost),(Decimal(0),)*3)
        self.assertEqual(p.realized_pnl,Decimal(500))
        with self.assertRaises(PortfolioError): self.opening()
        self.trade('BUY','1','170',executed_at=NOW+timedelta(days=2))
        self.assertEqual(self.position().history_completeness,HistoryCompleteness.OPENING_BALANCE_HISTORY)
        self.assertIsNone(self.position().original_first_purchase_date)

    def test_opening_buy_sell_uses_combined_basis(self):
        self.opening()
        self.trade('BUY','10','200')
        self.trade('SELL','5','180')
        p=self.position()
        self.assertEqual(p.average_book_cost,Decimal(150))
        self.assertEqual(p.book_cost,Decimal(2250))
        self.assertEqual(p.realized_pnl,Decimal(150))

    def test_unified_and_account_views_keep_history_breakdown(self):
        self.opening()
        self.portfolio.record_transaction('NVDA','BUY','5','200',currency='USD',market='US')
        p=self.portfolio.get_unified_position('NVDA',currency='USD',market='US')
        self.assertEqual(p.shares_held,Decimal(15))
        self.assertEqual(p.book_cost,Decimal(2000))
        self.assertEqual(len(p.accounts),2)
        self.assertEqual(len(self.portfolio.get_open_positions_by_account(self.identity.provider,self.identity.account_ref)),1)
        self.assertEqual({a.history_completeness for a in p.accounts},set(HistoryCompleteness))

    def test_no_backdating_before_opening(self):
        self.opening()
        before=self.db.path.read_bytes()
        for action in ('BUY','SELL'):
            with self.assertRaisesRegex(PortfolioError,'precedes'):
                self.trade(action,'1','100',executed_at=NOW-timedelta(seconds=1))
        self.assertEqual(before,self.db.path.read_bytes())

    def test_same_time_real_trade_orders_after_opening(self):
        self.opening()
        self.trade('SELL','1','150',executed_at=NOW)
        self.assertEqual(self.position().shares_held,Decimal(9))
        self.assertEqual(self.position().realized_pnl,Decimal(50))

    def test_invalid_values_and_manual_account_rejected(self):
        for quantity,cost in [('0','1'),('-1','1'),('1','-1'),('NaN','1'),('1','Infinity')]:
            with self.subTest(quantity=quantity,cost=cost), self.assertRaises(PortfolioError):
                self.opening(quantity,cost)
        for kwargs in [dict(confirmed=False),dict(confirmed=1),dict(as_of=None),dict(market=''),dict(broker_provider='MANUAL',broker_account_ref='DEFAULT')]:
            with self.subTest(kwargs=kwargs),self.assertRaises(PortfolioError): self.opening(**kwargs)
        self.assertEqual(self.portfolio.get_transactions(),())
        with self.assertRaises(PortfolioError): self.trade('OPENING_BALANCE','1','100')

    def test_zero_cost_is_known_cost_not_missing(self):
        self.opening(cost='0')
        self.trade('SELL','1','100')
        self.assertEqual(self.position().realized_pnl,Decimal(100))

    def test_append_only_opening_and_database_unique_guard(self):
        self.opening()
        for query in ['UPDATE portfolio_opening_balances SET notes="changed"','DELETE FROM portfolio_opening_balances',
                      'INSERT INTO portfolio_opening_balances SELECT sequence+1, "second", as_of, recorded_at, broker_provider, broker_account_ref, symbol, asset_name, market, currency, action, quantity, opening_unit_cost, opening_book_cost, source, history_completeness, notes, external_reference FROM portfolio_opening_balances']:
            with self.assertRaises(StorageError), self.db.connect() as connection: connection.execute(query)
        self.assertEqual(len(self.portfolio.get_transactions()),1)

    def test_existing_closed_trade_history_blocks_initialization(self):
        self.trade('BUY','1','100')
        self.trade('SELL','1','100')
        with self.assertRaisesRegex(PortfolioError,'ALREADY_TRACKED'): self.opening()
        with self.assertRaises(ImportRejected): self.imported()

    def test_concurrent_openings_cannot_duplicate(self):
        def attempt(_):
            try:
                return self.opening()
            except PortfolioError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(attempt,range(2)))
        self.assertEqual(sum(e is not None for e in outcomes),1)
        self.assertEqual(self.position().shares_held,Decimal(10))

    def test_preview_enriched_fields_and_no_write(self):
        h=replace(self.result.data.holdings[0],asset_name='Synthetic NVIDIA',current_price=Decimal(225),market_value=Decimal(900),unrealized_pnl=Decimal(100))
        before=self.db.path.read_bytes()
        preview=self.preview(BrokerResult(snapshot(h)))
        self.assertEqual(preview.asset_name,'Synthetic NVIDIA')
        self.assertEqual(preview.current_price,Decimal(225))
        self.assertEqual(preview.market_value,Decimal(900))
        self.assertEqual(preview.unrealized_pnl,Decimal(100))
        self.assertIsNone(preview.execution_price)
        self.assertIsNone(preview.executed_at)
        self.assertEqual(before,self.db.path.read_bytes())

    def test_missing_average_cost_rejected(self):
        result=BrokerResult(snapshot(replace(holding(),average_cost=None)))
        with self.assertRaisesRegex(ImportRejected,'average cost is unavailable'): self.preview(result)

    def test_stale_failed_absent_and_expired_snapshots_rejected(self):
        bad=[BrokerResult(),BrokerResult(self.result.data,BrokerErrorCode.UNAVAILABLE),
             BrokerResult(replace(self.result.data,is_stale=True)),
             BrokerResult(replace(self.result.data,fetched_at=NOW-timedelta(seconds=61))),
             BrokerResult(replace(self.result.data,fetched_at=NOW+timedelta(seconds=1))),
             BrokerResult(snapshot(replace(holding(),is_stale=True))),
             BrokerResult(snapshot(replace(holding(),fetched_at=NOW-timedelta(seconds=61))))]
        for result in bad:
            with self.subTest(result_type=type(result)),self.assertRaises(ImportRejected): self.preview(result)
        self.assertEqual(self.portfolio.get_transactions(),())

    def test_wrong_account_provider_and_holding_identity_rejected(self):
        for result in [BrokerResult(replace(self.result.data,account_id='200')),
                       BrokerResult(replace(self.result.data,provider='KIWOOM')),
                       BrokerResult(snapshot(replace(holding(),account_id='200'))),
                       BrokerResult(snapshot(replace(holding(),provider='KIWOOM')))]:
            with self.assertRaises(ImportRejected): self.preview(result)

    def test_zero_negative_invalid_quantity_cost_rejected(self):
        for h in [replace(holding(),quantity=Decimal(0)),replace(holding(),quantity=Decimal(-1)),
                  replace(holding(),average_cost=Decimal('NaN')),replace(holding(),quantity=1.5)]:
            with self.assertRaises(PortfolioError): self.preview(BrokerResult(snapshot(h)))

    def test_duplicate_and_ambiguous_instruments_rejected(self):
        with self.assertRaises(ImportRejected): self.preview(BrokerResult(snapshot(holding(),holding())))
        mixed=BrokerResult(snapshot(holding(),holding(market='KR',currency='KRW')))
        with self.assertRaises(ImportRejected): self.preview(mixed)
        self.assertEqual(self.preview(mixed,market='US',currency='USD').market,'US')

    def test_import_confirmation_and_duplicate_rejection(self):
        for confirmed in (False,None,1):
            with self.assertRaisesRegex(ImportRejected,'CONFIRMATION_REQUIRED'):
                self.importer.import_position(self.result,self.identity,'NVDA',confirmed=confirmed)
        event=self.imported(notes='Explicit import')
        self.assertEqual(event.notes,'Explicit import')
        with self.assertRaisesRegex(ImportRejected,'ALREADY_TRACKED'): self.imported()
        self.assertEqual(len(self.portfolio.get_transactions()),1)

    def test_existing_position_quantity_mismatch_never_overwritten(self):
        self.opening(quantity='1')
        before=self.db.path.read_bytes()
        with self.assertRaisesRegex(ImportRejected,'QUANTITY_MISMATCH'): self.imported()
        self.assertEqual(before,self.db.path.read_bytes())

    def test_existing_position_cost_mismatch_never_overwritten(self):
        self.opening(quantity='4.063741',cost='1')
        with self.assertRaisesRegex(ImportRejected,'COST_MISMATCH'): self.imported()
        self.assertEqual(self.position().average_book_cost,Decimal(1))

    def test_broker_only_then_match_with_other_accounts_unchanged(self):
        other=PortfolioAccountIdentity.from_broker(replace(account(),account_id='200'))
        self.portfolio.record_transaction('NVDA','BUY','50','1',currency='USD',market='US')
        self.portfolio.record_transaction('NVDA','BUY','60','1',currency='USD',market='US',broker_provider=other.provider,broker_account_ref=other.account_ref)
        before=self.portfolio.get_transactions()
        engine=ReconciliationEngine()
        self.assertEqual(engine.compare(self.portfolio.get_open_positions(),self.result).rows[0].status,Status.BROKER_ONLY)
        self.imported()
        self.assertEqual(engine.compare(self.portfolio.get_open_positions(),self.result).rows[0].status,Status.MATCH)
        self.assertTrue(all(t in self.portfolio.get_transactions() for t in before))

    def test_snapshot_expiring_before_write_is_rejected(self):
        times=iter([NOW,NOW,NOW+timedelta(seconds=61)])
        importer=BrokerPositionImporter(self.portfolio,now=lambda:next(times))
        with self.assertRaises(ImportRejected): importer.import_position(self.result,self.identity,'NVDA',confirmed=True)
        self.assertEqual(self.portfolio.get_transactions(),())

    def test_cli_confirmed_import_and_duplicate(self):
        code,text,service=self.command('import-position','--confirm')
        self.assertEqual(code,0,text)
        self.assertIn('OPENING_BALANCE recorded:',text)
        service.get_holdings.assert_called_once()
        self.assertEqual(len(self.portfolio.get_transactions()),1)
        self.assertEqual(self.command('import-position','--confirm')[0],1)

    def test_cli_no_confirmation_no_network_or_database(self):
        service=Mock()
        with patch('magi.broker.import_cli.Portfolio',side_effect=AssertionError('DB forbidden')):
            code,text,service=self.command('import-position',service=service,portfolio=False)
        self.assertEqual(code,1)
        self.assertIn('CONFIRMATION_REQUIRED',text)
        service.get_accounts.assert_not_called()
        service.get_holdings.assert_not_called()

    def test_cli_preview_is_readonly_and_korean_warning(self):
        before=self.db.path.read_bytes()
        code,text,_=self.command('import-preview','--language','ko')
        self.assertEqual(code,0,text)
        self.assertIn(HISTORY_WARNINGS['ko'],text)
        self.assertIn(OPENING_LABELS['ko'],text)
        self.assertIn('Original purchase date: UNKNOWN',text)
        self.assertEqual(before,self.db.path.read_bytes())

    def test_cli_preview_missing_database_remains_missing(self):
        missing=self.root/'absent.db'
        with patch('magi.broker.import_cli.DEFAULT_DB_PATH',missing),patch('magi.broker.import_cli.Portfolio',side_effect=AssertionError('DB creation forbidden')):
            self.assertEqual(self.command('import-preview',portfolio=False)[0],0)
        self.assertFalse(missing.exists())

    def test_cli_confirmed_import_can_initialize_new_local_ledger(self):
        missing=self.root/'new.db'
        with patch('magi.broker.import_cli.DEFAULT_DB_PATH',missing),patch('magi.broker.import_cli.Portfolio',side_effect=lambda:Portfolio(Database(missing))):
            code,text,_=self.command('import-position','--confirm',portfolio=False)
        self.assertEqual(code,0,text)
        self.assertEqual(len(Portfolio(Database(missing)).get_transactions()),1)

    def test_cli_rejects_wrong_safe_ref_before_holdings(self):
        service=Mock()
        service.get_accounts.return_value=BrokerResult(AccountList((account(),),NOW))
        other=PortfolioAccountIdentity.from_broker(replace(account(),account_id='other'))
        with redirect_stderr(io.StringIO()):
            code=broker_cli(['import-position','NVDA','--account',other.account_ref,'--confirm'],service=service,portfolio=self.portfolio)
        self.assertEqual(code,1)
        service.get_holdings.assert_not_called()

    def test_cli_preview_all_never_imports_and_bulk_import_not_supported(self):
        service=Mock()
        service.get_accounts.return_value=BrokerResult(AccountList((account(),),NOW))
        service.get_holdings.return_value=BrokerResult(snapshot(holding(),holding(symbol='AMZN')))
        with patch('magi.broker.importing.utcnow',return_value=NOW),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(broker_cli(['import-preview','--account',self.identity.account_ref],service=service,portfolio=self.portfolio),0)
            with self.assertRaises(SystemExit): broker_cli(['import-position','--account',self.identity.account_ref,'--confirm'],service=service,portfolio=self.portfolio)
            with self.assertRaises(SystemExit): broker_cli(['import-position','NVDA','--account',self.identity.account_ref,'--confirm','--all'],service=service,portfolio=self.portfolio)
        self.assertEqual(self.portfolio.get_transactions(),())

    def test_portfolio_presentation_avoids_fake_purchase_date(self):
        self.opening()
        out=io.StringIO()
        with redirect_stdout(out):
            code=portfolio_cli(['portfolio','show','NVDA','--broker',self.identity.provider,'--account',self.identity.account_ref],portfolio=self.portfolio)
            portfolio_cli(['portfolio','history','NVDA','--broker',self.identity.provider,'--account',self.identity.account_ref],portfolio=self.portfolio)
        self.assertEqual(code,0)
        self.assertIn('Tracked realized P/L',out.getvalue())
        self.assertIn('Original purchase date: UNKNOWN',out.getvalue())
        self.assertNotIn('First purchase:',out.getvalue())
        self.assertNotIn('Fees:',out.getvalue())

    def test_security_no_raw_payload_account_number_credentials_or_tokens(self):
        for key in ('TEST_API_KEY','TEST_TOKEN','TEST_CLIENT_ID'):
            with patch.dict(os.environ,{key:'synthetic-sensitive-opening-value'}),self.assertRaises(StorageError):
                self.imported(notes='synthetic-sensitive-opening-value')
        self.imported()
        content=self.db.path.read_bytes()
        for forbidden in (b'synthetic-sensitive-opening-value',b'98765432109',b'accountSeq',b'access_token',b'Authorization'):
            self.assertNotIn(forbidden,content)
        with self.assertRaises(PortfolioError): self.opening(broker_account_ref='98765432109')
        self.assertEqual(self.portfolio.get_transactions()[0].broker_account_ref,self.identity.account_ref)

    def test_deterministic_local_external_reference(self):
        first=self.opening()
        second=Portfolio(Database(self.root/'other.db')).record_opening_balance('NVDA','2','200',as_of=NOW,currency='USD',market='US',confirmed=True,**self.scope)
        self.assertEqual(first.external_reference,second.external_reference)
        self.assertNotEqual(first.transaction_id,second.transaction_id)


class OpeningMigrationTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root=Path(directory.name)
        source=Database(self.root/'source.db')
        self.record=AnalysisMemory(source).save_analysis('Synthetic analysis',VotingEngine().vote(results('BUY','BUY','HOLD')),'Synthetic explanation')
        identity=PortfolioAccountIdentity.from_broker(account())
        self.trade=Portfolio(source).record_transaction('NVDA','BUY','4.063741','193.080604',currency='USD',market='US',
            linked_analysis_run_id=self.record.run_id,broker_provider=identity.provider,broker_account_ref=identity.account_ref,notes='Historical trade',external_reference='synthetic-execution')
        self.path=self.root/'v2.db'; self.original={}
        with sqlite3.connect(self.path) as old,source.connect() as new:
            old.executescript((Path(__file__).parent/'fixtures/schema_v2.sql').read_text())
            for table in ('analysis_runs','analysis_agents','portfolio_transactions'):
                columns=[r[1] for r in old.execute('PRAGMA table_info('+table+')')]
                rows=[tuple(r) for r in new.execute('SELECT '+','.join(columns)+' FROM '+table)]
                old.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in columns)+')',rows)
                self.original[table]=rows

    def test_v2_to_v3_preserves_all_rows_accounts_and_analysis(self):
        database=Database(self.path)
        with database.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            for table,rows in self.original.items():
                self.assertEqual([tuple(r) for r in connection.execute('SELECT * FROM '+table)],rows)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM portfolio_opening_balances').fetchone()[0],0)
        self.assertEqual(Portfolio(database).get_transactions(),(self.trade,))
        self.assertEqual(AnalysisMemory(database).get_analysis(self.record.run_id),self.record)
        for table in self.original:
            with self.assertRaises(StorageError),database.connect() as connection: connection.execute('DELETE FROM '+table)

    def test_v2_migration_rollback_after_ddl_preserves_file(self):
        original=Database._execute_schema
        before=self.path.read_bytes()
        def fail(connection,path):
            original(connection,path)
            raise sqlite3.OperationalError('synthetic migration failure')
        with patch.object(Database,'_execute_schema',side_effect=fail),self.assertRaisesRegex(StorageError,'migration failed'):
            Database(self.path)
        self.assertEqual(before,self.path.read_bytes())
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='portfolio_opening_balances'").fetchone())
        Database(self.path)

    def test_v3_idempotence_and_future_rejection(self):
        Database(self.path)
        before=self.path.read_bytes()
        Database(self.path)
        self.assertEqual(before,self.path.read_bytes())
        with sqlite3.connect(self.path) as connection: connection.execute('PRAGMA user_version=4')
        before=self.path.read_bytes()
        with self.assertRaisesRegex(StorageError,'Unsupported'): Database(self.path)
        self.assertEqual(before,self.path.read_bytes())

    def test_readonly_v2_preview_cannot_migrate(self):
        before=self.path.read_bytes()
        with self.assertRaises(StorageError),ReadOnlyLedgerDatabase(self.path).connect(): pass
        self.assertEqual(before,self.path.read_bytes())
