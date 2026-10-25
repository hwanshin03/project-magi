"""Virtual-only, immutable accounting values. No provider or owner imports."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import Enum
from uuid import UUID
import re

CENT = Decimal('0.01')
ZERO = Decimal('0.00')
MAX_MONEY = Decimal('1000000000000')
def arithmetic():
    """Fresh fixed arithmetic context; never inherit mutable caller settings."""
    return localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN))


class ErrorCode(str, Enum):
    INVALID_INPUT = 'INVALID_INPUT'
    UNSAFE_STORAGE = 'UNSAFE_STORAGE'
    STORAGE_UNAVAILABLE = 'STORAGE_UNAVAILABLE'
    INVALID_LEDGER = 'INVALID_LEDGER'
    UNKNOWN_PORTFOLIO = 'UNKNOWN_PORTFOLIO'
    UNKNOWN_INSTRUMENT = 'UNKNOWN_INSTRUMENT'
    IDENTITY_CONFLICT = 'IDENTITY_CONFLICT'
    CONFIRMATION_REQUIRED = 'CONFIRMATION_REQUIRED'
    ALREADY_FUNDED = 'ALREADY_FUNDED'
    NOT_FUNDED = 'NOT_FUNDED'
    INSUFFICIENT_CASH = 'INSUFFICIENT_CASH'
    INSUFFICIENT_SHARES = 'INSUFFICIENT_SHARES'
    IDEMPOTENCY_CONFLICT = 'IDEMPOTENCY_CONFLICT'
    DUPLICATE_FILL = 'DUPLICATE_FILL'
    CLOCK_REGRESSION = 'CLOCK_REGRESSION'
    LIMIT_EXCEEDED = 'LIMIT_EXCEEDED'


class VirtualError(ValueError):
    """Only a fixed code may cross the accounting boundary."""
    def __init__(self, code):
        self.code = ErrorCode(code)
        super().__init__(self.code.value)


def identifier(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise VirtualError(ErrorCode.INVALID_INPUT) from None
    return value


def usd(value):
    if value != 'USD' or type(value) is not str:
        raise VirtualError(ErrorCode.INVALID_INPUT)
    return value


def number(value, *, maximum=MAX_MONEY, positive=False):
    if (type(value) is not Decimal or not value.is_finite()
            or value < 0 or (positive and value == 0) or value > maximum
            or len(value.as_tuple().digits) > 30 or abs(value.as_tuple().exponent) > 8):
        raise VirtualError(ErrorCode.INVALID_INPUT)
    return value


def money(value, *, positive=False):
    number(value, positive=positive)
    with arithmetic():
        rounded = value.quantize(CENT)
    if positive and rounded == 0:
        raise VirtualError(ErrorCode.INVALID_INPUT)
    return rounded if rounded else ZERO


def instant(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise VirtualError(ErrorCode.INVALID_INPUT)
    return value.astimezone(timezone.utc)


class Side(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'


class EventKind(str, Enum):
    FUNDING = 'FUNDING'
    BUY = 'BUY'
    SELL = 'SELL'


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    symbol: str
    market: str = 'US'
    currency: str = 'USD'

    def __post_init__(self):
        identifier(self.instrument_id)
        usd(self.currency)
        if (self.market != 'US' or type(self.symbol) is not str
                or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,15}', self.symbol)):
            raise VirtualError(ErrorCode.INVALID_INPUT)


@dataclass(frozen=True)
class SyntheticFill:
    """Test/internal input ONLY. It is not proof of authorization or market provenance."""
    fill_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    provenance_id: str
    fees: Decimal = ZERO
    currency: str = 'USD'

    def __post_init__(self):
        for value in (self.fill_id, self.instrument_id, self.provenance_id):
            identifier(value)
        usd(self.currency)
        if type(self.side) is not Side:
            raise VirtualError(ErrorCode.INVALID_INPUT)
        number(self.quantity, maximum=Decimal('1000000'), positive=True)
        if self.quantity != self.quantity.to_integral_value():
            raise VirtualError(ErrorCode.INVALID_INPUT)
        number(self.price, maximum=Decimal('1000000'), positive=True)
        object.__setattr__(self, 'fees', money(self.fees))
        with arithmetic():
            if (self.quantity * self.price).quantize(CENT) == 0:
                raise VirtualError(ErrorCode.INVALID_INPUT)


@dataclass(frozen=True)
class Event:
    sequence: int
    event_id: str
    portfolio_id: str
    kind: EventKind
    currency: str
    amount: Decimal
    fill: SyntheticFill | None
    effective_at: datetime
    recorded_at: datetime
    idempotency_key: str


@dataclass(frozen=True)
class Position:
    instrument_id: str
    quantity: Decimal = ZERO
    book_cost: Decimal = ZERO
    realized_pnl: Decimal = ZERO

    @property
    def average_cost(self):
        with arithmetic():
            return self.book_cost / self.quantity if self.quantity else ZERO


@dataclass(frozen=True)
class PortfolioState:
    portfolio_id: str
    currency: str = 'USD'
    cash: Decimal = ZERO
    funding: Decimal = ZERO
    positions: tuple[Position, ...] = ()
    event_count: int = 0

    @property
    def realized_pnl(self):
        with arithmetic():
            return sum((p.realized_pnl for p in self.positions), ZERO)
