"""Fixed Marketaux endpoints; credentials never cross the transport boundary."""
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote, unquote_plus

import httpx
from dotenv import load_dotenv

ENDPOINTS = frozenset(('/v1/news/all', '/v1/entity/search'))
HOST = 'https://api.marketaux.com'
ERRORS = {400: 'PARAMETER_ERROR', 401: 'AUTHENTICATION_ERROR', 402: 'USAGE_LIMIT',
          403: 'ACCESS_RESTRICTED', 404: 'NOT_FOUND', 429: 'RATE_LIMIT',
          500: 'TEMPORARILY_UNAVAILABLE', 503: 'TEMPORARILY_UNAVAILABLE'}
PROVIDER_CODES = frozenset(('malformed_parameters', 'invalid_api_token', 'usage_limit_reached',
    'endpoint_access_restricted', 'resource_not_found', 'rate_limit_reached',
    'internal_server_error', 'maintenance', 'server_error', 'invalid_api_function'))
_LOCK = threading.RLock()


class MarketauxError(Exception):
    def __init__(self, code, *, provider_code=None, status=None):
        self.code = 'MARKETAUX_' + code
        self.provider_code = provider_code if provider_code in PROVIDER_CODES else None
        self.status = status
        super().__init__(self.code)


class TTLCache:
    def __init__(self, capacity=128, ttl=300):
        self.capacity, self.ttl, self.values = capacity, ttl, OrderedDict()

    def get(self, key, now):
        item = self.values.get(key)
        if item is None:
            return None
        when, value = item
        if not 0 <= (now - when).total_seconds() < self.ttl:
            del self.values[key]
            return None
        self.values.move_to_end(key)
        return value

    def put(self, key, now, value):
        self.values[key] = (now, value)
        self.values.move_to_end(key)
        while len(self.values) > self.capacity:
            self.values.popitem(last=False)


class _Silent(logging.Filter):
    def filter(self, record):
        return False


@contextmanager
def _private_transport_logs():
    # HTTPX's normal request log includes the query credential. Bypass Client and
    # suppress library diagnostics for this synchronous request, including DEBUG.
    # Never install a global logger level or expose provider exception text.
    names = ('httpx', 'httpcore', 'httpcore.connection', 'httpcore.http11',
             'httpcore.http2', 'httpcore.proxy', 'httpcore.socks')
    with _LOCK:
        guard = _Silent()
        loggers = [logging.getLogger(name) for name in names]
        for logger in loggers:
            logger.addFilter(guard)
        try:
            yield
        finally:
            for logger in loggers:
                logger.removeFilter(guard)


class MarketauxTransport:
    def __init__(self, *, transport=None, now=None, sleep=time.sleep):
        # Only explicit construction loads local configuration; imports are inert.
        load_dotenv(Path(__file__).resolve().parents[4] / '.env')
        self._token = os.environ.get('MARKETAUX_API_TOKEN', '').strip()
        if not self._token:
            raise MarketauxError('CONFIGURATION_ERROR')
        self._transport = transport if transport is not None else httpx.HTTPTransport(retries=0, trust_env=False)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.status = {}

    def close(self):
        self._transport.close()

    def clean(self, value):
        """Reject echoed credentials anywhere, including encoded payload text."""
        if isinstance(value, str):
            for decode in (unquote, unquote_plus):
                candidate = value
                for _ in range(4):
                    if self._token in candidate or re.search(r'(?i)api[_-]?token\s*[=:]', candidate):
                        raise MarketauxError('INVALID_RESPONSE')
                    decoded = decode(candidate)
                    if decoded == candidate:
                        break
                    candidate = decoded
        elif isinstance(value, dict):
            for k, v in value.items():
                self.clean(k)
                self.clean(v)
        elif isinstance(value, (tuple, list)):
            for v in value:
                self.clean(v)
        return value

    def _status(self, headers):
        # Optional numeric hints, not a subscription contract. Unknown headers
        # and arbitrary provider text are never copied into status metadata.
        aliases = {
            'daily_usage_remaining': ('x-usagelimit-remaining', 'x-usage-limit-remaining', 'x-daily-limit-remaining'),
            'rate_limit_remaining': ('x-ratelimit-remaining', 'x-rate-limit-remaining'),
            'reset': ('x-ratelimit-reset', 'x-rate-limit-reset', 'x-usagelimit-reset'),
            'retry_after': ('retry-after',),
        }
        self.status = {}
        for field, names in aliases.items():
            for name in names:
                value = headers.get(name, '')
                if re.fullmatch(r'\d{1,12}', value) and self._token not in value:
                    self.status[field] = int(value)
                    break

    def request(self, endpoint, params):
        if endpoint not in ENDPOINTS or 'api_token' in params:
            raise MarketauxError('PARAMETER_ERROR')
        self.clean(params)
        result = None
        self.status = {}
        for attempt in range(3):
            failure = None
            try:
                request = httpx.Request('GET', HOST + endpoint,
                    params={**params, 'api_token': self._token},
                    headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'},
                    extensions={'timeout': dict(connect=15., read=15., write=15., pool=15.)})
                with _private_transport_logs():
                    response = self._transport.handle_request(request)
                    try:
                        self._status(response.headers)
                        status = response.status_code
                        if status != 200:
                            # Retain only a known code, never the error message or URL.
                            raw = self._read(response)
                            provider_code = None
                            try:
                                payload = json.loads(raw)
                                error = payload.get('error', {})
                                candidate = error.get('code') if isinstance(error, dict) else None
                                if isinstance(candidate, str) and candidate in PROVIDER_CODES and self._token not in candidate:
                                    provider_code = candidate
                            except (ValueError, AttributeError, TypeError):
                                pass
                            failure = MarketauxError(ERRORS.get(status, 'INVALID_RESPONSE'),
                                                     provider_code=provider_code, status=status)
                        else:
                            raw = self._read(response)
                            self.clean(raw.decode('utf-8'))
                            result = json.loads(raw)
                            self.clean(result)
                            if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                                failure = MarketauxError('INVALID_RESPONSE')
                    finally:
                        response.close()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, OSError):
                failure = MarketauxError('TEMPORARILY_UNAVAILABLE')
            except (ValueError, TypeError, UnicodeError, RecursionError, httpx.HTTPError):
                failure = MarketauxError('INVALID_RESPONSE')
            if failure is None:
                return result
            transient = failure.status in (429, 500, 503) or (
                failure.status is None and failure.code == 'MARKETAUX_TEMPORARILY_UNAVAILABLE')
            if attempt == 2 or not transient:
                break
            # Respect a modest Retry-After; large waits are returned to the caller.
            delay = max(2 ** attempt, self.status.get('retry_after', 0))
            if delay > 15:
                break
            self.sleep(delay)
        raise failure from None

    @staticmethod
    def _read(response):
        if response.is_stream_consumed:
            if len(response.content) > 2_000_000:
                raise MarketauxError('INVALID_RESPONSE')
            return response.content
        chunks, size = [], 0
        for chunk in response.iter_raw():
            size += len(chunk)
            if size > 2_000_000:
                raise MarketauxError('INVALID_RESPONSE')
            chunks.append(chunk)
        return b''.join(chunks)
