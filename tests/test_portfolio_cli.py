"""Offline CLI tests over real temporary SQLite databases, without AI clients."""

import io
import runpy
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import main as application
from magi.memory import AnalysisMemory
from magi.portfolio import Portfolio
from magi.portfolio_cli import main as portfolio_cli
from magi.storage import Database, StorageError
from magi.voting import VotingEngine
from test_resilience import fake_clients
from test_voting import results


class PortfolioCLITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(Path(directory.name) / 'magi.db')
        self.portfolio = Portfolio(self.db)
        for target in ['socket.socket.connect', 'socket.create_connection', 'httpx.Client.send']:
            guard = patch(target, side_effect=AssertionError('Network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = portfolio_cli(['portfolio', *args], portfolio=self.portfolio)
            except SystemExit as error:
                code = error.code
        return code, out.getvalue(), err.getvalue()

    def buy(self, symbol='NVDA', quantity='10', price='180', *options):
        code, out, err = self.command('buy', symbol, quantity, price, *options)
        self.assertEqual(code, 0, err)
        return out

    def test_basic_buy_defaults_and_current_timestamp(self):
        before = datetime.now(timezone.utc)
        out = self.buy('nvda')
        trade = self.portfolio.get_transactions()[0]
        self.assertEqual(trade.symbol, 'NVDA')
        self.assertEqual(trade.currency, 'USD')
        self.assertEqual(trade.market, '')
        self.assertEqual(trade.quantity, Decimal('10'))
        self.assertGreaterEqual(datetime.fromisoformat(trade.timestamp), before)
        self.assertIn('BUY 10 NVDA @ 180 USD', out)
        self.assertIn('Shares remaining: 10', out)
        self.assertIn('Current price: NOT PROVIDED', out)

    def test_buy_date_fees_asset_name_and_note(self):
        out = self.buy('NVDA', '10', '180', '--date', '2026-09-26', '--fees', '1.25',
                       '--market', 'US', '--asset-name', 'Nvidia', '--note', 'Initial position')
        trade = self.portfolio.get_transactions()[0]
        self.assertEqual(trade.timestamp, '2026-09-26T00:00:00.000000+00:00')
        self.assertEqual(trade.notes, 'Initial position')
        self.assertIn('Average book cost: 180.125', out)
        self.assertIn('Remaining book cost: 1801.25', out)
        self.assertIn('Fees: 1.25 USD', out)
        self.assertIn('Asset name: Nvidia', out)
        self.assertIn('Asset name: Nvidia', self.command('show', 'NVDA')[1])

    def test_krw_and_numeric_symbol_no_currency_inference(self):
        self.buy('005930', '20', '72000', '--currency', 'krw', '--market', 'kr')
        self.assertEqual(self.portfolio.get_transactions()[0].currency, 'KRW')
        code, out, err = self.command('show', '005930', '--currency', 'KRW', '--market', 'KR')
        self.assertEqual(code, 0, err)
        self.assertIn('Currency: KRW', out)
        self.assertIn('Market: KR', out)
        self.buy('000660', '1', '100')
        self.assertEqual(self.portfolio.get_transactions('000660')[0].currency, 'USD')

    def test_partial_sell_fees_and_realized_output(self):
        self.buy('NVDA', '10', '180', '--date', '2026-09-10', '--fees', '1.25')
        code, out, err = self.command('sell', 'NVDA', '4', '210', '--date', '2026-09-20', '--fees', '1')
        self.assertEqual(code, 0, err)
        for fragment in ['SELL 4 NVDA @ 210 USD', 'Shares remaining: 6',
                         'Average book cost: 180.125', 'Remaining book cost: 1080.75',
                         'Realized P/L: +118.5 USD', 'Realized return: +16.45%']:
            self.assertIn(fragment, out)
        self.assertEqual(self.portfolio.get_position('NVDA').realized_pnl, Decimal('118.50'))

    def test_full_sell_closed_show_and_list(self):
        self.buy()
        code, out, err = self.command('sell', 'NVDA', '10', '200')
        self.assertEqual(code, 0, err)
        self.assertIn('Shares remaining: 0', out)
        self.assertIn('Remaining book cost: 0', out)
        self.assertIn('Realized P/L: +200 USD', out)
        self.assertIn('Shares remaining: 0', self.command('show', 'NVDA')[1])
        self.assertIn('No open positions.', self.command('list')[1])
        self.assertIn('NVDA', self.command('list', '--closed')[1])

    def test_oversell_records_nothing(self):
        self.buy(quantity='8')
        before = self.portfolio.get_transactions()
        code, out, err = self.command('sell', 'NVDA', '20', '200')
        self.assertEqual(code, 1)
        self.assertIn('Cannot sell 20 shares of NVDA', err)
        self.assertIn('No transaction was recorded.', err)
        self.assertEqual(self.portfolio.get_transactions(), before)
        self.assertNotIn('RECORDED TRANSACTION', out)

    def test_backdated_oversell_is_still_rejected(self):
        self.buy('NVDA', '10', '180', '--date', '2026-09-10')
        code, _, err = self.command('sell', 'NVDA', '1', '200', '--date', '2026-09-09')
        self.assertEqual(code, 1)
        self.assertIn('execution order', err)
        self.assertEqual(len(self.portfolio.get_transactions()), 1)

    def test_show_missing_price_gain_loss_and_zero(self):
        self.buy()
        code, out, err = self.command('show', 'NVDA')
        self.assertEqual(code, 0, err)
        for fragment in ['Total shares purchased: 10', 'Total shares sold: 0', 'Currency: USD',
                         'First purchase:', 'Latest transaction:', 'Current price: NOT PROVIDED',
                         'Market value: N/A', 'Unrealized P/L: N/A', 'Unrealized return: N/A']:
            self.assertIn(fragment, out)
        for price, market_value, pnl, rate in [('202.50', '2025', '+225', '+12.50%'),
                                               ('150', '1500', '-300', '-16.67%'),
                                               ('0', '0', '-1800', '-100.00%')]:
            code, out, err = self.command('show', 'NVDA', '--current-price', price)
            self.assertEqual(code, 0, err)
            self.assertIn('Market value: ' + market_value, out)
            self.assertIn('Unrealized P/L: ' + pnl, out)
            self.assertIn('Unrealized return: ' + rate, out)

    def test_list_multiple_markets_and_currencies_without_totals(self):
        self.buy('NVDA', '10', '180', '--market', 'US')
        self.buy('NVDA', '2', '100', '--market', 'OTHER')
        self.buy('005930', '20', '72000', '--currency', 'KRW', '--market', 'KR')
        self.buy('AAPL', '5', '195')
        self.command('sell', 'AAPL', '5', '195')
        code, out, err = self.command('list')
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count('NVDA'), 2)
        for fragment in ['USD', 'KRW', 'US', 'OTHER', 'KR', 'REALIZED P/L', '005930']:
            self.assertIn(fragment, out)
        for absent in ['AAPL', 'TOTAL', 'Market value', 'Unrealized']:
            self.assertNotIn(absent, out)
        for command in ['show', 'history']:
            code, _, err = self.command(command, 'NVDA')
            self.assertEqual(code, 1)
            self.assertIn('Ambiguous instrument', err)
            self.assertEqual(self.command(command, 'NVDA', '--market', 'US')[0], 0)

    def test_history_order_ids_fees_and_link(self):
        record = AnalysisMemory(self.db).save_analysis('NVDA', VotingEngine().vote(results('BUY','BUY','HOLD')))
        self.buy('NVDA', '1', '180', '--date', '2026-09-20', '--fees', '1', '--run-id', record.run_id)
        self.buy('NVDA', '1', '170', '--date', '2026-09-10', '--fees', '0.5')
        before = self.portfolio.get_transactions()
        code, out, err = self.command('history', 'NVDA')
        self.assertEqual(code, 0, err)
        self.assertLess(out.index('2026-09-10'), out.index('2026-09-20'))
        for trade in before:
            self.assertIn(trade.transaction_id, out)
        for fragment in [record.run_id, 'Fees: 1 USD', 'Fees: 0.5 USD', 'Market: UNSET']:
            self.assertIn(fragment, out)
        self.assertEqual(self.portfolio.get_transactions(), before)

    def test_linked_buy_and_sell_display_analysis(self):
        record = AnalysisMemory(self.db).save_analysis('NVDA', VotingEngine().vote(results('BUY','BUY','HOLD')))
        out = self.buy('NVDA', '10', '180', '--run-id', record.run_id)
        for fragment in [record.run_id, 'Final action: BUY_APPROVED', 'Melchior: BUY', 'Casper: HOLD']:
            self.assertIn(fragment, out)
        code, out, err = self.command('sell', 'NVDA', '1', '200', '--run-id', record.run_id, '--note', 'Rebalance')
        self.assertEqual(code, 0, err)
        self.assertIn('LINKED MAGI ANALYSIS', out)
        self.assertEqual(self.portfolio.get_transactions()[-1].linked_analysis_run_id, record.run_id)

    def test_recent_default_custom_limit_and_execution_order(self):
        for day in range(12, 0, -1):
            self.buy('NVDA', '1', '180', '--date', f'2026-09-{day:02d}')
        code, out, err = self.command('recent')
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count('Transaction ID:'), 10)
        self.assertLess(out.index('2026-09-12'), out.index('2026-09-03'))
        self.assertNotIn('2026-09-02', out)
        self.assertEqual(self.command('recent', '--limit', '2')[1].count('Transaction ID:'), 2)
        self.assertEqual(self.command('recent', '--limit', '20')[1].count('Transaction ID:'), 12)

    def test_recent_ties_reverse_append_order_across_symbols(self):
        self.buy('NVDA', '1', '180', '--date', '2026-09-10')
        self.buy('AAPL', '1', '195', '--date', '2026-09-10')
        out = self.command('recent')[1]
        self.assertLess(out.index('AAPL'), out.index('NVDA'))

    def test_expected_validation_errors_leave_ledger_empty(self):
        cases = [
            (['buy','NVDA','0','180'], 'Quantity must be greater than zero.'),
            (['buy','NVDA','-1','180'], 'Quantity must be greater than zero.'),
            (['buy','NVDA','1','-1'], 'Price cannot be negative.'),
            (['buy','NVDA','1','180','--fees','-1'], 'Fees cannot be negative.'),
            (['buy','NVDA','1','180','--date','2026-02-30'], 'Date must use YYYY-MM-DD format.'),
            (['buy','NVDA','1','180','--date','2026-9-1'], 'Date must use YYYY-MM-DD format.'),
            (['buy','NVDA','1','180','--date','2026-09-01T12:00:00Z'], 'Date must use YYYY-MM-DD format.'),
            (['buy','NVDA','NaN','180'], 'finite decimal'),
            (['buy','NVDA','1','Infinity'], 'finite decimal'),
            (['buy','NVDA','1','180','--currency','DOLLARS'], 'three-letter code'),
            (['recent','--limit','0'], 'Limit must be a positive integer.'),
            (['recent','--limit','-2'], 'Limit must be a positive integer.'),
            (['recent','--limit','1.5'], 'Limit must be a positive integer.'),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                code, out, err = self.command(*args)
                self.assertEqual(code, 2)
                self.assertIn(expected, err)
                self.assertNotIn('Traceback', err)
        self.assertEqual(self.portfolio.get_transactions(), ())

    def test_missing_analysis_records_nothing(self):
        code, _, err = self.command('buy', 'NVDA', '1', '180', '--run-id', 'abc123')
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), 'Analysis run abc123 was not found.')
        self.assertEqual(self.portfolio.get_transactions(), ())

    def test_empty_portfolio_help_and_nonexistent_show(self):
        self.assertIn('No open positions.', self.command('list')[1])
        self.assertIn('No transactions found.', self.command('history', 'NVDA')[1])
        self.assertIn('No transactions found.', self.command('recent')[1])
        self.assertEqual(self.command('show', 'NVDA')[0], 1)
        self.assertEqual(self.command('--help')[0], 0)
        self.assertEqual(self.command('unknown')[0], 2)

    def test_storage_errors_are_readable_and_programming_errors_propagate(self):
        with patch.object(self.portfolio, 'get_transactions', side_effect=StorageError('private details')):
            code, _, err = self.command('recent')
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), 'Portfolio memory is currently unavailable.')
        with patch.object(self.portfolio, 'get_transactions', side_effect=RuntimeError('programming bug')):
            with self.assertRaises(RuntimeError):
                self.command('recent')

    def test_successful_write_followed_by_read_failure_does_not_invite_duplicate(self):
        with patch.object(self.portfolio, 'get_position', side_effect=StorageError('read failed')):
            code, out, err = self.command('buy', 'NVDA', '1', '180')
        self.assertEqual(code, 0)
        self.assertIn('RECORDED TRANSACTION', out)
        self.assertIn('Do not re-record this trade.', err)
        self.assertEqual(len(self.portfolio.get_transactions()), 1)

    def test_portfolio_dispatch_never_creates_agents_or_calls_analysis(self):
        with ExitStack() as stack:
            stack.enter_context(patch('magi.portfolio_cli.Portfolio', return_value=self.portfolio))
            for target in ['main.main', 'magi.melchior.OpenAI', 'magi.balthasar.genai.Client', 'magi.casper.Anthropic']:
                stack.enter_context(patch(target, side_effect=AssertionError('AI access forbidden')))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(application.cli(['portfolio', 'buy', 'NVDA', '1', '180']), 0)
        self.assertEqual(len(self.portfolio.get_transactions()), 1)

    def test_main_script_without_arguments_still_runs_analysis(self):
        memory = AnalysisMemory(self.db)
        with fake_clients(), patch('magi.memory.AnalysisMemory', return_value=memory), \
                patch('sys.argv', ['main.py']), patch('builtins.input', return_value='offline question'):
            out = io.StringIO()
            with redirect_stdout(out), self.assertRaises(SystemExit) as exited:
                runpy.run_path(str(Path(application.__file__)), run_name='__main__')
        self.assertEqual(exited.exception.code, 0)
        self.assertIn('Project MAGI started', out.getvalue())
        self.assertIn('DEBATE ROUND 3', out.getvalue())
        self.assertIn('FINAL ACTION: HOLD (deterministic)', out.getvalue())
        self.assertEqual(len(memory.recent()), 1)


if __name__ == '__main__':
    unittest.main()
