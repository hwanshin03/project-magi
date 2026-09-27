"""Account discovery and holdings only; no order-related API surface."""
from decimal import Decimal, InvalidOperation
from functools import wraps
import re

from magi.market.base import MarketError
from magi.market.models import amount, instrument
from magi.toss import TossSession
from .base import BrokerError, BrokerProvider
from .models import (AccountList, AccountSummary, BrokerAccount, BrokerErrorCode,
                     CurrencySummary, Holding, HoldingsSnapshot)

READ_PATHS = frozenset({'/api/v1/accounts', '/api/v1/holdings'})


def broker_errors(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except MarketError as error:
            raise BrokerError(BrokerErrorCode(error.code.value)) from None
        except (ValueError, KeyError, TypeError, OverflowError):
            raise BrokerError(BrokerErrorCode.INVALID_RESPONSE) from None
    return wrapped


def number(value, signed=False):
    if value is None:
        return None
    if not signed:
        return amount(value)
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('Invalid decimal')
    try:
        result = Decimal(value)
        amount(result.copy_abs())
        return result
    except InvalidOperation:
        raise ValueError('Invalid decimal') from None


def nested(row, key):
    value = row.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Invalid object')
    return value


class TossBrokerProvider(BrokerProvider):
    name = 'TOSS'
    summary_from_holdings = True

    def __init__(self, *, session=None, **options):
        if session is not None and options:
            raise ValueError('Pass a shared session or session options, not both')
        self._session = session if session is not None else TossSession(**options)
        self._owns_session = session is None

    def close(self):
        if self._owns_session:
            self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _get(self, path, account=None):
        if path not in READ_PATHS:
            raise ValueError('Endpoint is not allowed')
        return self._session._get(path, {}, account)

    @staticmethod
    def _account(account):
        if (not isinstance(account, BrokerAccount) or account.provider != 'TOSS'
                or not re.fullmatch(r'[1-9][0-9]{0,18}', account.account_id)):
            raise ValueError('Invalid account reference')
        return int(account.account_id)

    @broker_errors
    def get_accounts(self):
        rows = self._get('/api/v1/accounts')
        if not isinstance(rows, list):
            raise ValueError('Invalid accounts')
        now = self._session._now()
        accounts = []
        for row in rows:
            seq = row['accountSeq']
            kind = row.get('accountType')
            if type(seq) is not int or not 0 < seq < 10**19:
                raise ValueError('Invalid account reference')
            if kind is not None and (not isinstance(kind, str) or not re.fullmatch('[A-Z_]{1,64}', kind)):
                raise ValueError('Invalid account type')
            # accountNo is deliberately discarded, never normalized or logged.
            accounts.append(BrokerAccount(str(seq), self.name, now, account_type=kind))
        if len({a.account_id for a in accounts}) != len(accounts):
            raise ValueError('Duplicate account reference')
        return AccountList(tuple(sorted(accounts, key=lambda a: int(a.account_id))), now)

    @broker_errors
    def get_holdings(self, account):
        seq = self._account(account)
        data = self._get('/api/v1/holdings', seq)
        if not isinstance(data, dict) or not isinstance(data['items'], list):
            raise ValueError('Invalid holdings')
        now = self._session._now()
        holdings = []
        for row in data['items']:
            symbol, market = instrument(row['symbol'], row['marketCountry'])
            currency = row['currency']
            if market not in ('US', 'KR') or currency != {'US':'USD', 'KR':'KRW'}[market]:
                raise ValueError('Inconsistent instrument')
            name = row.get('name')
            if name is not None and (not isinstance(name, str) or len(name) > 256 or any(ord(c)<32 for c in name)):
                raise ValueError('Invalid asset name')
            market_value, pnl = nested(row, 'marketValue'), nested(row, 'profitLoss')
            holdings.append(Holding(symbol, market, currency, amount(row['quantity']), self.name,
                account.account_id, now, asset_name=name, average_cost=number(row.get('averagePurchasePrice')),
                current_price=number(row.get('lastPrice')), book_cost=number(market_value.get('purchaseAmount')),
                market_value=number(market_value.get('amount')), unrealized_pnl=number(pnl.get('amount'), True),
                unrealized_return=number(pnl.get('rate'), True)))
        purchase = nested(data, 'totalPurchaseAmount')
        values = nested(nested(data, 'marketValue'), 'amount')
        pnl = nested(nested(data, 'profitLoss'), 'amount')
        totals = tuple(CurrencySummary(code, number(purchase.get(code.lower())),
                         number(values.get(code.lower())), number(pnl.get(code.lower()), True))
                       for code in ('KRW', 'USD'))
        return HoldingsSnapshot(tuple(holdings), totals, self.name, account.account_id, now)

    def get_account_summary(self, account):
        snapshot = self.get_holdings(account)
        return AccountSummary(snapshot.totals, len(snapshot.holdings), self.name,
                              account.account_id, snapshot.fetched_at, snapshot.is_stale)
