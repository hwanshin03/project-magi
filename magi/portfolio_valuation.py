"""Read-only valuation of ledger quantities; market snapshots never become events."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from decimal import Decimal, localcontext
from typing import Optional, Tuple
from magi.accounts import PortfolioAccountIdentity
from magi.portfolio import HistoryCompleteness
from magi.market.models import Quote, FXRate, MarketResult, ErrorCode, currency, utcnow
from magi.market.base import MarketError
from magi.broker.base import BrokerError
from magi.broker.reconciliation import ReconciliationEngine


class PriceRole(str, Enum):
    """Quote provenance, independent of provider identity and execution history."""
    BROKER_PRICE = 'BROKER_PRICE'
    REFERENCE_MARKET_PRICE = 'REFERENCE_MARKET_PRICE'
    UNSPECIFIED = 'UNSPECIFIED'


@dataclass(frozen=True)
class PortfolioValuation:
    broker_provider: str
    broker_account_ref: str = field(repr=False)
    symbol: str
    asset_name: Optional[str]
    market: str
    native_currency: str
    quantity: Decimal
    average_cost: Decimal
    remaining_book_cost: Decimal
    realized_pnl: Optional[Decimal]
    realized_return: Optional[Decimal]
    history_completeness: HistoryCompleteness
    tracking_start: Optional[str]
    original_purchase_date: Optional[str]
    pre_tracking_realized_pnl: Optional[Decimal]
    current_price: Optional[Decimal] = None
    market_value: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    unrealized_return: Optional[Decimal] = None
    quote_timestamp: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    is_stale: bool = False
    market_data_provider: Optional[str] = None
    price_role: PriceRole = PriceRole.UNSPECIFIED
    display_currency: Optional[str] = None
    fx_rate: Optional[Decimal] = None
    converted_price: Optional[Decimal] = None
    converted_market_value: Optional[Decimal] = None
    converted_unrealized_pnl: Optional[Decimal] = None
    converted_book_cost: Optional[Decimal] = None
    fx_timestamp: Optional[datetime] = None
    fx_is_stale: bool = False
    fx_source: Optional[str] = None
    reconciliation: str = 'UNVERIFIED'


@dataclass(frozen=True)
class ValuationTotal:
    currency: str
    book_cost: Optional[Decimal]
    market_value: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    complete: bool
    is_stale: bool
    converted: bool = False


@dataclass(frozen=True)
class PortfolioView:
    positions: Tuple[PortfolioValuation, ...]
    native_totals: Tuple[ValuationTotal, ...]
    converted_total: Optional[ValuationTotal]
    reconciliation_warnings: Tuple[str, ...] = ()


def _decimal(value):
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


class PortfolioValuationService:
    def __init__(self, portfolio, market, broker=None, *, now=utcnow, price_roles=None):
        self.portfolio, self.market, self.broker, self.now = portfolio, market, broker, now
        self.price_roles = {'TOSS': PriceRole.BROKER_PRICE}
        self.price_roles.update({provider: PriceRole(role) for provider, role in (price_roles or {}).items()})

    def _reconcile(self, positions):
        states, warnings = {}, []
        if not self.broker or not any(p.broker_provider != 'MANUAL' for p in positions):
            return states, warnings
        accounts = self.broker.get_accounts()
        if accounts.error or accounts.data is None or accounts.data.is_stale:
            return states, ['UNVERIFIED']
        scopes = {(p.broker_provider, p.broker_account_ref) for p in positions}
        for account in accounts.data.accounts:
            identity = PortfolioAccountIdentity.from_broker(account)
            scope = (identity.provider, identity.account_ref)
            if scope not in scopes or account.is_stale:
                continue
            result = self.broker.get_holdings(account)
            report = ReconciliationEngine().compare(positions, result)
            if not report.available:
                warnings.append('UNVERIFIED')
                continue
            for row in report.rows:
                states[(*scope, row.symbol, row.market, row.currency)] = row.status.value
                if row.status.value != 'MATCH':
                    warnings.append('MISMATCH')
        return states, warnings

    def value(self, *, display_currency=None, broker_provider=None, broker_account_ref=None):
        display = currency(display_currency) if display_currency else None
        filters = {} if broker_provider is None and broker_account_ref is None else dict(
            broker_provider=broker_provider, broker_account_ref=broker_account_ref)
        positions = self.portfolio.get_open_positions(**filters)
        try:
            states, warnings = self._reconcile(positions)
        except (BrokerError, ValueError):
            states, warnings = {}, ['UNVERIFIED']
        quotes, rates, rows = {}, {}, []
        with localcontext() as context:
            context.prec = 80
            for p in positions:
                key = (p.symbol, p.market)
                if key not in quotes:
                    try:
                        quotes[key] = self.market.get_quote(*key)
                    except (MarketError, ValueError):
                        quotes[key] = MarketResult(error=ErrorCode.UNAVAILABLE)
                result = quotes[key]
                q = result.data
                if not (isinstance(q, Quote) and (q.symbol, q.market, q.currency) ==
                        (p.symbol, p.market, p.currency) and _decimal(q.price)):
                    q = None
                events = self.portfolio.get_transactions(p.symbol, market=p.market, currency=p.currency,
                    broker_provider=p.broker_provider, broker_account_ref=p.broker_account_ref)
                name = next((e.asset_name for e in reversed(events) if e.asset_name), None)
                values = dict(broker_provider=p.broker_provider, broker_account_ref=p.broker_account_ref,
                    symbol=p.symbol, asset_name=name or (q.asset_name if q else None), market=p.market,
                    native_currency=p.currency, quantity=p.shares_held, average_cost=p.average_book_cost,
                    remaining_book_cost=p.book_cost, realized_pnl=p.realized_pnl, realized_return=p.realized_return,
                    history_completeness=p.history_completeness, tracking_start=p.tracking_start_date,
                    original_purchase_date=p.original_first_purchase_date,
                    pre_tracking_realized_pnl=p.pre_tracking_realized_pnl, display_currency=display,
                    reconciliation=states.get((p.broker_provider,p.broker_account_ref,p.symbol,p.market,p.currency),
                                              'NOT_APPLICABLE' if p.broker_provider == 'MANUAL' else 'UNVERIFIED'))
                if q:
                    value = p.shares_held * q.price
                    pnl = value - p.book_cost
                    values.update(current_price=q.price, market_value=value, unrealized_pnl=pnl,
                        unrealized_return=pnl/p.book_cost if p.book_cost else None,
                        quote_timestamp=q.timestamp, fetched_at=q.fetched_at,
                        is_stale=q.is_stale or bool(result.error), market_data_provider=q.provider,
                        price_role=self.price_roles.get(q.provider, PriceRole.UNSPECIFIED))
                rate, fx = None, None
                if display == p.currency:
                    rate = Decimal(1)
                elif display:
                    pair = (p.currency, display)
                    if pair not in rates:
                        try:
                            rates[pair] = self.market.get_fx_rate(*pair)
                        except (MarketError, ValueError):
                            rates[pair] = MarketResult(error=ErrorCode.UNAVAILABLE)
                    fx_result = rates[pair]
                    candidate = fx_result.data
                    if (isinstance(candidate, FXRate) and (candidate.base_currency,candidate.quote_currency)==pair
                            and _decimal(candidate.rate) and candidate.rate > 0):
                        fx, rate = candidate, candidate.rate
                        values.update(fx_timestamp=fx.timestamp, fx_source=fx.provider,
                            fx_is_stale=fx.is_stale or bool(fx_result.error) or
                            (fx.valid_until is not None and self.now() >= fx.valid_until))
                if rate is not None:
                    values.update(fx_rate=rate, converted_book_cost=p.book_cost*rate)
                    if q:
                        values.update(converted_price=q.price*rate, converted_market_value=value*rate,
                                      converted_unrealized_pnl=pnl*rate)
                rows.append(PortfolioValuation(**values))
            def total(group, cur, converted=False):
                def add(native, translated):
                    amounts = [getattr(p, translated if converted else native) for p in group]
                    return None if any(v is None for v in amounts) else sum(amounts, Decimal(0))
                book = add('remaining_book_cost','converted_book_cost')
                value = add('market_value','converted_market_value')
                pnl = add('unrealized_pnl','converted_unrealized_pnl')
                return ValuationTotal(cur,book,value,pnl,all(v is not None for v in (book,value,pnl)),
                    any(p.is_stale or (converted and p.fx_is_stale) for p in group),converted)
            native = tuple(total([p for p in rows if p.native_currency == cur],cur)
                           for cur in sorted({p.native_currency for p in rows}))
            unified = total(rows,display,True) if display else None
        return PortfolioView(tuple(rows),native,unified,tuple(sorted(set(warnings))))
