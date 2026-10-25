"""Pure replay of the isolated virtual ledger; USD cents and whole-share fills."""
from dataclasses import replace
from .models import (CENT, MAX_MONEY, ZERO, ErrorCode, Event, EventKind,
                     PortfolioState, Position, SyntheticFill, VirtualError,
                     arithmetic, identifier, instant, money, usd)


def reconstruct(portfolio_id, events):
    identifier(portfolio_id)
    cash = funding = ZERO
    positions = {}
    last_time = None
    seen_events, seen_keys, seen_fills = set(), set(), set()
    count = 0
    with arithmetic():
        for count, event in enumerate(events, 1):
            if (type(event) is not Event or event.portfolio_id != portfolio_id
                    or type(event.sequence) is not int or event.sequence != count
                    or type(event.kind) is not EventKind):
                raise VirtualError(ErrorCode.INVALID_LEDGER)
            identifier(event.event_id); identifier(event.idempotency_key); usd(event.currency)
            effective, recorded = instant(event.effective_at), instant(event.recorded_at)
            if (effective != recorded or (last_time is not None and recorded < last_time)
                    or event.event_id in seen_events or event.idempotency_key in seen_keys):
                raise VirtualError(ErrorCode.INVALID_LEDGER)
            seen_events.add(event.event_id); seen_keys.add(event.idempotency_key)
            last_time = recorded
            if event.kind is EventKind.FUNDING:
                if event.fill is not None or funding or count != 1 or money(event.amount, positive=True) != event.amount:
                    raise VirtualError(ErrorCode.INVALID_LEDGER)
                cash = funding = event.amount
            else:
                fill = event.fill
                if (type(fill) is not SyntheticFill or replace(fill) != fill
                        or event.amount != ZERO or event.kind.value != fill.side.value
                        or fill.fill_id in seen_fills):
                    raise VirtualError(ErrorCode.INVALID_LEDGER)
                if not funding:
                    raise VirtualError(ErrorCode.NOT_FUNDED)
                seen_fills.add(fill.fill_id)
                old = positions.get(fill.instrument_id, Position(fill.instrument_id))
                gross = (fill.quantity * fill.price).quantize(CENT)
                if event.kind is EventKind.BUY:
                    debit = gross + fill.fees
                    if debit > cash:
                        raise VirtualError(ErrorCode.INSUFFICIENT_CASH)
                    cash -= debit
                    new = replace(old, quantity=old.quantity + fill.quantity,
                                  book_cost=old.book_cost + debit)
                else:
                    if fill.quantity > old.quantity:
                        raise VirtualError(ErrorCode.INSUFFICIENT_SHARES)
                    released = (old.book_cost if fill.quantity == old.quantity else
                                (old.book_cost * fill.quantity / old.quantity).quantize(CENT))
                    net = gross - fill.fees
                    if cash + net < 0:
                        raise VirtualError(ErrorCode.INSUFFICIENT_CASH)
                    cash += net
                    new = replace(old, quantity=old.quantity - fill.quantity,
                                  book_cost=old.book_cost - released,
                                  realized_pnl=old.realized_pnl + net - released)
                positions[fill.instrument_id] = new
            if (cash > MAX_MONEY or sum((p.book_cost for p in positions.values()), ZERO) > MAX_MONEY
                    or abs(sum((p.realized_pnl for p in positions.values()), ZERO)) > MAX_MONEY):
                raise VirtualError(ErrorCode.LIMIT_EXCEEDED)
            if cash + sum((p.book_cost for p in positions.values()), ZERO) != funding + sum(
                    (p.realized_pnl for p in positions.values()), ZERO):
                raise VirtualError(ErrorCode.INVALID_LEDGER)
    return PortfolioState(portfolio_id, cash=cash, funding=funding,
                          positions=tuple(positions[k] for k in sorted(positions)), event_count=count)
