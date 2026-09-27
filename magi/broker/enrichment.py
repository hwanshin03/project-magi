"""Optional display-only quotes and FX, separate from broker snapshots and ledger."""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Optional
from magi.market.models import ErrorCode, FXRate, Quote, convert, currency
from .models import Holding


@dataclass(frozen=True)
class HoldingView:
    holding: Holding
    current_price: Optional[Decimal]
    market_value: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    unrealized_return: Optional[Decimal]
    price_source: Optional[str]
    quote: Optional[Quote] = None
    fx: Optional[FXRate] = None
    display_currency: Optional[str] = None
    display_price: Optional[Decimal] = None
    display_market_value: Optional[Decimal] = None
    quote_error: Optional[ErrorCode] = None
    fx_error: Optional[ErrorCode] = None
    is_stale: bool = False


def enrich_holding(holding, market_data, display_currency=None):
    display = currency(display_currency) if display_currency else None
    price, value = holding.current_price, holding.market_value
    pnl, rate = holding.unrealized_pnl, holding.unrealized_return
    quote = fx = quote_error = fx_error = None
    source = 'BROKER' if price is not None else None
    stale = holding.is_stale
    if price is None:
        result = market_data.get_quote(holding.symbol, holding.market)
        quote, quote_error = result.data, result.error
        if quote is not None:
            if (quote.symbol, quote.market, quote.currency) != (holding.symbol, holding.market, holding.currency):
                quote, quote_error = None, ErrorCode.INVALID_RESPONSE
            else:
                price, source = quote.price, 'MARKET_DATA'
                stale |= quote.is_stale
    with localcontext() as context:
        context.prec = 80
        if price is not None and (value is None or source == 'MARKET_DATA'):
            value = price * holding.quantity
            # Calculate display analytics from a single valuation price, not mixed snapshots.
            book = holding.book_cost
            if book is None and holding.average_cost is not None:
                book = holding.average_cost * holding.quantity
            pnl = value - book if book is not None else None
            rate = pnl / book if pnl is not None and book else None
    dp = dv = None
    if display == holding.currency:
        dp, dv = price, value
    elif display is not None and (price is not None or value is not None):
        result = market_data.get_fx_rate(holding.currency, display)
        fx, fx_error = result.data, result.error
        if fx is not None:
            if (fx.base_currency, fx.quote_currency) != (holding.currency, display):
                fx, fx_error = None, ErrorCode.INVALID_RESPONSE
            else:
                dp = convert(price, holding.currency, fx) if price is not None else None
                dv = convert(value, holding.currency, fx) if value is not None else None
                stale |= fx.is_stale
    return HoldingView(holding, price, value, pnl, rate, source, quote, fx, display,
                       dp, dv, quote_error, fx_error, stale)
