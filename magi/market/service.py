"""Bounded in-memory snapshot cache. Expected outages never escape this boundary."""
from collections import OrderedDict
from dataclasses import replace
import time
from .base import MarketError
from .models import ErrorCode, FXRate, Quote, MarketResult, currency, instrument, utcnow


class MarketDataService:
    def __init__(self, provider, *, clock=time.monotonic, now=utcnow, max_entries=256):
        if max_entries < 1:
            raise ValueError('Cache capacity must be positive')
        self.provider, self.clock, self.now = provider, clock, now
        self.max_entries = max_entries
        self._cache = OrderedDict()

    def _freshness(self, data, ttl):
        now = self.now()
        stale = data.is_stale or (now - data.fetched_at).total_seconds() >= ttl
        if isinstance(data, Quote):
            stale |= data.timestamp is None or (now - data.timestamp).total_seconds() > 60
        if isinstance(data, FXRate):
            stale |= now < data.timestamp or (data.valid_until is not None and now >= data.valid_until)
        return replace(data, is_stale=stale)

    def _get(self, key, ttl, call):
        cached = self._cache.get(key)
        if cached and self.clock() - cached[0] < ttl:
            self._cache.move_to_end(key)
            data = self._freshness(cached[1], ttl)
            return MarketResult(data)
        try:
            data = self._freshness(call(), ttl)
        except MarketError as error:
            return MarketResult(replace(cached[1], is_stale=True) if cached else None, error.code)
        self._cache[key] = (self.clock(), data)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return MarketResult(data)

    def get_quote(self, symbol, market):
        symbol, market = instrument(symbol, market)
        return self._get(('quote', symbol, market), 15, lambda: self.provider.get_quote(symbol, market))

    def get_quotes(self, instruments):
        """Batch only cache misses; callers never see provider batch-size limits."""
        items = tuple(instrument(*item) for item in instruments)
        missing = tuple(dict.fromkeys(item for item in items if (
            ('quote', *item) not in self._cache
            or self.clock() - self._cache[('quote', *item)][0] >= 15)))
        cached_results = {item: MarketResult(self._freshness(self._cache[('quote', *item)][1], 15))
                          for item in items if item not in missing}
        fetched, failure = {}, None
        if missing:
            try:
                values = self.provider.get_quotes(missing)
                if len(values) != len(missing) or any(
                        (value.symbol, value.market) != item for item, value in zip(missing, values)):
                    raise MarketError(ErrorCode.INVALID_RESPONSE)
                fetched = dict(zip(missing, values))
            except MarketError as error:
                failure = error.code
        def resolve(item):
            if failure is not None:
                raise MarketError(failure)
            return fetched[item]
        return tuple(cached_results[item] if item in cached_results else
                     self._get(('quote', *item), 15, lambda item=item: resolve(item)) for item in items)

    def get_daily_candles(self, symbol, market, limit=30):
        return self._candles(symbol, market, limit, '1d')

    def get_minute_candles(self, symbol, market, limit=30, interval='1m'):
        if interval != '1m':
            return MarketResult(error=ErrorCode.UNSUPPORTED)
        return self._candles(symbol, market, limit, interval)

    def _candles(self, symbol, market, limit, interval):
        symbol, market = instrument(symbol, market)
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Candle limit must be between 1 and 10000')
        call = (lambda: self.provider.get_daily_candles(symbol, market, limit)) if interval == '1d' else (
            lambda: self.provider.get_minute_candles(symbol, market, limit, interval))
        return self._get(('candles', symbol, market, limit, interval), 300 if interval == '1d' else 15, call)

    def get_fx_rate(self, base_currency, quote_currency):
        base, quote = currency(base_currency), currency(quote_currency)
        return self._get(('fx', base, quote), 30, lambda: self.provider.get_fx_rate(base, quote))
