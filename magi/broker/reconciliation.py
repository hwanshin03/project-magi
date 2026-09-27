"""Compare snapshots only. Never infer transactions or choose a source of truth."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import Optional, Tuple


class ReconciliationStatus(str, Enum):
    MATCH = 'MATCH'
    QUANTITY_MISMATCH = 'QUANTITY_MISMATCH'
    COST_MISMATCH = 'COST_MISMATCH'
    BROKER_ONLY = 'BROKER_ONLY'
    MAGI_ONLY = 'MAGI_ONLY'
    AMBIGUOUS = 'AMBIGUOUS'
    UNAVAILABLE = 'UNAVAILABLE'


@dataclass(frozen=True)
class ReconciliationRow:
    symbol: str
    market: str
    currency: str
    ledger_quantity: Optional[Decimal]
    broker_quantity: Optional[Decimal]
    quantity_difference: Optional[Decimal]
    ledger_average_cost: Optional[Decimal]
    broker_average_cost: Optional[Decimal]
    cost_difference: Optional[Decimal]
    status: ReconciliationStatus
    cost_comparable: bool = False


@dataclass(frozen=True)
class ReconciliationReport:
    rows: Tuple[ReconciliationRow, ...]
    available: bool
    is_stale: bool = False
    status: Optional[ReconciliationStatus] = None


class ReconciliationEngine:
    def compare(self, ledger_positions, broker_result):
        # None means unavailable; an empty tuple means a known empty ledger.
        snapshot = broker_result.data
        if ledger_positions is None or snapshot is None or broker_result.error or snapshot.is_stale:
            return ReconciliationReport((), False, bool(snapshot and snapshot.is_stale), ReconciliationStatus.UNAVAILABLE)
        if any(h.is_stale for h in snapshot.holdings):
            return ReconciliationReport((), False, True, ReconciliationStatus.UNAVAILABLE)
        ledger, broker = defaultdict(list), defaultdict(list)
        for position in ledger_positions:
            if position.shares_held != 0:
                ledger[(position.symbol.upper(), position.market.upper(), position.currency.upper())].append(position)
        for holding in snapshot.holdings:
            if holding.quantity != 0:
                broker[(holding.symbol.upper(), holding.market.upper(), holding.currency.upper())].append(holding)
        keys = sorted(set(ledger) | set(broker))
        rows = []
        with localcontext() as context:
            context.prec = 80
            for key in keys:
                left, right = ledger.get(key, []), broker.get(key, [])
                symbol, market, currency = key
                unknown_market = not market or any(
                    other[0] == symbol and other[2] == currency and not other[1] for other in keys)
                ambiguous = (unknown_market or len(left)>1 or len(right)>1 or
                             any(h.account_id != snapshot.account_id or h.provider != snapshot.provider for h in right))
                lq = left[0].shares_held if len(left)==1 else (Decimal(0) if not left else None)
                rq = right[0].quantity if len(right)==1 else (Decimal(0) if not right else None)
                lc = left[0].average_book_cost if len(left)==1 else None
                rc = right[0].average_cost if len(right)==1 else None
                comparable = bool(left and right and lc is not None and rc is not None and not ambiguous)
                difference = rq-lq if lq is not None and rq is not None and not ambiguous else None
                cost_difference = rc-lc if comparable else None
                if ambiguous:
                    status = ReconciliationStatus.AMBIGUOUS
                elif not left:
                    status = ReconciliationStatus.BROKER_ONLY
                elif not right:
                    status = ReconciliationStatus.MAGI_ONLY
                elif lq != rq:
                    status = ReconciliationStatus.QUANTITY_MISMATCH
                elif comparable and lc != rc:
                    status = ReconciliationStatus.COST_MISMATCH
                else:
                    status = ReconciliationStatus.MATCH
                rows.append(ReconciliationRow(*key, lq, rq, difference, lc, rc, cost_difference, status, comparable))
        return ReconciliationReport(tuple(rows), True)
