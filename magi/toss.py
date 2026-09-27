"""Shared in-memory Toss OAuth and transport; fixed read-only endpoint allowlist."""
import json
import os
import random
import re
import threading
import time
from functools import wraps
from pathlib import Path

import httpx
from dotenv import load_dotenv

from magi.market.base import MarketError
from magi.market.models import ErrorCode, utcnow

BASE_URL = 'https://openapi.tossinvest.com'
READ_PATHS = frozenset({'/api/v1/prices', '/api/v1/candles', '/api/v1/exchange-rate',
              '/api/v1/accounts', '/api/v1/holdings'})
TRANSIENT = {429, 500, 502, 503, 504}


def wire_errors(method):
    """Only parsing/validation errors are sanitized; programming errors propagate."""
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (ValueError, KeyError, TypeError, OverflowError):
            raise MarketError(ErrorCode.INVALID_RESPONSE) from None
    return wrapped


class TossSession:
    def __init__(self, *, client_id=None, client_secret=None, transport=None,
                 clock=time.monotonic, now=utcnow, sleep=time.sleep, jitter=None):
        # Environment loading is explicit construction only; never modifies .env.
        if client_id is None or client_secret is None:
            load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
        self._client_id = client_id if client_id is not None else os.getenv('TOSS_CLIENT_ID')
        self._client_secret = client_secret if client_secret is not None else os.getenv('TOSS_CLIENT_SECRET')
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(10),
                                    follow_redirects=False, trust_env=False)
        self._clock, self._now, self._sleep = clock, now, sleep
        self._jitter = jitter if jitter is not None else lambda: random.uniform(0, .25)
        self._token = None
        self._expires = 0
        self._next_request = 0
        self._lock = threading.RLock()

    def close(self):
        self._token = None
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _send(self, method, path, **kwargs):
        # Internal allowlist is deliberately not a generic brokerage client.
        if (method, path) != ('POST', '/oauth2/token') and not (method == 'GET' and path in READ_PATHS):
            raise ValueError('Endpoint is not allowed')
        for attempt in range(3):
            delay = self._next_request - self._clock()
            if delay > 10:
                raise MarketError(ErrorCode.RATE_LIMITED)
            if delay > 0:
                self._sleep(delay)
            # Accounts require 1/s; other paths retain the conservative 2/s ceiling.
            self._next_request = self._clock() + (1.0 if path == '/api/v1/accounts' else .5)
            response = None
            try:
                response = self._client.request(method, BASE_URL + path, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                pass
            if response is not None:
                retry_after = response.headers.get('Retry-After', '')
                if response.status_code == 429 and re.fullmatch(r'\d+(?:\.\d+)?', retry_after):
                    self._next_request = max(self._next_request, self._clock() + float(retry_after))
                limit = response.headers.get('X-RateLimit-Limit', '')
                if limit.isdigit() and int(limit) > 0:
                    self._next_request = max(self._next_request, self._clock() + 1 / int(limit))
                reset = response.headers.get('X-RateLimit-Reset', '')
                if response.headers.get('X-RateLimit-Remaining') == '0' and re.fullmatch(r'\d+(?:\.\d+)?', reset):
                    self._next_request = max(self._next_request, self._clock() + float(reset))
            if response is not None and response.status_code not in TRANSIENT:
                return response
            code = ErrorCode.RATE_LIMITED if response is not None and response.status_code == 429 else ErrorCode.UNAVAILABLE
            if attempt == 2:
                raise MarketError(code) from None
            delay = 1 * 2 ** attempt + self._jitter()
            if response is not None:
                for header in ('Retry-After', 'X-RateLimit-Reset'):
                    raw = response.headers.get(header, '')
                    if re.fullmatch(r'\d+(?:\.\d+)?', raw):
                        requested = float(raw)
                        self._next_request = max(self._next_request, self._clock() + requested)
                        if requested > 10:
                            # Do not violate long server cooldowns by retrying early.
                            raise MarketError(code) from None
                        delay = max(delay, requested)
            self._sleep(delay)
        raise AssertionError('Unreachable retry state')

    @staticmethod
    def _json(response):
        try:
            value = json.loads(response.content)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise MarketError(ErrorCode.INVALID_RESPONSE) from None

    @wire_errors
    def _authenticate(self):
        if self._token and self._clock() < self._expires:
            return
        self._token = None
        if not self._client_id or not self._client_secret:
            raise MarketError(ErrorCode.AUTHENTICATION)
        response = self._send('POST', '/oauth2/token', data={
            'grant_type': 'client_credentials', 'client_id': self._client_id,
            'client_secret': self._client_secret})
        if response.status_code != 200:
            raise MarketError(ErrorCode.AUTHENTICATION)
        data = self._json(response)
        token, seconds = data['access_token'], data['expires_in']
        if (not isinstance(token, str) or not token or len(token) > 16384
                or not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token)
                or data['token_type'] != 'Bearer' or type(seconds) is not int or seconds <= 0):
            raise ValueError()
        self._token = token
        self._expires = self._clock() + seconds - min(30, seconds / 10)

    def _get(self, path, params, account=None):
        if path not in READ_PATHS:
            raise ValueError('Endpoint is not allowed')
        if path == '/api/v1/holdings' and (type(account) is not int or account <= 0):
            raise ValueError('Invalid account reference')
        with self._lock:
            self._authenticate()
            for auth_attempt in range(2):
                headers = {'Authorization': 'Bearer ' + self._token}
                if account is not None:
                    headers['X-Tossinvest-Account'] = str(account)
                response = self._send('GET', path, params=params, headers=headers)
                if response.status_code == 401:
                    self._token = None
                    if auth_attempt == 0:
                        self._authenticate()
                        continue
                    raise MarketError(ErrorCode.AUTHENTICATION)
                if response.status_code != 200:
                    code = {403: ErrorCode.AUTHENTICATION, 404: ErrorCode.NOT_FOUND}.get(
                        response.status_code, ErrorCode.UNAVAILABLE)
                    raise MarketError(code)
                data = self._json(response)
                if 'result' not in data:
                    raise MarketError(ErrorCode.INVALID_RESPONSE)
                return data['result']
        raise AssertionError('Unreachable authentication state')
