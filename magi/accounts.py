"""Local account identities. Broker references are opaque, never account numbers."""
from dataclasses import dataclass, field
import hashlib
import re
from typing import Optional
from magi.storage import check_sensitive


@dataclass(frozen=True)
class PortfolioAccountIdentity:
    provider: str = 'MANUAL'
    account_ref: str = field(default='DEFAULT', repr=False)
    display_label: Optional[str] = None
    account_type: Optional[str] = None
    base_currency: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.provider, str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,31}', self.provider):
            raise ValueError('Invalid portfolio account identity.')
        if not isinstance(self.account_ref, str):
            raise ValueError('Invalid portfolio account identity.')
        if self.provider == 'MANUAL':
            valid = re.fullmatch('[A-Z][A-Z_]{0,31}', self.account_ref)
        else:
            valid = re.fullmatch('ref_[a-f0-9]{64}', self.account_ref)
        if not valid:
            raise ValueError('Use a safe portfolio account reference; account numbers are not accepted.')
        check_sensitive([self.provider, self.account_ref])
        check_sensitive([self.display_label, self.account_type, self.base_currency])
        # Descriptive metadata is optional and never used as a matching key.
        for value in (self.display_label, self.account_type):
            if value is not None and (not isinstance(value, str) or len(value)>128 or any(ord(c)<32 for c in value)):
                raise ValueError('Invalid portfolio account metadata.')
        if self.base_currency is not None and (not isinstance(self.base_currency, str) or not re.fullmatch('[A-Z]{3}', self.base_currency)):
            raise ValueError('Invalid portfolio account currency.')

    @classmethod
    def from_broker(cls, account):
        from magi.broker.models import BrokerAccount
        if not isinstance(account, BrokerAccount) or not isinstance(account.account_id, str) or not account.account_id or not isinstance(account.provider, str):
            raise ValueError('Broker account could not be matched to a local MAGI account.')
        provider = account.provider.upper()
        if provider == 'MANUAL':
            raise ValueError('Broker account could not be matched to a local MAGI account.')
        digest = hashlib.sha256((provider + '\0' + account.account_id).encode()).hexdigest()
        return cls(provider, 'ref_' + digest, account_type=account.account_type, base_currency=account.base_currency)


def account_identity(provider=None, account_ref=None):
    if provider is None and account_ref is None:
        return PortfolioAccountIdentity()
    if provider is None or account_ref is None:
        raise ValueError('Specify both broker provider and safe account reference.')
    if not isinstance(provider, str):
        raise ValueError('Invalid portfolio account identity.')
    return PortfolioAccountIdentity(provider.strip().upper(), account_ref)


def account_label(provider, account_ref, language='en'):
    if provider == 'MANUAL':
        return ('수동 입력' if language=='ko' else 'Manual') + ' / ' + account_ref
    word = '계좌' if language=='ko' else 'Account'
    return f'{provider} / {word} …{account_ref[-8:]}'
