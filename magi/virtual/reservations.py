"""Immutable synthetic reservation claims and strict codecs; no admission authority."""
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from .models import ErrorCode, VirtualError, identifier, money, number, usd
from .order_codec import _dump, _fields, _load, _text
from .storage import _decimal_text


class Resource(str, Enum):
    CASH = 'CASH'
    SHARES = 'SHARES'


class ReservationStatus(str, Enum):
    ACTIVE = 'ACTIVE'


class ReservationCode(str, Enum):
    RESERVED = 'RESERVED'
    INSUFFICIENT_CASH = 'INSUFFICIENT_CASH'
    INSUFFICIENT_SHARES = 'INSUFFICIENT_SHARES'


@dataclass(frozen=True)
class Reservation:
    """Proposed CREATE until a RESERVED outcome commits. UUIDs confer no authority.

    command_id/creation_event_id refer to the persisted initial order history.
    operation_id is this reservation operation, not a submission or settlement ID.
    The actor/key must match the initial submission; no independent release key.
    """
    reservation_id: str
    operation_id: str
    portfolio_id: str
    order_id: str
    instrument_id: str
    command_id: str
    creation_event_id: str
    actor_id: str
    idempotency_key: str
    resource: Resource
    amount: Decimal
    currency: str | None
    status: ReservationStatus = ReservationStatus.ACTIVE

    def __post_init__(self):
        for name in _IDENTITIES:
            identifier(getattr(self, name))
        if type(self.resource) is not Resource or type(self.status) is not ReservationStatus:
            raise VirtualError(ErrorCode.INVALID_INPUT)
        if self.resource is Resource.CASH:
            usd(self.currency)
            if money(self.amount, positive=True) != self.amount:
                raise VirtualError(ErrorCode.INVALID_INPUT)  # Never round a cap silently.
        else:
            number(self.amount, maximum=Decimal('1000000'), positive=True)
            if self.currency is not None or self.amount != self.amount.to_integral_value():
                raise VirtualError(ErrorCode.INVALID_INPUT)


_IDENTITIES = ('reservation_id', 'operation_id', 'portfolio_id', 'order_id', 'instrument_id',
               'command_id', 'creation_event_id', 'actor_id', 'idempotency_key')


def encode_reservation(value):
    try:
        if type(value) is not Reservation or replace(value) != value:
            raise ValueError()
        return _dump(dict(version=1, **{name: getattr(value, name) for name in _IDENTITIES},
                          resource=value.resource.value, amount=_decimal_text(value.amount),
                          currency=value.currency, status=value.status.value))
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        raise VirtualError(ErrorCode.INVALID_INPUT) from None


def decode_reservation(text):
    try:
        data = _fields(_load(text), 'version ' + ' '.join(_IDENTITIES) + ' resource amount currency status')
        version = data.pop('version')
        if type(version) is not int or version != 1:
            raise ValueError()
        data['resource'] = Resource(data['resource'])
        data['status'] = ReservationStatus(data['status'])
        data['amount'] = Decimal(_text(data['amount']))
        result = Reservation(**data)
        if encode_reservation(result) != text:
            raise ValueError()
        return result
    except (ValueError, TypeError, ArithmeticError, KeyError, RecursionError):
        raise VirtualError(ErrorCode.INVALID_LEDGER) from None


@dataclass(frozen=True)
class ReservationOutcome:
    request: Reservation
    code: ReservationCode


@dataclass(frozen=True)
class AvailableResource:
    posted: Decimal
    reserved: Decimal
    available: Decimal


@dataclass(frozen=True)
class ReservationState:
    portfolio_id: str
    cash: AvailableResource
    shares: tuple[tuple[str, AvailableResource], ...]
    reservations: tuple[Reservation, ...]
