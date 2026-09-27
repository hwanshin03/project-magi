"""Provider-neutral short-lived snapshots, including explicit outage/stale state."""
from collections import OrderedDict
from dataclasses import replace
import time
from .base import BrokerError
from .models import AccountList, AccountSummary, BrokerResult, CashSnapshot, HoldingsSnapshot


def stale(snapshot):
    changes = {'is_stale': True}
    if isinstance(snapshot, AccountList):
        changes['accounts'] = tuple(replace(a, is_stale=True) for a in snapshot.accounts)
    elif isinstance(snapshot, HoldingsSnapshot):
        changes['holdings'] = tuple(replace(h, is_stale=True) for h in snapshot.holdings)
    elif isinstance(snapshot, CashSnapshot):
        changes['balances'] = tuple(replace(b, is_stale=True) for b in snapshot.balances)
    return replace(snapshot, **changes)


class BrokerService:
    def __init__(self, provider, *, clock=time.monotonic, ttl=10, max_entries=32):
        if not 0 < ttl <= 60 or type(max_entries) is not int or max_entries <= 0:
            raise ValueError('Invalid cache configuration')
        self.provider, self.clock, self.ttl, self.max_entries = provider, clock, ttl, max_entries
        self._cache = OrderedDict()

    def _get(self, key, call):
        cached = self._cache.get(key)
        if cached and self.clock() - cached[0] < self.ttl:
            self._cache.move_to_end(key)
            return BrokerResult(cached[1])
        try:
            snapshot = call()
        except BrokerError as error:
            return BrokerResult(stale(cached[1]) if cached else None, error.code)
        self._cache[key] = (self.clock(), snapshot)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return BrokerResult(snapshot)

    def get_accounts(self):
        return self._get(('accounts',), self.provider.get_accounts)

    def get_holdings(self, account):
        return self._get(('holdings', account.provider, account.account_id),
                         lambda: self.provider.get_holdings(account))

    def get_cash_balances(self, account):
        return self._get(('cash', account.provider, account.account_id),
                         lambda: self.provider.get_cash_balances(account))

    def get_account_summary(self, account):
        # The service uses the provider's summary capability. Toss derives it from
        # the same holdings response; its wrapper below reuses our cached snapshot.
        if getattr(self.provider, 'summary_from_holdings', False):
            result = self.get_holdings(account)
            if result.data is None:
                return BrokerResult(error=result.error)
            data = result.data
            return BrokerResult(AccountSummary(data.totals, len(data.holdings), data.provider,
                                  data.account_id, data.fetched_at, data.is_stale), result.error)
        return self._get(('summary', account.provider, account.account_id),
                         lambda: self.provider.get_account_summary(account))
