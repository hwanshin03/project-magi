"""Provider-neutral read-only broker contract; no trading methods."""
from abc import ABC, abstractmethod
from .models import BrokerErrorCode


class BrokerError(Exception):
    def __init__(self, code=BrokerErrorCode.UNAVAILABLE):
        self.code = BrokerErrorCode(code)
        super().__init__(self.code.value)


class BrokerProvider(ABC):
    supports_cash_balances = False
    summary_from_holdings = False

    @abstractmethod
    def get_accounts(self):
        """Return AccountList, excluding full account numbers."""

    @abstractmethod
    def get_holdings(self, account):
        """Return HoldingsSnapshot for exactly one account."""

    def get_cash_balances(self, account):
        """Unsupported is distinct from zero cash."""
        raise BrokerError(BrokerErrorCode.UNSUPPORTED)

    @abstractmethod
    def get_account_summary(self, account):
        """Return AccountSummary with separate native-currency totals."""
