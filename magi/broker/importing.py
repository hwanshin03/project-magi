"""Explicit, single-position initialization from validated normalized snapshots.

No network operations. Preview never writes; confirmation writes one local opening
balance, never a broker order or historical BUY. Reconciliation remains read-only.
"""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from magi.accounts import PortfolioAccountIdentity
from magi.portfolio import PortfolioError, decimal_value, symbol_value
from magi.storage import check_sensitive, timestamp
from magi.market.models import utcnow
from .models import BrokerAccount, BrokerResult, HoldingsSnapshot, Holding
from .reconciliation import ReconciliationEngine, ReconciliationStatus


class ImportRejected(PortfolioError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


class BrokerPositionImporter:
    def __init__(self, portfolio=None, *, now=None):
        # None means no local database exists; preview only. Writes need a Portfolio.
        self.portfolio = portfolio
        self.now = now or utcnow

    def _fresh_time(self, value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'Snapshot time is invalid.')
        age = (self.now() - value).total_seconds()
        if not 0 <= age <= 60:
            raise ImportRejected('UNAVAILABLE', 'A snapshot from the last 60 seconds is required.')

    def preview(self, result, account, symbol, *, market=None, currency=None):
        symbol = symbol_value(symbol)
        if not isinstance(account, PortfolioAccountIdentity) or account.provider == 'MANUAL':
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'An explicit broker account is required.')
        if not isinstance(result, BrokerResult) or result.error or not isinstance(result.data, HoldingsSnapshot):
            raise ImportRejected('UNAVAILABLE', 'A successful broker holdings snapshot is required.')
        snapshot = result.data
        if snapshot.is_stale:
            raise ImportRejected('UNAVAILABLE', 'Stale broker holdings cannot be imported.')
        self._fresh_time(snapshot.fetched_at)
        actual = PortfolioAccountIdentity.from_broker(BrokerAccount(snapshot.account_id, snapshot.provider, snapshot.fetched_at))
        if (actual.provider, actual.account_ref) != (account.provider, account.account_ref):
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'Snapshot does not match the selected provider/account.')
        for holding in snapshot.holdings:
            if (not isinstance(holding, Holding) or holding.provider != snapshot.provider
                    or holding.account_id != snapshot.account_id):
                raise ImportRejected('IMPORT_NOT_ALLOWED', 'Holding identity does not match the selected account.')
            if holding.is_stale:
                raise ImportRejected('UNAVAILABLE', 'Stale holdings cannot be imported.')
            self._fresh_time(holding.fetched_at)
        matches = [h for h in snapshot.holdings if h.symbol == symbol
                   and (market is None or h.market == market.upper())
                   and (currency is None or h.currency == currency.upper())]
        if len(matches) != 1:
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'Select exactly one broker instrument using symbol, market and currency.')
        holding = matches[0]
        if holding.average_cost is None:
            raise ImportRejected('IMPORT_NOT_ALLOWED', f'Cannot import {symbol} because broker average cost is unavailable.')
        if (not isinstance(holding.market, str) or not holding.market or not isinstance(holding.currency, str) or len(holding.currency) != 3
                or not holding.currency.isascii() or not holding.currency.isalpha() or holding.currency != holding.currency.upper()
                or holding.market != holding.market.strip().upper()):
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'An explicit normalized market and currency are required.')
        if holding.asset_name is not None and not isinstance(holding.asset_name, str):
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'Asset name must be normalized text.')
        for value in (holding.quantity, holding.average_cost):
            if not isinstance(value, Decimal):
                raise ImportRejected('IMPORT_NOT_ALLOWED', 'Normalized Decimal quantities and costs are required.')
            decimal_value(value)
        if holding.quantity <= 0:
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'Broker quantity must be greater than zero.')
        scope = dict(broker_provider=account.provider, broker_account_ref=account.account_ref)
        local = self.portfolio.get_position(symbol, market=holding.market, currency=holding.currency, **scope) if self.portfolio else None
        if local is not None:
            if local.shares_held != holding.quantity:
                raise ImportRejected('QUANTITY_MISMATCH', 'Instrument already has tracked history; initialization is not allowed.')
            if local.average_book_cost != holding.average_cost:
                raise ImportRejected('COST_MISMATCH', 'Instrument already has tracked history; initialization is not allowed.')
            raise ImportRejected('ALREADY_TRACKED', 'Instrument already has tracked history; initialization is not allowed.')
        positions = self.portfolio.get_open_positions_by_account(account.provider, account.account_ref) if self.portfolio else ()
        report = ReconciliationEngine().compare(positions, result)
        key = (symbol, holding.market, holding.currency)
        row = next((r for r in report.rows if (r.symbol, r.market, r.currency) == key), None)
        if not report.available or row is None or row.status != ReconciliationStatus.BROKER_ONLY:
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'A unique BROKER_ONLY position is required.')
        preview = next(p for p in report.import_previews if (p.symbol, p.market, p.currency) == key)
        check_sensitive(asdict(preview))
        return preview

    def import_position(self, result, account, symbol, *, confirmed=False, market=None, currency=None, notes=''):
        if confirmed is not True:
            raise ImportRejected('CONFIRMATION_REQUIRED', 'Use explicit confirmation to create one opening balance.')
        if self.portfolio is None:
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'A writable local ledger is required.')
        preview = self.preview(result, account, symbol, market=market, currency=currency)
        # Recheck elapsed time immediately before writing. The ledger independently
        # serializes the no-history check and insert to prevent concurrent duplicates.
        self._fresh_time(result.data.fetched_at)
        return self.portfolio.record_opening_balance(
            preview.symbol, preview.observed_quantity, preview.observed_average_cost,
            as_of=timestamp(preview.as_of), currency=preview.currency, market=preview.market,
            broker_provider=account.provider, broker_account_ref=account.account_ref,
            confirmed=True, asset_name=preview.asset_name, notes=notes)
