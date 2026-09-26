"""Append-only trade ledger and weighted-average analytics; NOT tax accounting."""

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from magi.memory import AnalysisMemory
from magi.storage import Database, StorageError, check_sensitive, timestamp

ZERO = Decimal('0')


class PortfolioError(ValueError):
    pass


class TradeAction(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'


def decimal_value(value):
    """All arithmetic uses Decimal; floats are converted through their text form."""
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if (not result.is_finite() or result < 0 or len(result.as_tuple().digits) > 28
                or abs(result.as_tuple().exponent) > 18 or result > Decimal('1e28')):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise PortfolioError('Amounts must be finite nonnegative decimals (up to 28 digits, 18 decimal places).') from None


def symbol_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._^-]{0,31}', value.strip()):
        raise PortfolioError('A valid symbol is required')
    return value.strip().upper()


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    timestamp: str
    recorded_at: str
    symbol: str
    currency: str
    action: TradeAction
    quantity: Decimal
    price_per_share: Decimal
    fees: Decimal
    asset_name: Optional[str] = None
    market: str = ''
    notes: str = ''
    linked_analysis_run_id: Optional[str] = None
    external_reference: Optional[str] = None
    sequence: int = 0


@dataclass(frozen=True)
class PositionState:
    symbol: str
    currency: str
    market: str
    first_purchase_date: Optional[str]
    latest_transaction_date: Optional[str]
    total_shares_purchased: Decimal
    total_shares_sold: Decimal
    shares_held: Decimal
    average_book_cost: Decimal
    book_cost: Decimal
    total_capital_invested: Decimal
    total_sale_proceeds: Decimal
    realized_cost_basis: Decimal
    realized_pnl: Decimal
    realized_return: Optional[Decimal]
    current_price: Optional[Decimal]
    market_value: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    unrealized_return: Optional[Decimal]


@dataclass(frozen=True)
class PositionHistory:
    transaction: Transaction
    position: PositionState


def _calculate(transactions, current_price=None):
    """Replay in execution order. Full closes release the entire remaining basis."""
    if not transactions:
        return ()
    price = None if current_price is None else decimal_value(current_price)
    quantity = book = purchased = sold = invested = proceeds = realized = basis_sold = ZERO
    first_purchase = None
    history = []
    with localcontext() as context:
        context.prec = 80
        for trade in transactions:
            gross = trade.quantity * trade.price_per_share
            if trade.action == TradeAction.BUY:
                first_purchase = first_purchase or trade.timestamp
                quantity += trade.quantity
                purchased += trade.quantity
                book += gross + trade.fees
                invested += gross + trade.fees
            else:
                if trade.quantity > quantity:
                    raise PortfolioError('Sell exceeds holdings at its execution date; short positions are unsupported')
                released = book if trade.quantity == quantity else book * trade.quantity / quantity
                net = gross - trade.fees
                quantity -= trade.quantity
                sold += trade.quantity
                book -= released
                proceeds += net
                basis_sold += released
                realized += net - released
            market_value = None if price is None else price * quantity
            pnl = None if market_value is None else market_value - book
            state = PositionState(
                trade.symbol, trade.currency, trade.market, first_purchase, trade.timestamp,
                purchased, sold, quantity, book / quantity if quantity else ZERO, book,
                invested, proceeds, basis_sold, realized,
                realized / basis_sold if basis_sold else None,
                price, market_value, pnl, pnl / book if pnl is not None and book else None)
            history.append(PositionHistory(trade, state))
    return tuple(history)


class Portfolio:
    def __init__(self, database=None):
        self.database = database if database is not None else Database()

    def _decode(self, row):
        try:
            data = dict(row)
            UUID(data['transaction_id'])
            for key in ('timestamp', 'recorded_at'):
                if timestamp(data[key]) != data[key]:
                    raise ValueError()
            if symbol_value(data['symbol']) != data['symbol'] or not re.fullmatch('[A-Z]{3}', data['currency']):
                raise ValueError()
            if not isinstance(data['market'], str) or data['market'] != data['market'].strip().upper():
                raise ValueError()
            for key in ('asset_name', 'external_reference', 'linked_analysis_run_id'):
                if data[key] is not None and not isinstance(data[key], str):
                    raise ValueError()
            if not isinstance(data['notes'], str) or type(data['sequence']) is not int or data['sequence'] <= 0:
                raise ValueError()
            if data['linked_analysis_run_id'] is not None:
                UUID(data['linked_analysis_run_id'])
            data['action'] = TradeAction(data['action'])
            for key in ('quantity', 'price_per_share', 'fees'):
                data[key] = decimal_value(data[key])
            if data['quantity'] <= 0:
                raise ValueError()
            check_sensitive(data)
            return Transaction(**data)
        except (ValueError, TypeError, KeyError):
            raise StorageError('Invalid stored transaction record.') from None

    def record_transaction(self, symbol, action, quantity, price_per_share, *, currency,
                           executed_at=None, fees='0', asset_name=None, market=None, notes='',
                           linked_analysis_run_id=None, external_reference=None):
        try:
            action = TradeAction(action)
            executed_at = timestamp(executed_at)
        except (ValueError, TypeError):
            raise PortfolioError('Use BUY or SELL and a valid ISO date or timezone-aware timestamp') from None
        symbol = symbol_value(symbol)
        if not isinstance(currency, str) or not re.fullmatch('[A-Za-z]{3}', currency):
            raise PortfolioError('Currency must be a three-letter code')
        currency = currency.upper()
        if market is not None and (not isinstance(market, str) or not market.strip()):
            raise PortfolioError('Market must be nonempty text or None')
        market = market.strip().upper() if market else ''
        for value in (asset_name, notes, linked_analysis_run_id, external_reference):
            if value is not None and not isinstance(value, str):
                raise PortfolioError('Optional metadata must be text')
        quantity, price, fees = map(decimal_value, (quantity, price_per_share, fees))
        if quantity <= 0:
            raise PortfolioError('Quantity must be greater than zero')
        trade = Transaction(str(uuid4()), executed_at, timestamp(), symbol, currency, action,
                            quantity, price, fees, asset_name, market, notes or '',
                            linked_analysis_run_id, external_reference)
        check_sensitive(asdict(trade))
        with self.database.connect() as connection:
            # Serialize validation and insertion so concurrent sells cannot oversell.
            connection.execute('BEGIN IMMEDIATE')
            if linked_analysis_run_id is not None and not connection.execute(
                    'SELECT 1 FROM analysis_runs WHERE run_id = ?', (linked_analysis_run_id,)).fetchone():
                raise PortfolioError('Linked analysis does not exist')
            prior = [self._decode(row) for row in connection.execute(
                'SELECT * FROM portfolio_transactions WHERE symbol = ? AND currency = ? AND market = ? '
                'ORDER BY timestamp, sequence', (symbol, currency, market))]
            # New entries sort after already-recorded trades with identical timestamps.
            replay = sorted(prior + [trade], key=lambda t: t.timestamp)
            _calculate(replay)
            cursor = connection.execute(
                'INSERT INTO portfolio_transactions '
                '(transaction_id,timestamp,recorded_at,symbol,asset_name,market,currency,action,quantity,'
                'price_per_share,fees,notes,linked_analysis_run_id,external_reference) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (trade.transaction_id, trade.timestamp, trade.recorded_at, symbol, asset_name, market,
                 currency, action.value, str(quantity), str(price), str(fees), trade.notes,
                 linked_analysis_run_id, external_reference))
            row = connection.execute('SELECT * FROM portfolio_transactions WHERE sequence = ?',
                                     (cursor.lastrowid,)).fetchone()
            return self._decode(row)

    def get_transactions(self, symbol=None, *, currency=None, market=None):
        clauses, values = [], []
        for key, value in [('symbol', symbol_value(symbol) if symbol is not None else None),
                           ('currency', currency.upper() if currency is not None else None),
                           ('market', market.upper() if market is not None else None)]:
            if value is not None:
                clauses.append(key + ' = ?')
                values.append(value)
        query = 'SELECT * FROM portfolio_transactions'
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        with self.database.connect() as connection:
            return tuple(self._decode(row) for row in connection.execute(query + ' ORDER BY timestamp, sequence', values))

    def _instrument_transactions(self, symbol, currency=None, market=None):
        trades = self.get_transactions(symbol, currency=currency, market=market)
        if len({(t.currency, t.market) for t in trades}) > 1:
            raise PortfolioError('Ambiguous instrument; specify currency and market')
        return trades

    def get_position(self, symbol, current_price=None, *, currency=None, market=None):
        trades = self._instrument_transactions(symbol, currency, market)
        if not trades:
            return None
        try:
            return _calculate(trades, current_price)[-1].position
        except PortfolioError:
            if current_price is not None:
                decimal_value(current_price)  # Expose invalid caller price as a validation error.
            raise StorageError('Stored ledger cannot produce a valid position.') from None

    def get_position_history(self, symbol, *, currency=None, market=None):
        trades = self._instrument_transactions(symbol, currency, market)
        try:
            return _calculate(trades)
        except PortfolioError:
            raise StorageError('Stored ledger cannot produce a valid position history.') from None

    def _positions(self, opened, current_prices=None):
        groups = {}
        for trade in self.get_transactions():
            groups.setdefault((trade.symbol, trade.currency, trade.market), []).append(trade)
        result = []
        for key, trades in groups.items():
            price = (current_prices or {}).get(key)
            if price is not None:
                decimal_value(price)
            try:
                position = _calculate(trades, price)[-1].position
            except PortfolioError:
                raise StorageError('Stored ledger cannot produce valid positions.') from None
            if (position.shares_held > 0) == opened:
                result.append(position)
        return tuple(result)

    def get_open_positions(self, current_prices=None):
        return self._positions(True, current_prices)

    def get_closed_positions(self):
        return self._positions(False)

    def get_first_purchase_date(self, symbol, **filters):
        trades = self._instrument_transactions(symbol, **filters)
        return next((t.timestamp for t in trades if t.action == TradeAction.BUY), None)

    def get_latest_transaction(self, symbol, **filters):
        trades = self._instrument_transactions(symbol, **filters)
        return trades[-1] if trades else None

    def get_linked_analysis(self, transaction_id):
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM portfolio_transactions WHERE transaction_id = ?',
                                     (transaction_id,)).fetchone()
            trade = self._decode(row) if row else None
        return (AnalysisMemory(self.database).get_analysis(trade.linked_analysis_run_id)
                if trade and trade.linked_analysis_run_id else None)
