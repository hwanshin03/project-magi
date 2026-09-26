"""Decimal portfolio accounting over temporary append-only SQLite ledgers."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from decimal import Decimal as D
from pathlib import Path

from magi.portfolio import Portfolio, PortfolioError
from magi.storage import Database, StorageError


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(Path(directory.name) / 'magi.db')
        self.portfolio = Portfolio(self.db)

    def trade(self, action='BUY', quantity='10', price='100', day=1, symbol='NVDA', **kwargs):
        return self.portfolio.record_transaction(symbol, action, quantity, price, currency='USD',
                                                 executed_at=f'2026-01-{day:02d}', **kwargs)

    def test_buy_and_decimal_storage(self):
        trade = self.trade(asset_name='Nvidia', market='US', notes='Long term', external_reference='broker-1')
        self.assertEqual(self.portfolio.get_transactions(), (trade,))
        position = self.portfolio.get_position('nvda')
        self.assertEqual(position.shares_held, D('10'))
        self.assertEqual(position.average_book_cost, D('100'))
        self.assertEqual(position.total_capital_invested, D('1000'))
        self.assertEqual(position.realized_pnl, D('0'))
        self.assertIsNone(position.realized_return)
        self.assertIsNone(position.unrealized_pnl)
        self.assertIsNone(position.unrealized_return)
        self.assertIsNone(position.market_value)
        with self.db.connect() as connection:
            row = connection.execute('SELECT typeof(quantity), typeof(price_per_share), typeof(fees) FROM portfolio_transactions').fetchone()
            self.assertEqual(tuple(row), ('text', 'text', 'text'))

    def test_multiple_buys_weighted_average_and_fees(self):
        self.trade(quantity='10', price='100', fees='10')
        self.trade(quantity='10', price='200', fees='10', day=2)
        position = self.portfolio.get_position('NVDA')
        self.assertEqual(position.shares_held, D('20'))
        self.assertEqual(position.book_cost, D('3020'))
        self.assertEqual(position.average_book_cost, D('151'))
        self.assertEqual(position.total_capital_invested, D('3020'))

    def test_partial_sell_realized_and_unrealized_returns(self):
        self.trade(quantity='10', price='100', fees='10')
        self.trade(quantity='10', price='200', fees='10', day=2)
        self.trade('SELL', '5', '180', day=3, fees='5')
        position = self.portfolio.get_position('NVDA', current_price='160')
        self.assertEqual(position.total_shares_purchased, D('20'))
        self.assertEqual(position.total_shares_sold, D('5'))
        self.assertEqual(position.shares_held, D('15'))
        self.assertEqual(position.book_cost, D('2265'))
        self.assertEqual(position.average_book_cost, D('151'))
        self.assertEqual(position.total_sale_proceeds, D('895'))
        self.assertEqual(position.realized_cost_basis, D('755'))
        self.assertEqual(position.realized_pnl, D('140'))
        self.assertAlmostEqual(position.realized_return, D('140') / D('755'))
        self.assertEqual(position.market_value, D('2400'))
        self.assertEqual(position.unrealized_pnl, D('135'))
        self.assertAlmostEqual(position.unrealized_return, D('135') / D('2265'))

    def test_full_close_reopen_and_no_residual_basis(self):
        self.trade(quantity='3', price='1', fees='1')
        self.trade('SELL', '1', '2', day=2)
        self.trade('SELL', '2', '2', day=3)
        closed = self.portfolio.get_position('NVDA')
        self.assertEqual(closed.shares_held, D('0'))
        self.assertEqual(closed.book_cost, D('0'))
        self.assertEqual(closed.average_book_cost, D('0'))
        self.assertEqual(closed.realized_pnl, D('2'))
        self.assertEqual(closed.realized_return, D('0.5'))
        self.assertEqual(self.portfolio.get_open_positions(), ())
        self.assertEqual(self.portfolio.get_closed_positions(), (closed,))
        self.trade(quantity='1', price='20', day=4)
        reopened = self.portfolio.get_position('NVDA')
        self.assertEqual(reopened.average_book_cost, D('20'))
        self.assertEqual(reopened.realized_pnl, D('2'))
        self.assertEqual(reopened.first_purchase_date, closed.first_purchase_date)

    def test_history_execution_order_first_purchase_latest_sale(self):
        late = self.trade(day=3)
        first = self.trade(day=1)
        sale = self.trade('SELL', '3', '110', day=4)
        self.assertEqual(self.portfolio.get_transactions('NVDA'), (first, late, sale))
        self.assertEqual(self.portfolio.get_first_purchase_date('NVDA'), first.timestamp)
        self.assertEqual(self.portfolio.get_latest_transaction('NVDA'), sale)
        self.assertEqual(sale.quantity, D('3'))
        history = self.portfolio.get_position_history('NVDA')
        self.assertEqual([entry.position.shares_held for entry in history], [D('10'), D('20'), D('17')])
        self.assertEqual(history[-1].transaction.transaction_id, sale.transaction_id)

    def test_equal_timestamp_uses_append_sequence(self):
        buy = self.trade()
        sell = self.trade('SELL', '1', '110')
        self.assertEqual(self.portfolio.get_transactions(), (buy, sell))
        self.assertEqual(self.portfolio.get_position('NVDA').shares_held, D('9'))

    def test_multiple_symbols_and_external_prices(self):
        self.trade(symbol='NVDA')
        self.trade(symbol='TSLA', price='200')
        positions = self.portfolio.get_open_positions({('NVDA', 'USD', ''): '90'})
        self.assertEqual(len(positions), 2)
        nvda, tsla = positions
        self.assertEqual(nvda.unrealized_pnl, D('-100'))
        self.assertIsNone(tsla.unrealized_pnl)
        self.assertEqual(len(self.portfolio.get_transactions('TSLA')), 1)
        self.assertIsNone(self.portfolio.get_position('UNKNOWN'))
        self.assertIsNone(self.portfolio.get_first_purchase_date('UNKNOWN'))
        self.assertIsNone(self.portfolio.get_latest_transaction('UNKNOWN'))

    def test_currency_and_market_never_blended(self):
        self.trade(market='US')
        self.portfolio.record_transaction('NVDA', 'BUY', '2', '1000', currency='KRW', market='KR')
        with self.assertRaises(PortfolioError):
            self.portfolio.get_position('NVDA')
        self.assertEqual(self.portfolio.get_position('NVDA', currency='KRW', market='KR').shares_held, D('2'))
        self.assertEqual(len(self.portfolio.get_open_positions()), 2)

    def test_invalid_quantity_price_fees_action_and_time(self):
        for amount in ['0', '-1', 'NaN', 'Infinity', True]:
            with self.subTest(quantity=amount), self.assertRaises(PortfolioError):
                self.trade(quantity=amount)
        for field in ['price', 'fees']:
            for amount in ['-1', 'NaN', 'Infinity', 'not a number']:
                with self.subTest(field=field, amount=amount), self.assertRaises(PortfolioError):
                    self.trade(**{field: amount})
        for action in ['SHORT', 'DIVIDEND', 'buy']:
            with self.assertRaises(PortfolioError):
                self.trade(action=action)
        for date in ['not a date', '2026-02-30', '2026-01-01T10:00:00']:
            with self.assertRaises(PortfolioError):
                self.portfolio.record_transaction('NVDA', 'BUY', '1', '1', currency='USD', executed_at=date)
        self.assertEqual(self.portfolio.get_transactions(), ())

    def test_oversell_and_backdated_oversell_are_atomic(self):
        with self.assertRaises(PortfolioError):
            self.trade('SELL', '1', '100')
        self.trade(day=2)
        with self.assertRaises(PortfolioError):
            self.trade('SELL', '11', '100', day=3)
        with self.assertRaises(PortfolioError):
            self.trade('SELL', '1', '100', day=1)
        self.trade('SELL', '10', '100', day=4)
        # Inserting an earlier sale would make the already-recorded later sale invalid.
        with self.assertRaises(PortfolioError):
            self.trade('SELL', '1', '100', day=3)
        self.assertEqual(len(self.portfolio.get_transactions()), 2)

    def test_fractional_shares_exact_decimal_arithmetic(self):
        self.trade(quantity='0.1', price='0.2', fees='0.01')
        self.assertEqual(self.portfolio.get_position('NVDA').book_cost, D('0.03'))
        self.assertEqual(self.portfolio.get_position('NVDA', current_price=0.5).unrealized_pnl, D('0.02'))
        with self.assertRaises(PortfolioError):
            self.portfolio.get_position('NVDA', current_price=-1)

    def test_zero_basis_and_sales_fee_loss(self):
        self.trade(quantity='1', price='0')
        self.assertIsNone(self.portfolio.get_position('NVDA', current_price='10').unrealized_return)
        self.trade('SELL', '1', '0', day=2, fees='1')
        closed = self.portfolio.get_position('NVDA')
        self.assertEqual(closed.realized_pnl, D('-1'))
        self.assertIsNone(closed.realized_return)

    def test_ledger_append_only_and_invalid_stored_record(self):
        trade = self.trade()
        for sql in ['UPDATE portfolio_transactions SET quantity = ?', 'DELETE FROM portfolio_transactions WHERE quantity = ?']:
            with self.assertRaises(StorageError), self.db.connect() as connection:
                connection.execute(sql, ('10',))
        self.assertEqual(self.portfolio.get_transactions(), (trade,))
        with self.db.connect() as connection:
            connection.execute('DROP TRIGGER transaction_no_update')
            connection.execute("UPDATE portfolio_transactions SET quantity = 'NaN'")
        with self.assertRaises(StorageError):
            self.portfolio.get_transactions()

    def test_database_failure_sanitized(self):
        with self.db.connect() as connection:
            connection.execute('DROP TABLE portfolio_transactions')
        with self.assertRaisesRegex(StorageError, '^Memory database operation failed.$'):
            self.portfolio.get_transactions()

    def test_concurrent_sells_cannot_both_consume_same_shares(self):
        self.trade()
        barrier = Barrier(2)
        def sell():
            barrier.wait(timeout=5)
            try:
                self.trade('SELL', '7', '110', day=2)
                return True
            except PortfolioError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(sell) for _ in range(2)]
            outcomes = [future.result(timeout=5) for future in futures]
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(self.portfolio.get_position('NVDA').shares_held, D('3'))
        self.assertEqual(len(self.portfolio.get_transactions()), 2)


if __name__ == '__main__':
    unittest.main()
