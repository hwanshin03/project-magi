"""Append-only trade ledger and weighted-average analytics; NOT tax accounting."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Optional, Union
from uuid import UUID, uuid4

from magi.accounts import PortfolioAccountIdentity, account_identity
from magi.memory import AnalysisMemory
from magi.storage import Database, StorageError, check_sensitive, timestamp

ZERO = Decimal('0')


class PortfolioError(ValueError):
    pass


class TradeAction(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'
    OPENING_BALANCE = 'OPENING_BALANCE'


class HistoryCompleteness(str, Enum):
    COMPLETE_HISTORY = 'COMPLETE_HISTORY'
    OPENING_BALANCE_HISTORY = 'OPENING_BALANCE_HISTORY'


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
    broker_provider: str = 'MANUAL'
    broker_account_ref: str = 'DEFAULT'


@dataclass(frozen=True)
class OpeningBalance:
    transaction_id: str
    timestamp: str  # Tracking as-of time; never an execution or original purchase date.
    recorded_at: str
    broker_provider: str
    broker_account_ref: str
    symbol: str
    market: str
    currency: str
    quantity: Decimal
    opening_unit_cost: Decimal
    opening_book_cost: Decimal
    asset_name: Optional[str] = None
    notes: str = ''
    external_reference: Optional[str] = None
    sequence: int = 0
    action: TradeAction = TradeAction.OPENING_BALANCE
    source: str = 'BROKER_SNAPSHOT'
    history_completeness: HistoryCompleteness = HistoryCompleteness.OPENING_BALANCE_HISTORY

    @property
    def as_of(self):
        return self.timestamp

    # Common ledger consumers must not mistake opening cost for an execution.
    price_per_share = None
    fees = None
    linked_analysis_run_id = None


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
    broker_provider: str = 'MANUAL'
    broker_account_ref: str = 'DEFAULT'
    history_completeness: HistoryCompleteness = HistoryCompleteness.COMPLETE_HISTORY
    tracking_start_date: Optional[str] = None
    first_recorded_buy_date: Optional[str] = None
    pre_tracking_realized_pnl: Optional[Decimal] = ZERO

    @property
    def original_first_purchase_date(self):
        return self.first_purchase_date


@dataclass(frozen=True)
class UnifiedPosition:
    symbol: str
    market: str
    currency: str
    shares_held: Decimal
    book_cost: Decimal
    average_book_cost: Decimal
    accounts: tuple


@dataclass(frozen=True)
class AccountPositions:
    identity: PortfolioAccountIdentity
    open_positions: int
    closed_positions: int


@dataclass(frozen=True)
class PositionHistory:
    transaction: Union[Transaction, OpeningBalance]
    position: PositionState


def _calculate(transactions, current_price=None):
    """Replay in execution order. Full closes release the entire remaining basis."""
    if not transactions:
        return ()
    price = None if current_price is None else decimal_value(current_price)
    quantity = book = purchased = sold = invested = proceeds = realized = basis_sold = ZERO
    first_purchase = first_recorded_buy = None
    completeness = HistoryCompleteness.COMPLETE_HISTORY
    tracking_start = transactions[0].timestamp
    history = []
    with localcontext() as context:
        context.prec = 80
        for trade in transactions:
            if trade.action == TradeAction.OPENING_BALANCE:
                if history:
                    raise PortfolioError('Opening balance must initialize an untracked instrument')
                quantity, book = trade.quantity, trade.opening_book_cost
                completeness = HistoryCompleteness.OPENING_BALANCE_HISTORY
            elif trade.action == TradeAction.BUY:
                gross = trade.quantity * trade.price_per_share
                first_recorded_buy = first_recorded_buy or trade.timestamp
                if completeness == HistoryCompleteness.COMPLETE_HISTORY:
                    first_purchase = first_purchase or trade.timestamp
                quantity += trade.quantity
                purchased += trade.quantity
                book += gross + trade.fees
                invested += gross + trade.fees
            else:
                gross = trade.quantity * trade.price_per_share
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
                price, market_value, pnl, pnl / book if pnl is not None and book else None,
                trade.broker_provider, trade.broker_account_ref, completeness, tracking_start,
                first_recorded_buy, None if completeness == HistoryCompleteness.OPENING_BALANCE_HISTORY else ZERO)
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
            if data['action'] == TradeAction.OPENING_BALANCE:
                raise ValueError()
            for key in ('quantity', 'price_per_share', 'fees'):
                data[key] = decimal_value(data[key])
            if data['quantity'] <= 0:
                raise ValueError()
            account_identity(data['broker_provider'], data['broker_account_ref'])
            check_sensitive(data)
            return Transaction(**data)
        except (ValueError, TypeError, KeyError):
            raise StorageError('Invalid stored transaction record.') from None

    def record_transaction(self, symbol, action, quantity, price_per_share, *, currency,
                           executed_at=None, fees='0', asset_name=None, market=None, notes='',
                           linked_analysis_run_id=None, external_reference=None,
                           broker_provider=None, broker_account_ref=None):
        identity = self._account(broker_provider, broker_account_ref)
        try:
            action = TradeAction(action)
            if action == TradeAction.OPENING_BALANCE:
                raise ValueError()
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
                            linked_analysis_run_id, external_reference, broker_provider=identity.provider,
                            broker_account_ref=identity.account_ref)
        check_sensitive(asdict(trade))
        with self.database.connect() as connection:
            # Serialize validation and insertion so concurrent sells cannot oversell.
            connection.execute('BEGIN IMMEDIATE')
            if linked_analysis_run_id is not None and not connection.execute(
                    'SELECT 1 FROM analysis_runs WHERE run_id = ?', (linked_analysis_run_id,)).fetchone():
                raise PortfolioError('Linked analysis does not exist')
            prior = list(self._read_events(connection,
                ' WHERE symbol = ? AND currency = ? AND market = ? AND broker_provider = ? AND broker_account_ref = ?',
                (symbol, currency, market, identity.provider, identity.account_ref)))
            if any(t.action == TradeAction.OPENING_BALANCE and trade.timestamp < t.timestamp for t in prior):
                raise PortfolioError('Trade precedes opening balance tracking start; historical backfill is unsupported')
            # New entries sort after already-recorded trades with identical timestamps.
            replay = sorted(prior + [trade], key=lambda t: t.timestamp)
            _calculate(replay)
            cursor = connection.execute(
                'INSERT INTO portfolio_transactions '
                '(transaction_id,timestamp,recorded_at,symbol,asset_name,market,currency,action,quantity,'
                'price_per_share,fees,notes,linked_analysis_run_id,external_reference,broker_provider,broker_account_ref) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (trade.transaction_id, trade.timestamp, trade.recorded_at, symbol, asset_name, market,
                 currency, action.value, str(quantity), str(price), str(fees), trade.notes,
                 linked_analysis_run_id, external_reference, identity.provider, identity.account_ref))
            row = connection.execute('SELECT * FROM portfolio_transactions WHERE sequence = ?',
                                     (cursor.lastrowid,)).fetchone()
            return self._decode(row)

    @staticmethod
    def _account(provider=None, account_ref=None):
        try:
            return account_identity(provider, account_ref)
        except ValueError:
            raise PortfolioError('Invalid account selection: specify both broker and safe account reference.') from None

    def get_transactions(self, symbol=None, *, currency=None, market=None,
                         broker_provider=None, broker_account_ref=None):
        """Return the ordered ledger: real transactions and explicit opening events."""
        clauses, values = [], []
        if broker_provider is not None or broker_account_ref is not None:
            identity = self._account(broker_provider, broker_account_ref)
            clauses.extend(['broker_provider = ?', 'broker_account_ref = ?'])
            values.extend([identity.provider, identity.account_ref])
        for key, value in [('symbol', symbol_value(symbol) if symbol is not None else None),
                           ('currency', currency.upper() if currency is not None else None),
                           ('market', market.upper() if market is not None else None)]:
            if value is not None:
                clauses.append(key + ' = ?')
                values.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.database.connect() as connection:
            return self._read_events(connection, where, values)

    def _read_events(self, connection, where='', values=()):
        trades = [self._decode(row) for row in connection.execute('SELECT * FROM portfolio_transactions' + where, values)]
        openings = [self._decode_opening(row) for row in connection.execute('SELECT * FROM portfolio_opening_balances' + where, values)]
        return tuple(sorted(trades + openings, key=lambda t: (t.timestamp, t.action != TradeAction.OPENING_BALANCE, t.sequence)))

    def _decode_opening(self, row):
        try:
            data = dict(row)
            data['timestamp'] = data.pop('as_of')
            UUID(data['transaction_id'])
            for key in ('timestamp', 'recorded_at'):
                if timestamp(data[key]) != data[key]:
                    raise ValueError()
            identity = account_identity(data['broker_provider'], data['broker_account_ref'])
            if identity.provider == 'MANUAL' or identity.provider != data['broker_provider']:
                raise ValueError()
            if symbol_value(data['symbol']) != data['symbol'] or not re.fullmatch('[A-Z]{3}', data['currency']):
                raise ValueError()
            if not data['market'] or data['market'] != data['market'].strip().upper():
                raise ValueError()
            for key in ('asset_name', 'external_reference'):
                if data[key] is not None and not isinstance(data[key], str):
                    raise ValueError()
            if not isinstance(data['notes'], str) or type(data['sequence']) is not int or data['sequence'] <= 0:
                raise ValueError()
            data['quantity'] = decimal_value(data['quantity'])
            data['opening_unit_cost'] = decimal_value(data['opening_unit_cost'])
            data['opening_book_cost'] = Decimal(data['opening_book_cost'])
            with localcontext() as context:
                context.prec = 80
                if data['quantity'] <= 0 or data['opening_book_cost'] != data['quantity'] * data['opening_unit_cost']:
                    raise ValueError()
            if (data['action'] != 'OPENING_BALANCE' or data['source'] != 'BROKER_SNAPSHOT'
                    or data['history_completeness'] != 'OPENING_BALANCE_HISTORY'):
                raise ValueError()
            data['action'] = TradeAction(data['action'])
            data['history_completeness'] = HistoryCompleteness(data['history_completeness'])
            check_sensitive(data)
            return OpeningBalance(**data)
        except (ValueError, TypeError, KeyError, InvalidOperation):
            raise StorageError('Invalid stored opening balance.') from None

    def record_opening_balance(self, symbol, quantity, opening_unit_cost, *, as_of, currency,
                               market, broker_provider, broker_account_ref, confirmed=False,
                               asset_name=None, notes='', external_reference=None):
        """Explicit local initialization only; imports validate broker freshness separately."""
        if confirmed is not True:
            raise PortfolioError('CONFIRMATION_REQUIRED: opening balance requires explicit confirmation')
        identity = self._account(broker_provider, broker_account_ref)
        if identity.provider == 'MANUAL':
            raise PortfolioError('IMPORT_NOT_ALLOWED: a broker account is required')
        symbol = symbol_value(symbol)
        if not isinstance(currency, str) or not re.fullmatch('[A-Za-z]{3}', currency):
            raise PortfolioError('Currency must be a three-letter code')
        if not isinstance(market, str) or not market.strip():
            raise PortfolioError('An explicit market is required')
        currency, market = currency.upper(), market.strip().upper()
        try:
            as_of = timestamp(as_of) if as_of is not None else None
            if as_of is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise PortfolioError('A valid opening balance as-of timestamp is required') from None
        quantity, unit_cost = decimal_value(quantity), decimal_value(opening_unit_cost)
        if quantity <= 0:
            raise PortfolioError('Quantity must be greater than zero')
        for value in (asset_name, notes, external_reference):
            if value is not None and not isinstance(value, str):
                raise PortfolioError('Optional metadata must be text')
        with localcontext() as context:
            context.prec = 80
            book = quantity * unit_cost
        key = (identity.provider, identity.account_ref, symbol, market, currency)
        if external_reference is None:
            external_reference = 'opening_' + hashlib.sha256(json.dumps(key).encode()).hexdigest()
        event = OpeningBalance(str(uuid4()), as_of, timestamp(), identity.provider,
                               identity.account_ref, symbol, market, currency, quantity,
                               unit_cost, book, asset_name, notes or '', external_reference)
        check_sensitive(asdict(event))
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            prior = self._read_events(connection,
                ' WHERE broker_provider = ? AND broker_account_ref = ? AND symbol = ? AND market = ? AND currency = ?', key)
            if prior:
                raise PortfolioError('ALREADY_TRACKED: opening balance cannot replace existing ledger history')
            cursor = connection.execute(
                'INSERT INTO portfolio_opening_balances '
                '(transaction_id,as_of,recorded_at,broker_provider,broker_account_ref,symbol,asset_name,market,currency,'
                'action,quantity,opening_unit_cost,opening_book_cost,source,history_completeness,notes,external_reference) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (event.transaction_id, event.as_of, event.recorded_at, identity.provider, identity.account_ref,
                 symbol, asset_name, market, currency, event.action.value, str(quantity), str(unit_cost), str(book),
                 event.source, event.history_completeness.value, event.notes, external_reference))
            return self._decode_opening(connection.execute(
                'SELECT * FROM portfolio_opening_balances WHERE sequence = ?', (cursor.lastrowid,)).fetchone())

    def _instrument_transactions(self, symbol, currency=None, market=None, broker_provider=None, broker_account_ref=None):
        trades = self.get_transactions(symbol, currency=currency, market=market,
                                       broker_provider=broker_provider, broker_account_ref=broker_account_ref)
        if len({(t.broker_provider, t.broker_account_ref, t.currency, t.market) for t in trades}) > 1:
            raise PortfolioError('Ambiguous instrument; specify broker, account, currency and market')
        return trades

    def get_position(self, symbol, current_price=None, *, currency=None, market=None,
                     broker_provider=None, broker_account_ref=None):
        trades = self._instrument_transactions(symbol, currency, market, broker_provider, broker_account_ref)
        if not trades:
            return None
        try:
            return _calculate(trades, current_price)[-1].position
        except PortfolioError:
            if current_price is not None:
                decimal_value(current_price)  # Expose invalid caller price as a validation error.
            raise StorageError('Stored ledger cannot produce a valid position.') from None

    def get_position_history(self, symbol, *, currency=None, market=None,
                             broker_provider=None, broker_account_ref=None):
        trades = self._instrument_transactions(symbol, currency, market, broker_provider, broker_account_ref)
        try:
            return _calculate(trades)
        except PortfolioError:
            raise StorageError('Stored ledger cannot produce a valid position history.') from None

    def _positions(self, opened, current_prices=None, **account_filters):
        groups = {}
        for trade in self.get_transactions(**account_filters):
            groups.setdefault((trade.broker_provider, trade.broker_account_ref, trade.symbol, trade.market, trade.currency), []).append(trade)
        result = []
        for key, trades in groups.items():
            price = (current_prices or {}).get(key)
            # Legacy quote lookup is price-only, never a cross-account aggregation.
            if price is None:
                price = (current_prices or {}).get((key[2], key[4], key[3]))
            if price is not None:
                decimal_value(price)
            try:
                position = _calculate(trades, price)[-1].position
            except PortfolioError:
                raise StorageError('Stored ledger cannot produce valid positions.') from None
            if opened is None or (position.shares_held > 0) == opened:
                result.append(position)
        return tuple(result)

    def get_open_positions(self, current_prices=None, **account_filters):
        return self._positions(True, current_prices, **account_filters)

    def get_closed_positions(self, **account_filters):
        return self._positions(False, **account_filters)

    def get_positions_by_account(self, broker_provider, broker_account_ref):
        identity = self._account(broker_provider, broker_account_ref)
        return self._positions(None, broker_provider=identity.provider, broker_account_ref=identity.account_ref)

    def get_open_positions_by_account(self, broker_provider, broker_account_ref):
        return tuple(p for p in self.get_positions_by_account(broker_provider, broker_account_ref) if p.shares_held > 0)

    def get_accounts_with_positions(self):
        groups = {}
        for position in self._positions(None):
            key = (position.broker_provider, position.broker_account_ref)
            groups.setdefault(key, []).append(position)
        return tuple(AccountPositions(self._account(*key), sum(p.shares_held>0 for p in rows),
                                      sum(p.shares_held==0 for p in rows)) for key, rows in sorted(groups.items()))

    def get_unified_position(self, symbol, *, currency=None, market=None):
        symbol = symbol_value(symbol)
        positions = tuple(p for p in self._positions(None) if p.symbol == symbol
                          and (currency is None or p.currency == currency.upper())
                          and (market is None or p.market == market.upper()))
        if not positions:
            return None
        if len({(p.market, p.currency) for p in positions}) != 1:
            raise PortfolioError('Ambiguous unified instrument; specify market and currency')
        with localcontext() as context:
            context.prec = 80
            quantity = sum((p.shares_held for p in positions), ZERO)
            book = sum((p.book_cost for p in positions), ZERO)
            return UnifiedPosition(symbol, positions[0].market, positions[0].currency,
                                   quantity, book, book/quantity if quantity else ZERO, positions)

    def get_first_purchase_date(self, symbol, **filters):
        trades = self._instrument_transactions(symbol, **filters)
        if any(t.action == TradeAction.OPENING_BALANCE for t in trades):
            return None
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
