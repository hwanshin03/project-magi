"""Localized display only. Rounding and dust classification never affect accounting."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import Enum
from magi.accounts import account_label


class GainState(str, Enum):
    POSITIVE = 'POSITIVE'
    NEGATIVE = 'NEGATIVE'
    NEUTRAL = 'NEUTRAL'
    UNKNOWN = 'UNKNOWN'


class PositionClass(str, Enum):
    NORMAL = 'NORMAL'
    DUST = 'DUST'


LABELS = {
 'en': {
  'title':'MAGI PORTFOLIO', 'quantity':'Quantity', 'average':'Average Cost', 'price':'Current Price',
  'book':'Remaining Book Cost', 'value':'Market Value', 'pnl':'Unrealized P/L', 'return':'Return',
  'tracking':'Tracking Start', 'purchase':'Original Purchase Date', 'history':'History Status',
  'realized':'Realized P/L since tracking', 'realized_return':'Realized return since tracking',
  'before':'Historical realized P/L before tracking', 'unknown':'UNKNOWN', 'unavailable':'UNAVAILABLE',
  'OPENING_BALANCE_HISTORY':'Opening balance history', 'COMPLETE_HISTORY':'Complete recorded history',
  'stale':'STALE MARKET DATA', 'stale_fx':'STALE FX DATA', 'stale_total':'STALE QUOTE OR FX IN TOTAL', 'approx':'Approximately',
  'unverified':'Broker reconciliation could not be verified', 'mismatch':'Portfolio reconciliation mismatch',
  'native':'Native currency totals', 'converted':'FX-converted display totals', 'incomplete':'INCOMPLETE',
  'dust':'Potential dust position', 'broker':'Broker', 'TOSS':'Toss Securities',
  'sources':'Quantity and basis: MAGI ledger; quotes: labeled by source and role; holdings: reconciliation only',
  'price_source':'Price source', 'price_role':'Price role',
  'BROKER_PRICE':'Broker price', 'REFERENCE_MARKET_PRICE':'Reference market price', 'UNSPECIFIED':'Unspecified',
  'fx_note':'Display conversion estimate; not an execution or actual brokerage exchange rate.',
  'fx_freshness':'FX freshness', 'fresh':'Fresh', 'neutral':'Neutral',
  'quote_time':'Quote timestamp', 'fx':'FX rate / source / timestamp', 'empty':'No open positions',
 },
 'ko': {
  'title':'MAGI 포트폴리오', 'quantity':'보유수량', 'average':'평균단가', 'price':'현재가',
  'book':'잔여 장부금액', 'value':'평가금액', 'pnl':'평가손익', 'return':'수익률',
  'tracking':'MAGI 추적 시작', 'purchase':'최초 실제 매수일', 'history':'기록 상태',
  'realized':'MAGI 추적 이후 실현손익', 'realized_return':'MAGI 추적 이후 실현수익률',
  'before':'추적 이전 실현손익', 'unknown':'알 수 없음', 'unavailable':'사용 불가',
  'OPENING_BALANCE_HISTORY':'초기 보유 잔고 기준', 'COMPLETE_HISTORY':'기록된 전체 거래 내역',
  'stale':'오래된 시세 데이터', 'stale_fx':'오래된 환율 데이터', 'stale_total':'합계에 오래된 시세 또는 환율 포함', 'approx':'약',
  'unverified':'증권사 잔고 대조를 확인할 수 없습니다', 'mismatch':'포트폴리오 잔고 대조 불일치',
  'native':'통화별 합계', 'converted':'환율 환산 표시 합계', 'incomplete':'불완전',
  'dust':'소액 잔고 후보', 'broker':'증권사', 'TOSS':'토스증권',
  'sources':'수량·장부금액: MAGI 원장; 시세: 출처·역할 표시; 증권사 잔고: 대조 전용',
  'price_source':'가격 출처', 'price_role':'가격 역할',
  'BROKER_PRICE':'증권사 가격', 'REFERENCE_MARKET_PRICE':'참고 시장 가격', 'UNSPECIFIED':'미지정',
  'fx_note':'표시용 환산 추정치이며 실제 체결·증권사 환전 적용 환율을 의미하지 않습니다.',
  'fx_freshness':'환율 최신성', 'fresh':'최신', 'neutral':'중립',
  'quote_time':'시세 기준 시각', 'fx':'적용 환율 / 출처 / 기준 시각', 'empty':'보유 포지션 없음',
 }
}


def gain_state(value):
    if value is None:
        return GainState.UNKNOWN
    return GainState.POSITIVE if value > 0 else GainState.NEGATIVE if value < 0 else GainState.NEUTRAL


def money(value, currency, *, signed=False, language='en'):
    if value is None:
        return LABELS[language]['unavailable']
    with localcontext() as context:
        context.prec = max(80, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + 5)
        rounded = abs(value).quantize(Decimal('1') if currency == 'KRW' else Decimal('.01'), rounding=ROUND_HALF_UP)
        sign = '-' if value < 0 else '+' if signed and value > 0 else ''
        prefix = {'USD':'$', 'KRW':'₩'}.get(currency, currency+' ')
        text = f'{sign}{prefix}{rounded:,.0f}' if currency == 'KRW' else f'{sign}{prefix}{rounded:,.2f}'
        return text + f" ({LABELS[language]['neutral']})" if signed and value == 0 else text


def percentage(value, language='en'):
    if value is None:
        return LABELS[language]['unknown']
    with localcontext() as context:
        context.prec = 80
        return f'{value*100:+.2f}%'


def classify(position, threshold=None):
    """Threshold is in the position's native currency; disabled unless supplied."""
    if threshold is not None and (not isinstance(threshold, Decimal) or not threshold.is_finite() or threshold < 0):
        raise ValueError('Dust threshold must be a nonnegative Decimal')
    return (PositionClass.DUST if threshold is not None and position.market_value is not None
            and position.market_value < threshold else PositionClass.NORMAL)


def render(view, *, language='en', dust_threshold=None, hide_dust=False):
    labels = LABELS[language]
    lines = [f"=== {labels['title']} ===", labels['sources']]
    if not view.positions:
        lines.append(labels['empty'])
    for warning in view.reconciliation_warnings:
        lines.append(labels['mismatch' if warning == 'MISMATCH' else 'unverified'])
    for p in view.positions:
        dust = classify(p,dust_threshold) == PositionClass.DUST
        if hide_dust and dust:
            continue
        lines += ['', f"{labels['broker']}: {labels.get(p.broker_provider,p.broker_provider)} | " +
                  account_label(p.broker_provider,p.broker_account_ref,language),
                  f'{p.asset_name or p.symbol} ({p.symbol}) | {p.market} / {p.native_currency}']
        if dust:
            lines.append(labels['dust'])
        if p.reconciliation == 'UNVERIFIED':
            lines.append(labels['unverified'])
        elif p.reconciliation not in ('MATCH','NOT_APPLICABLE'):
            lines.append(labels['mismatch'] + ': ' + p.reconciliation)
        if p.is_stale:
            lines.append(labels['stale'])
        if p.fx_is_stale:
            lines.append(labels['stale_fx'])
        lines.append(f"{labels['quantity']}: {p.quantity}")
        for label,native,converted in [('average',p.average_cost,None),('price',p.current_price,p.converted_price),
                ('book',p.remaining_book_cost,p.converted_book_cost),('value',p.market_value,p.converted_market_value),
                ('pnl',p.unrealized_pnl,p.converted_unrealized_pnl)]:
            lines.append(f"{labels[label]}: {money(native,p.native_currency,signed=label=='pnl',language=language)}")
            if p.display_currency and p.display_currency != p.native_currency and label != 'average':
                lines.append(f"{labels['approx']} {money(converted,p.display_currency,signed=label=='pnl',language=language)}")
        lines += [f"{labels['return']}: {percentage(p.unrealized_return,language)}",
                  f"{labels['realized']}: {money(p.realized_pnl,p.native_currency,signed=True,language=language)}",
                  f"{labels['realized_return']}: {percentage(p.realized_return,language)}",
                  f"{labels['before']}: {labels['unknown'] if p.pre_tracking_realized_pnl is None else money(p.pre_tracking_realized_pnl,p.native_currency,language=language)}",
                  f"{labels['tracking']}: {p.tracking_start or labels['unknown']}",
                  f"{labels['purchase']}: {p.original_purchase_date or labels['unknown']}",
                  f"{labels['history']}: {labels[p.history_completeness.value]}",
                  f"{labels['quote_time']}: {p.quote_timestamp or labels['unavailable']}",
                  f"{labels['price_source']}: {labels.get(p.market_data_provider,p.market_data_provider) or labels['unavailable']}",
                  f"{labels['price_role']}: {labels[p.price_role.value]} ({p.price_role.value})"]
        if p.display_currency and p.display_currency != p.native_currency:
            rate = f'1 {p.native_currency} = {p.fx_rate} {p.display_currency}' if p.fx_rate is not None else labels['unavailable']
            lines.append(f"{labels['fx']}: {rate} / {p.fx_source or labels['unavailable']} / {p.fx_timestamp or labels['unavailable']}")
            freshness = labels['unavailable'] if p.fx_rate is None else labels['stale_fx'] if p.fx_is_stale else labels['fresh']
            lines += [f"{labels['fx_freshness']}: {freshness}", labels['fx_note']]
    for total in (*view.native_totals, *((view.converted_total,) if view.converted_total else ())):
        lines += ['', f"{labels['converted' if total.converted else 'native']}: {total.currency}"]
        if not total.complete:
            lines.append(labels['incomplete'])
        if total.is_stale:
            lines.append(labels['stale_total'] if total.converted else labels['stale'])
        for label,value in [('book',total.book_cost),('value',total.market_value),('pnl',total.unrealized_pnl)]:
            lines.append(f"{labels[label]}: {money(value,total.currency,signed=label=='pnl',language=language)}")
    return '\n'.join(lines)
