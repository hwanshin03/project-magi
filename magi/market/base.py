"""Adapters normalize and validate wire data, raising only coded expected errors."""
from abc import ABC, abstractmethod
from .models import ErrorCode


class MarketError(Exception):
    def __init__(self, code=ErrorCode.UNAVAILABLE):
        self.code = ErrorCode(code)
        super().__init__(self.code.value)


class MarketDataProvider(ABC):
    @abstractmethod
    def get_quote(self, symbol, market):
        """Return Quote or raise MarketError. Market must be explicit."""

    def get_quotes(self, instruments):
        return tuple(self.get_quote(symbol, market) for symbol, market in instruments)

    @abstractmethod
    def get_daily_candles(self, symbol, market, limit=30):
        """Return CandleSeries in chronological order (possibly empty)."""

    @abstractmethod
    def get_minute_candles(self, symbol, market, limit=30, interval='1m'):
        """Return CandleSeries; unsupported intervals raise UNSUPPORTED."""

    @abstractmethod
    def get_fx_rate(self, base_currency, quote_currency):
        """Return FXRate in exactly the requested direction."""
