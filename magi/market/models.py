"""Immutable, provider-neutral market snapshots; never ledger records."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Generic, Optional, Tuple, TypeVar
import re


def utcnow():
    return datetime.now(timezone.utc)


def instrument(symbol, market):
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,31}', symbol.strip()):
        raise ValueError('Invalid symbol')
    if not isinstance(market, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,15}', market.strip()):
        raise ValueError('Explicit market is required')
    return symbol.strip().upper(), market.strip().upper()


def currency(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z]{3}', value):
        raise ValueError('Invalid currency')
    return value.upper()


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('Invalid decimal')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid decimal') from None
    if not result.is_finite() or result < 0 or len(result.as_tuple().digits) > 30 or abs(result.as_tuple().exponent) > 18:
        raise ValueError('Invalid decimal')
    return result


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware timestamp required')
    return value


class ErrorCode(str, Enum):
    UNAVAILABLE = 'UNAVAILABLE'
    RATE_LIMITED = 'RATE_LIMITED'
    AUTHENTICATION = 'AUTHENTICATION'
    INVALID_RESPONSE = 'INVALID_RESPONSE'
    UNSUPPORTED = 'UNSUPPORTED'
    NOT_FOUND = 'NOT_FOUND'


T = TypeVar('T')


@dataclass(frozen=True)
class MarketResult(Generic[T]):
    data: Optional[T] = None
    error: Optional[ErrorCode] = None


@dataclass(frozen=True)
class Quote:
    symbol: str
    market: str
    currency: str
    price: Decimal
    timestamp: Optional[datetime]
    provider: str
    fetched_at: datetime
    is_stale: bool = False
    asset_name: Optional[str] = None
    previous_close: Optional[Decimal] = None
    absolute_change: Optional[Decimal] = None
    percent_change: Optional[Decimal] = None
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    volume: Optional[Decimal] = None


@dataclass(frozen=True)
class Candle:
    symbol: str
    market: str
    currency: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    provider: str


@dataclass(frozen=True)
class CandleSeries:
    symbol: str
    market: str
    interval: str
    candles: Tuple[Candle, ...]
    provider: str
    fetched_at: datetime
    is_stale: bool = False


@dataclass(frozen=True)
class FXRate:
    base_currency: str
    quote_currency: str
    rate: Decimal
    timestamp: datetime
    provider: str
    fetched_at: datetime
    valid_until: Optional[datetime] = None
    is_stale: bool = False


def convert(value, native_currency, fx):
    """Explicit directional conversion; never mutate the native value."""
    if currency(native_currency) != fx.base_currency or fx.rate <= 0:
        raise ValueError('FX direction does not match native currency')
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError('Conversion requires a finite nonnegative Decimal')
    with localcontext() as context:
        context.prec = 80
        return value * fx.rate
