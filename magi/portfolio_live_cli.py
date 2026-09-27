"""Explicit live view; shared read-only provider session and query-only ledger."""
import argparse
from contextlib import ExitStack
from decimal import Decimal, InvalidOperation
import sys
from magi.accounts import account_identity
from magi.broker.ledger import ReadOnlyLedgerDatabase
from magi.portfolio import Portfolio
from magi.portfolio_valuation import PortfolioValuationService
from magi.storage import StorageError
from magi.valuation_presentation import render


def threshold(value):
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise argparse.ArgumentTypeError('Use a finite nonnegative native-currency amount.') from None


def main(argv=None, *, portfolio=None, market=None, broker=None):
    parser = argparse.ArgumentParser(prog='python main.py portfolio live', description='Read-only live ledger valuation; no trades or imports.')
    parser.add_argument('--currency', type=str.upper, choices=('USD','KRW'), default='KRW', help='preferred display currency; native prices are preserved')
    parser.add_argument('--language', choices=('en','ko'), default='ko')
    parser.add_argument('--broker')
    parser.add_argument('--account', help='safe local account reference')
    parser.add_argument('--dust-threshold', type=threshold, help='presentation threshold in each position native currency; disabled by default')
    parser.add_argument('--hide-dust', action='store_true', help='hide dust rows only; totals still include them')
    args = parser.parse_args(argv)
    if args.hide_dust and args.dust_threshold is None:
        parser.error('--hide-dust requires --dust-threshold')
    try:
        filters = {}
        if args.broker is not None or args.account is not None:
            identity = account_identity(args.broker,args.account)
            filters = dict(broker_provider=identity.provider,broker_account_ref=identity.account_ref)
        ledger = portfolio if portfolio is not None else Portfolio(ReadOnlyLedgerDatabase())
        # Read before constructing any network clients. Missing/schema-incompatible
        # databases are never created or migrated by this command.
        positions = ledger.get_open_positions(**filters)
        with ExitStack() as stack:
            if market is None and positions:
                from magi.toss import TossSession
                from magi.market.toss import TossProvider
                from magi.market.service import MarketDataService
                from magi.broker.toss import TossBrokerProvider
                from magi.broker.service import BrokerService
                session = stack.enter_context(TossSession())
                market = MarketDataService(TossProvider(session=session))
                if broker is None:
                    broker = BrokerService(TossBrokerProvider(session=session))
            view = PortfolioValuationService(ledger,market,broker).value(display_currency=args.currency,**filters)
            print(render(view,language=args.language,dust_threshold=args.dust_threshold,hide_dust=args.hide_dust))
        return 0
    except (StorageError, ValueError):
        print('포트폴리오를 읽을 수 없거나 계좌 선택이 잘못되었습니다.' if args.language == 'ko'
              else 'Portfolio unavailable or invalid safe account selection.',file=sys.stderr)
        return 1
