"""Optional read-only portfolio enrichment; never inserts or updates ledger rows."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from magi.portfolio import PortfolioError, PositionState
from .models import ErrorCode, FXRate, Quote, convert, currency as currency_value


@dataclass(frozen=True)
class PositionValuation:
    position: PositionState
    quote: Optional[Quote] = None
    fx: Optional[FXRate] = None
    display_currency: Optional[str] = None
    display_price: Optional[Decimal] = None
    display_market_value: Optional[Decimal] = None
    quote_error: Optional[ErrorCode] = None
    fx_error: Optional[ErrorCode] = None
    is_stale: bool = False


class PortfolioValuationService:
    def __init__(self, portfolio, market):
        self.portfolio, self.market = portfolio, market

    def get_position_with_market_data(self, symbol, *, market, currency=None, display_currency=None):
        base = self.portfolio.get_position(symbol, market=market, currency=currency)
        if base is None:
            return None
        display = currency_value(display_currency) if display_currency is not None else None
        result = self.market.get_quote(base.symbol, base.market)
        quote = result.data
        if quote is None:
            return PositionValuation(base, quote_error=result.error, display_currency=display)
        if (quote.symbol, quote.market, quote.currency) != (base.symbol, base.market, base.currency):
            return PositionValuation(base, quote_error=ErrorCode.INVALID_RESPONSE, display_currency=display)
        try:
            position = self.portfolio.get_position(base.symbol, current_price=quote.price,
                                                   currency=base.currency, market=base.market)
        except PortfolioError:
            return PositionValuation(base, quote_error=ErrorCode.INVALID_RESPONSE, display_currency=display)
        fx, fx_error = None, None
        price = value = None
        stale = quote.is_stale
        if display == base.currency:
            price, value = position.current_price, position.market_value
        elif display is not None:
            fx_result = self.market.get_fx_rate(base.currency, display)
            fx, fx_error = fx_result.data, fx_result.error
            if fx is not None:
                if (fx.base_currency, fx.quote_currency) != (base.currency, display):
                    fx, fx_error = None, ErrorCode.INVALID_RESPONSE
                else:
                    price = convert(position.current_price, base.currency, fx)
                    value = convert(position.market_value, base.currency, fx)
                    stale |= fx.is_stale
        return PositionValuation(position, quote, fx, display, price, value, result.error, fx_error, stale)
