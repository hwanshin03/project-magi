"""Small presentation dictionary; models retain stable English field identifiers."""
from .models import ErrorCode

LABELS = {
    'en': {'price': 'Current Price', 'absolute_change': 'Daily Change', 'volume': 'Volume',
           'is_stale': 'Stale', 'fetched_at': 'Fetched at'},
    'ko': {'price': '현재가', 'absolute_change': '일간 등락', 'volume': '거래량',
           'is_stale': '오래된 데이터', 'fetched_at': '조회 시각'},
}
ERRORS = {
    ErrorCode.UNAVAILABLE: 'Market data is temporarily unavailable.',
    ErrorCode.RATE_LIMITED: 'Market data is rate limited. Try again later.',
    ErrorCode.AUTHENTICATION: 'Market data authentication failed. Check credentials and allowed IP settings.',
    ErrorCode.INVALID_RESPONSE: 'Market data provider returned an invalid response.',
    ErrorCode.UNSUPPORTED: 'This market data request is not supported by the provider.',
    ErrorCode.NOT_FOUND: 'Requested market data could not be found.',
}
