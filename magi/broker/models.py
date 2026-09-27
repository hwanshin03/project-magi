"""Immutable broker snapshots. Never stored as portfolio transactions."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Generic, Optional, Tuple, TypeVar


class BrokerErrorCode(str, Enum):
    UNAVAILABLE = 'UNAVAILABLE'
    AUTHENTICATION = 'AUTHENTICATION'
    RATE_LIMITED = 'RATE_LIMITED'
    INVALID_RESPONSE = 'INVALID_RESPONSE'
    UNSUPPORTED = 'UNSUPPORTED'
    NOT_FOUND = 'NOT_FOUND'
    AMBIGUOUS = 'AMBIGUOUS'


T = TypeVar('T')


@dataclass(frozen=True)
class BrokerResult(Generic[T]):
    data: Optional[T] = None
    error: Optional[BrokerErrorCode] = None


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str = field(repr=False)
    provider: str
    fetched_at: datetime
    account_name: Optional[str] = None
    account_type: Optional[str] = None
    base_currency: Optional[str] = None
    is_stale: bool = False


@dataclass(frozen=True)
class AccountList:
    accounts: Tuple[BrokerAccount, ...]
    fetched_at: datetime
    is_stale: bool = False


@dataclass(frozen=True)
class Holding:
    symbol: str
    market: str
    currency: str
    quantity: Decimal
    provider: str
    account_id: str = field(repr=False)
    fetched_at: datetime
    asset_name: Optional[str] = None
    available_quantity: Optional[Decimal] = None
    average_cost: Optional[Decimal] = None
    current_price: Optional[Decimal] = None
    book_cost: Optional[Decimal] = None
    market_value: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    unrealized_return: Optional[Decimal] = None
    is_stale: bool = False


@dataclass(frozen=True)
class CurrencySummary:
    currency: str
    total_book_cost: Optional[Decimal] = None
    total_market_value: Optional[Decimal] = None
    total_unrealized_pnl: Optional[Decimal] = None


@dataclass(frozen=True)
class HoldingsSnapshot:
    holdings: Tuple[Holding, ...]
    totals: Tuple[CurrencySummary, ...]
    provider: str
    account_id: str = field(repr=False)
    fetched_at: datetime
    is_stale: bool = False


@dataclass(frozen=True)
class CashBalance:
    currency: str
    provider: str
    account_id: str = field(repr=False)
    fetched_at: datetime
    cash_balance: Optional[Decimal] = None
    available_cash: Optional[Decimal] = None
    buying_power: Optional[Decimal] = None
    is_stale: bool = False


@dataclass(frozen=True)
class CashSnapshot:
    balances: Tuple[CashBalance, ...]
    fetched_at: datetime
    is_stale: bool = False


@dataclass(frozen=True)
class AccountSummary:
    totals: Tuple[CurrencySummary, ...]
    asset_count: int
    provider: str
    account_id: str = field(repr=False)
    fetched_at: datetime
    is_stale: bool = False
