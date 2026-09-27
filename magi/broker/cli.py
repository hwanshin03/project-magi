"""Read-only remote access; explicit import commands may write local opening balances."""
import argparse
import sys
from magi.accounts import PortfolioAccountIdentity
from magi.portfolio import Portfolio
from magi.storage import StorageError
from .ledger import ReadOnlyLedgerDatabase
from .models import BrokerErrorCode
from .presentation import ERRORS, account_label, value_or_na
from .reconciliation import ReconciliationEngine
from .service import BrokerService
from .toss import TossBrokerProvider


def _select(accounts, index):
    if not accounts:
        return None
    if index is None:
        return accounts[0] if len(accounts) == 1 else None
    return accounts[index-1] if 1 <= index <= len(accounts) else None


def main(argv=None, *, service=None, portfolio=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ('import-preview', 'import-position'):
        from .import_cli import main as import_cli
        return import_cli(argv, service=service, portfolio=portfolio)
    parser = argparse.ArgumentParser(prog='python main.py broker', description='Read-only broker access; explicit local imports; no trading.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('accounts')
    commands.add_parser('import-preview', help='read-only opening-balance preview')
    commands.add_parser('import-position', help='explicit single-position local opening balance')
    for name in ('holdings', 'balances', 'summary', 'reconcile'):
        command = commands.add_parser(name)
        command.add_argument('--account', type=int, help='Displayed account index, never a full account number')
    args = parser.parse_args(argv)
    if getattr(args, 'account', None) is not None and args.account < 1:
        parser.error('Account index must be positive')
    provider = None
    try:
        if service is None:
            provider = TossBrokerProvider()
            service = BrokerService(provider)
        if args.command == 'balances' and not service.provider.supports_cash_balances:
            print('Cash balances are unsupported by this read-only adapter; no order-related endpoint is used.', file=sys.stderr)
            return 1
        result = service.get_accounts()
        if result.error:
            print(ERRORS[result.error], file=sys.stderr)
        if result.data is None:
            return 1
        accounts = result.data.accounts
        if args.command == 'accounts':
            print('=== BROKER ACCOUNTS ===')
            print(f'Fetched at: {result.data.fetched_at.isoformat()} | Stale: {result.data.is_stale}')
            for index, account in enumerate(accounts, 1):
                print(f'{account_label(index)} | Provider: {account.provider} | Type: {value_or_na(account.account_type)}')
                print('Local portfolio reference: ' + PortfolioAccountIdentity.from_broker(account).account_ref)
            if not accounts:
                print('No broker accounts found.')
            return 0
        if result.error or result.data.is_stale:
            print('Fresh account discovery is required before selecting an account.', file=sys.stderr)
            return 1
        account = _select(accounts, args.account)
        if account is None:
            if not accounts:
                print('No broker accounts found.', file=sys.stderr)
            else:
                print(ERRORS[BrokerErrorCode.AMBIGUOUS], file=sys.stderr)
                for index in range(1, len(accounts)+1):
                    print(account_label(index), file=sys.stderr)
            return 1
        if args.command == 'balances':
            result = service.get_cash_balances(account)
        elif args.command == 'summary':
            result = service.get_account_summary(account)
        else:
            result = service.get_holdings(account)
        if result.error:
            print(ERRORS[result.error], file=sys.stderr)
        if args.command == 'reconcile':
            positions = None
            try:
                if portfolio is None:
                    portfolio = Portfolio(ReadOnlyLedgerDatabase())
                identity = PortfolioAccountIdentity.from_broker(account)
                positions = portfolio.get_open_positions_by_account(identity.provider, identity.account_ref)
            except StorageError:
                print('MAGI ledger is unavailable for reconciliation.', file=sys.stderr)
            report = ReconciliationEngine().compare(positions, result)
            print('=== MAGI ↔ BROKER RECONCILIATION ===')
            print('Selected broker account compared only with its matching local ledger account; no changes made.')
            if not report.available:
                print('Status: UNAVAILABLE')
                return 1
            for row in report.rows:
                print(f'{row.symbol} | {row.market or "UNSET"} | {row.currency}')
                print(f'MAGI Ledger: {value_or_na(row.ledger_quantity)} | Broker: {value_or_na(row.broker_quantity)}')
                print(f'Quantity difference (broker - MAGI): {value_or_na(row.quantity_difference)}')
                print(f'MAGI average cost: {value_or_na(row.ledger_average_cost)} | Broker average cost: {value_or_na(row.broker_average_cost)}')
                print(f'Cost difference: {value_or_na(row.cost_difference)} | Cost comparable: {row.cost_comparable}')
                print(f'Status: {row.status.value}')
            if report.import_previews:
                print(f'Broker-only previews: {len(report.import_previews)}. No transactions created; purchase dates/prices are not inferred.')
            if not report.rows:
                print('Both position snapshots are empty.')
            return 0
        if result.data is None:
            return 1
        data = result.data
        print(f'=== BROKER {args.command.upper()} ===')
        print(f'Fetched at: {data.fetched_at.isoformat()} | Stale: {data.is_stale}')
        if args.command == 'holdings':
            for holding in data.holdings:
                print(f'{holding.symbol} | {holding.market} | {holding.currency}')
                for label, value in [('Shares', holding.quantity), ('Average Cost', holding.average_cost),
                                     ('Current Price', holding.current_price), ('Market Value', holding.market_value),
                                     ('Unrealized P/L', holding.unrealized_pnl), ('Unrealized return (ratio)', holding.unrealized_return)]:
                    print(f'{label}: {value_or_na(value)}')
            if not data.holdings:
                print('No holdings.')
        elif args.command == 'summary':
            print(f'Asset count: {data.asset_count}')
            for total in data.totals:
                print(f'{total.currency}: Book cost {value_or_na(total.total_book_cost)} | Market value {value_or_na(total.total_market_value)} | P/L {value_or_na(total.total_unrealized_pnl)}')
        else:
            for balance in data.balances:
                print(f'{balance.currency}: Cash {value_or_na(balance.cash_balance)} | Available {value_or_na(balance.available_cash)} | Buying power {value_or_na(balance.buying_power)}')
            if not data.balances:
                print('No cash balances supplied; not interpreted as zero cash.')
        return 0
    except (ValueError, StorageError):
        print('Broker account could not be matched to a local MAGI account.', file=sys.stderr)
        return 1
    finally:
        if provider is not None:
            provider.close()
