"""Phase 6C: synthetic identities, temporary ledgers, no live requests."""
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from magi.accounts import PortfolioAccountIdentity, account_label
from magi.broker.ledger import ReadOnlyLedgerDatabase
from magi.broker.models import BrokerAccount, BrokerResult
from magi.broker.reconciliation import ReconciliationEngine, ReconciliationStatus as Status
from magi.memory import AnalysisMemory
from magi.portfolio import Portfolio, PortfolioError
from magi.portfolio_cli import main as cli
from magi.storage import Database, StorageError
from magi.voting import VotingEngine
from test_broker import account, holding, snapshot, NOW
from test_voting import results


class AccountTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = Database(self.root / 'ledger.db')
        self.portfolio = Portfolio(self.db)
        self.toss = PortfolioAccountIdentity.from_broker(account())
        self.other = PortfolioAccountIdentity.from_broker(replace(account(), account_id='101'))
        self.kiwoom = PortfolioAccountIdentity.from_broker(replace(account(), provider='KIWOOM'))

    def buy(self, identity=None, quantity='10', price='180', **kwargs):
        identity = identity or PortfolioAccountIdentity()
        return self.portfolio.record_transaction('NVDA', 'BUY', quantity, price,
            currency=kwargs.pop('currency', 'USD'), market=kwargs.pop('market', 'US'),
            broker_provider=identity.provider, broker_account_ref=identity.account_ref, **kwargs)

    def scope(self, identity):
        return dict(broker_provider=identity.provider, broker_account_ref=identity.account_ref)

    def command(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = cli(['portfolio', *args], portfolio=self.portfolio)
        return code, output.getvalue()

    def test_manual_default_and_immutable_identity(self):
        self.assertEqual(PortfolioAccountIdentity().provider, 'MANUAL')
        self.assertEqual(PortfolioAccountIdentity().account_ref, 'DEFAULT')
        with self.assertRaises(AttributeError):
            self.toss.provider = 'MANUAL'

    def test_provider_and_account_references_are_distinct_and_stable(self):
        self.assertEqual(self.toss, PortfolioAccountIdentity.from_broker(account()))
        self.assertEqual(len({self.toss.account_ref, self.other.account_ref, self.kiwoom.account_ref}), 3)
        self.assertEqual(self.kiwoom.provider, 'KIWOOM')

    def test_masking_and_localization(self):
        self.assertNotIn(self.toss.account_ref, account_label('TOSS', self.toss.account_ref))
        self.assertIn('계좌', account_label('TOSS', self.toss.account_ref, 'ko'))
        self.assertIn('수동 입력', account_label('MANUAL', 'DEFAULT', 'ko'))
        self.assertNotIn(self.toss.account_ref, repr(self.toss))

    def test_invalid_account_identity(self):
        for provider, ref in [('TOSS','98765432109'), ('','DEFAULT'), ('MANUAL','123'), ('TOSS','Bearer secret')]:
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                PortfolioAccountIdentity(provider, ref)
        with self.assertRaises(PortfolioError):
            self.portfolio.get_transactions(broker_provider='TOSS')
        with self.assertRaises(ValueError):
            PortfolioAccountIdentity(base_currency=123)

    def test_account_buy_sell_and_fractional_precision(self):
        self.buy(self.toss, '4.063741')
        self.portfolio.record_transaction('NVDA','SELL','0.000311','200',currency='USD',market='US',**self.scope(self.toss))
        position = self.portfolio.get_position('NVDA', **self.scope(self.toss))
        self.assertEqual(position.shares_held, Decimal('4.063430'))
        self.assertEqual(position.total_shares_sold, Decimal('0.000311'))
        self.assertEqual(self.portfolio.get_transactions()[0].quantity, Decimal('4.063741'))

    def test_oversell_cannot_use_another_account(self):
        self.buy(quantity='100')
        self.buy(self.toss, '1')
        before = self.db.path.read_bytes()
        with self.assertRaises(PortfolioError):
            self.portfolio.record_transaction('NVDA','SELL','2','180',currency='USD',market='US',**self.scope(self.toss))
        self.assertEqual(before, self.db.path.read_bytes())

    def test_same_symbol_separate_positions_and_histories(self):
        for identity, quantity in [(None,'3'),(self.toss,'4'),(self.other,'5'),(self.kiwoom,'10')]:
            self.buy(identity,quantity)
        self.assertEqual(len(self.portfolio.get_open_positions()),4)
        self.assertEqual(self.portfolio.get_position('NVDA',**self.scope(self.toss)).shares_held, Decimal(4))
        self.assertEqual(len(self.portfolio.get_position_history('NVDA',**self.scope(self.toss))),1)
        with self.assertRaisesRegex(PortfolioError,'Ambiguous'):
            self.portfolio.get_position('NVDA',currency='USD',market='US')

    def test_unified_weighted_book_cost_and_breakdown(self):
        self.buy(quantity='3',price='100',fees='3')
        self.buy(self.toss,quantity='4.063741',price='200')
        combined = self.portfolio.get_unified_position('NVDA',market='US',currency='USD')
        self.assertEqual(combined.shares_held,Decimal('7.063741'))
        self.assertEqual(combined.book_cost,Decimal('1115.748200'))
        self.assertEqual(len(combined.accounts),2)
        from decimal import localcontext
        with localcontext() as context:
            context.prec = 80
            self.assertEqual(combined.average_book_cost,combined.book_cost/combined.shares_held)

    def test_currency_and_market_never_implicitly_combined(self):
        self.buy(self.toss)
        self.buy(self.toss,currency='KRW',market='KR')
        self.buy(self.other,currency='USD',market='OTHER')
        with self.assertRaises(PortfolioError):
            self.portfolio.get_unified_position('NVDA')
        with self.assertRaises(PortfolioError):
            self.portfolio.get_unified_position('NVDA',currency='USD')
        self.assertEqual(self.portfolio.get_unified_position('NVDA',currency='KRW',market='KR').shares_held,Decimal(10))

    def test_open_closed_positions_and_account_counts(self):
        self.buy(self.toss)
        self.portfolio.record_transaction('NVDA','SELL','10','200',currency='USD',market='US',**self.scope(self.toss))
        self.buy()
        self.assertEqual(self.portfolio.get_open_positions_by_account(self.toss.provider,self.toss.account_ref),())
        self.assertEqual(len(self.portfolio.get_positions_by_account(self.toss.provider,self.toss.account_ref)),1)
        counts = {a.identity.provider:a for a in self.portfolio.get_accounts_with_positions()}
        self.assertEqual(counts['TOSS'].closed_positions,1)
        self.assertEqual(counts['MANUAL'].open_positions,1)

    def test_external_reference_preserved_across_accounts(self):
        for identity in (self.toss,self.other):
            self.buy(identity,external_reference='synthetic-execution-id')
        for identity in (self.toss,self.other):
            trades = self.portfolio.get_transactions(**self.scope(identity))
            self.assertEqual(len(trades),1)
            self.assertEqual(trades[0].external_reference,'synthetic-execution-id')

    def test_cli_manual_default_and_broker_buy_show(self):
        self.assertEqual(self.command('buy','NVDA','3','100','--market','US')[0],0)
        self.assertEqual(self.command('buy','NVDA','4.063741','180','--market','US','--broker','TOSS','--account',self.toss.account_ref)[0],0)
        code,text = self.command('show','NVDA','--broker','TOSS','--account',self.toss.account_ref)
        self.assertEqual(code,0)
        self.assertIn('Shares remaining: 4.063741',text)
        self.assertIn('Shares remaining: 3',self.command('show','NVDA')[1])

    def test_cli_list_history_accounts_and_recent_scoped(self):
        self.buy(notes='manual-only-note')
        self.buy(self.toss,quantity='4',notes='toss-only-note')
        selector = ['--broker','TOSS','--account',self.toss.account_ref]
        for command in ('history','recent'):
            args = [command,'NVDA'] if command=='history' else [command]
            code,text = self.command(*args,*selector)
            self.assertEqual(code,0)
            self.assertIn('toss-only-note',text)
            self.assertNotIn('manual-only-note',text)
        code,text = self.command('list',*selector)
        self.assertEqual(code,0)
        self.assertNotIn('Manual',text)
        self.assertIn('Manual',self.command('list','--all-accounts')[1])
        code,text = self.command('accounts')
        self.assertEqual(code,0)
        self.assertIn('Open positions: 1',text)
        self.assertIn(self.toss.account_ref,text)

    def test_cli_invalid_selection_does_not_write(self):
        before = self.db.path.read_bytes()
        for extra in [('--broker','TOSS'),('--broker','TOSS','--account','98765432109')]:
            self.assertEqual(self.command('buy','NVDA','1','1',*extra)[0],1)
        self.assertEqual(self.command('list','--all-accounts','--broker','TOSS','--account',self.toss.account_ref)[0],1)
        self.assertEqual(before,self.db.path.read_bytes())

    def test_reconcile_ignores_manual_other_account_and_other_provider(self):
        self.buy(quantity='500')
        self.buy(self.other,quantity='600')
        self.buy(self.kiwoom,quantity='700')
        self.buy(self.toss,quantity='10')
        report = ReconciliationEngine().compare(self.portfolio.get_open_positions(),BrokerResult(snapshot(holding())))
        self.assertEqual(len(report.rows),1)
        self.assertEqual(report.rows[0].status,Status.MATCH)
        empty = ReconciliationEngine().compare(self.portfolio.get_open_positions(),BrokerResult(snapshot()))
        self.assertEqual(len(empty.rows),1)
        self.assertEqual(empty.rows[0].ledger_quantity,Decimal(10))
        self.assertEqual(empty.rows[0].status,Status.MAGI_ONLY)

    def test_broker_only_preview_never_writes_or_infers_executions(self):
        self.buy(quantity='500')
        before = self.db.path.read_bytes()
        report = ReconciliationEngine().compare(self.portfolio.get_open_positions(),BrokerResult(snapshot(holding(quantity='0.000311'))))
        self.assertEqual(report.rows[0].status,Status.BROKER_ONLY)
        preview = report.import_previews[0]
        self.assertEqual(preview.account.account_ref,self.toss.account_ref)
        self.assertEqual(preview.observed_quantity,Decimal('0.000311'))
        self.assertIsNone(preview.executed_at)
        self.assertIsNone(preview.execution_price)
        self.assertEqual(before,self.db.path.read_bytes())

    def test_market_valuation_preserves_selected_account(self):
        from magi.market.models import MarketResult, Quote
        from magi.market.valuation import PortfolioValuationService
        self.buy(quantity='100')
        self.buy(self.toss,quantity='4')
        market = Mock()
        market.get_quote.return_value = MarketResult(Quote(
            symbol='NVDA', market='US', currency='USD', price=Decimal('200'),
            timestamp=NOW, provider='FAKE', fetched_at=NOW))
        valuation = PortfolioValuationService(self.portfolio,market).get_position_with_market_data(
            'NVDA',market='US',currency='USD',**self.scope(self.toss))
        self.assertEqual(valuation.position.market_value,Decimal('800'))
        self.assertEqual(valuation.position.broker_account_ref,self.toss.account_ref)
        market.get_quote.assert_called_once_with('NVDA','US')

    def test_account_resolution_failure_is_unavailable(self):
        result = BrokerResult(replace(snapshot(holding()),account_id=''))
        report = ReconciliationEngine().compare((),result)
        self.assertFalse(report.available)
        self.assertEqual(report.status,Status.UNAVAILABLE)
        self.assertEqual(report.import_previews,())

    def test_invalid_cli_selection_does_not_initialize_database(self):
        with patch('magi.portfolio_cli.Portfolio',side_effect=AssertionError('Must validate before opening DB')):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(cli(['portfolio','buy','NVDA','1','100','--broker','TOSS']),1)

    def test_no_full_account_number_credentials_or_tokens_persisted(self):
        raw = '98765432109'
        identity = PortfolioAccountIdentity.from_broker(BrokerAccount(raw,'TOSS',NOW))
        self.buy(identity)
        for name in ('TEST_API_KEY','TEST_ACCESS_TOKEN','TEST_CLIENT_ID'):
            with patch.dict(os.environ,{name:'synthetic-sensitive-value'}):
                with self.assertRaises(StorageError):
                    self.buy(identity,notes='synthetic-sensitive-value')
        contents = self.db.path.read_bytes()
        for forbidden in (raw.encode(),b'synthetic-sensitive-value',b'Bearer '):
            self.assertNotIn(forbidden,contents)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        source = Database(self.root/'source.db')
        self.record = AnalysisMemory(source).save_analysis('Synthetic NVDA question',VotingEngine().vote(results('BUY','BUY','HOLD')),'Synthetic explanation')
        portfolio = Portfolio(source)
        portfolio.record_transaction('NVDA','BUY','4.063741','180',currency='USD',market='US',notes='original note',external_reference='execution-a',linked_analysis_run_id=self.record.run_id)
        portfolio.record_transaction('NVDA','SELL','0.000311','200',currency='USD',market='US')
        self.path = self.root/'legacy.db'
        self.original = {}
        with sqlite3.connect(self.path) as old, source.connect() as new:
            old.executescript((Path(__file__).parent/'fixtures/schema_v1.sql').read_text())
            for table in ('analysis_runs','analysis_agents','portfolio_transactions'):
                columns = [row[1] for row in old.execute('PRAGMA table_info('+table+')')]
                rows = [tuple(row) for row in new.execute('SELECT '+','.join(columns)+' FROM '+table)]
                old.executemany('INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',rows)
                self.original[table] = columns,rows

    def test_v1_migration_preserves_every_historical_field_and_analysis(self):
        database = Database(self.path)
        with database.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            for table,(columns,rows) in self.original.items():
                self.assertEqual([tuple(row) for row in connection.execute('SELECT '+','.join(columns)+' FROM '+table)],rows)
        self.assertEqual(AnalysisMemory(database).get_analysis(self.record.run_id),self.record)
        trades = Portfolio(database).get_transactions()
        self.assertTrue(all((t.broker_provider,t.broker_account_ref)==('MANUAL','DEFAULT') for t in trades))
        self.assertEqual(Portfolio(database).get_position('NVDA').shares_held,Decimal('4.063430'))
        new = Portfolio(database).record_transaction('NVDA','BUY','1','100',currency='USD',market='US')
        self.assertGreater(new.sequence,max(t.sequence for t in trades))
        for sql in ('UPDATE portfolio_transactions SET notes="changed"','DELETE FROM portfolio_transactions'):
            with self.assertRaises(StorageError), database.connect() as connection:
                connection.execute(sql)

    def test_migration_failure_rolls_back_columns_indexes_and_version(self):
        original = Database._execute_schema
        def fail(connection,path):
            original(connection,path)
            raise sqlite3.OperationalError('synthetic failure after DDL')
        with patch.object(Database,'_execute_schema',side_effect=fail), self.assertRaisesRegex(StorageError,'migration failed'):
            Database(self.path)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertNotIn('broker_provider',[row[1] for row in connection.execute('PRAGMA table_info(portfolio_transactions)')])
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='transaction_account_instrument'").fetchone())
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM portfolio_transactions').fetchone()[0],2)
        Database(self.path)

    def test_migration_idempotent(self):
        Database(self.path)
        before = self.path.read_bytes()
        Database(self.path)
        self.assertEqual(before,self.path.read_bytes())

    def test_unsupported_future_version_rejected_without_changes(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA user_version=999')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(StorageError,'Unsupported'):
            Database(self.path)
        self.assertEqual(before,self.path.read_bytes())

    def test_readonly_reconciliation_refuses_to_migrate_v1(self):
        before = self.path.read_bytes()
        with self.assertRaises(StorageError), ReadOnlyLedgerDatabase(self.path).connect():
            pass
        self.assertEqual(before,self.path.read_bytes())
