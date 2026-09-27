"""Toss market adapter; shared OAuth session, market-only request surface."""
from magi.toss import BASE_URL, TossSession, wire_errors
from .base import MarketDataProvider, MarketError
from .models import Candle, CandleSeries, ErrorCode, FXRate, Quote, amount, currency, instant, instrument

READ_PATHS = {'/api/v1/prices', '/api/v1/candles', '/api/v1/exchange-rate'}


class TossProvider(MarketDataProvider):
    name = 'TOSS'

    def __init__(self, *, session=None, **options):
        if session is not None and options:
            raise ValueError('Pass a shared session or session options, not both')
        self._session = session if session is not None else TossSession(**options)
        self._owns_session = session is None
        self._now = self._session._now

    def close(self):
        if self._owns_session:
            self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def _client_id(self):
        return self._session._client_id

    @_client_id.setter
    def _client_id(self, value):
        self._session._client_id = value

    def _send(self, method, path, **kwargs):
        if (method, path) != ('POST', '/oauth2/token') and not (method == 'GET' and path in READ_PATHS):
            raise ValueError('Endpoint is not allowed')
        return self._session._send(method, path, **kwargs)

    def _get(self, path, params):
        if path not in READ_PATHS:
            raise ValueError('Endpoint is not allowed')
        return self._session._get(path, params)

    @staticmethod
    def _instrument(symbol, market):
        symbol, market = instrument(symbol, market)
        if market not in ('US', 'KR'):
            raise MarketError(ErrorCode.UNSUPPORTED)
        return symbol, market

    @staticmethod
    def _currency(value, market):
        if value != {'US': 'USD', 'KR': 'KRW'}[market]:
            raise ValueError()
        return value

    def get_quote(self, symbol, market):
        return self.get_quotes([(symbol, market)])[0]

    @wire_errors
    def get_quotes(self, instruments):
        instruments = [self._instrument(*item) for item in instruments]
        quotes = []
        # Batch limits and wire symbol lists stay entirely within this adapter.
        for offset in range(0, len(instruments), 200):
            batch = instruments[offset:offset + 200]
            rows = self._get('/api/v1/prices', {'symbols': ','.join(dict.fromkeys(s for s, _ in batch))})
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError()
            by_symbol = {row['symbol']: row for row in rows}
            if len(by_symbol) != len(rows) or set(by_symbol) != {s for s, _ in batch}:
                raise ValueError()
            fetched = self._now()
            for symbol, market in batch:
                row = by_symbol[symbol]
                stamp = instant(row['timestamp']) if row.get('timestamp') is not None else None
                quotes.append(Quote(symbol, market, self._currency(row['currency'], market),
                                    amount(row['lastPrice']), stamp, self.name, fetched))
        return tuple(quotes)

    def get_daily_candles(self, symbol, market, limit=30):
        return self._candles(symbol, market, limit, '1d')

    def get_minute_candles(self, symbol, market, limit=30, interval='1m'):
        if interval != '1m':
            raise MarketError(ErrorCode.UNSUPPORTED)
        return self._candles(symbol, market, limit, interval)

    @wire_errors
    def _candles(self, symbol, market, limit, interval):
        symbol, market = self._instrument(symbol, market)
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError()
        candles, seen_cursors = {}, set()
        before = None
        # Bounded pagination, with duplicate boundary candles removed.
        for _ in range((limit + 198) // 199 + 1):
            params = {'symbol': symbol, 'interval': interval, 'count': min(200, limit - len(candles) + (1 if before else 0)), 'adjusted': 'true'}
            if before is not None:
                params['before'] = before
            data = self._get('/api/v1/candles', params)
            if not isinstance(data, dict) or not isinstance(data['candles'], list):
                raise ValueError()
            previous_size = len(candles)
            for row in data['candles']:
                stamp = instant(row['timestamp'])
                values = [amount(row[key]) for key in ('openPrice', 'highPrice', 'lowPrice', 'closePrice', 'volume')]
                op, high, low, close, volume = values
                if not low <= min(op, close) <= max(op, close) <= high:
                    raise ValueError()
                candle = Candle(symbol, market, self._currency(row['currency'], market), stamp,
                                op, high, low, close, volume, self.name)
                if stamp in candles and candles[stamp] != candle:
                    raise ValueError()
                candles[stamp] = candle
            before = data.get('nextBefore')
            if len(candles) >= limit or before is None:
                break
            instant(before)
            if before in seen_cursors or len(candles) == previous_size:
                raise ValueError()
            seen_cursors.add(before)
        else:
            raise ValueError()
        ordered = tuple(sorted(candles.values(), key=lambda c: c.timestamp)[-limit:])
        return CandleSeries(symbol, market, interval, ordered, self.name, self._now())

    @wire_errors
    def get_fx_rate(self, base_currency, quote_currency):
        base, quote = currency(base_currency), currency(quote_currency)
        if {base, quote} != {'USD', 'KRW'}:
            raise MarketError(ErrorCode.UNSUPPORTED)
        row = self._get('/api/v1/exchange-rate', {'baseCurrency': base, 'quoteCurrency': quote})
        if row['baseCurrency'] != base or row['quoteCurrency'] != quote:
            raise ValueError()
        rate = amount(row['rate'])
        start, end = instant(row['validFrom']), instant(row['validUntil'])
        if rate <= 0 or end <= start:
            raise ValueError()
        return FXRate(base, quote, rate, start, self.name, self._now(), end)
