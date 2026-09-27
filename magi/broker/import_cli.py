"""Explicit single-position local import; remote access remains read-only."""
import argparse
import sys
from magi.accounts import account_identity, PortfolioAccountIdentity, account_label
from magi.portfolio import Portfolio, PortfolioError
from magi.portfolio_presentation import OPENING_LABELS, HISTORY_WARNINGS
from magi.storage import DEFAULT_DB_PATH, StorageError, check_sensitive
from .importing import BrokerPositionImporter, ImportRejected
from .ledger import ReadOnlyLedgerDatabase
from .service import BrokerService
from .toss import TossBrokerProvider


def print_preview(preview, language='en'):
    print('=== BROKER POSITION IMPORT PREVIEW ===')
    print(f'Provider: {preview.account.provider}')
    print(f'Account: {account_label(preview.account.provider, preview.account.account_ref, language)}')
    print(f'Symbol: {preview.symbol} | Market: {preview.market} | Currency: {preview.currency}')
    if preview.asset_name:
        print(f'Asset name: {preview.asset_name}')
    print(f'Broker quantity: {preview.observed_quantity}')
    print(f'Broker average cost: {preview.observed_average_cost} {preview.currency}')
    print(f'Opening book cost: {preview.opening_book_cost} {preview.currency}')
    for label, value in [('Current price', preview.current_price), ('Market value', preview.market_value),
                         ('Current unrealized P/L', preview.unrealized_pnl)]:
        print(f'{label}: {value if value is not None else "UNKNOWN"}')
    print(f'As of / tracking starts: {preview.as_of.isoformat()}')
    print('Original purchase date: UNKNOWN')
    print('Historical realized P/L before tracking: UNKNOWN')
    print(HISTORY_WARNINGS[language])
    print(f'This import creates OPENING_BALANCE ({OPENING_LABELS[language]}), not historical BUY transactions.')


def main(argv, *, service=None, portfolio=None):
    parser = argparse.ArgumentParser(prog='python main.py broker', description='Explicit opening-balance bookkeeping; no broker orders.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('import-preview', 'import-position'):
        command = commands.add_parser(name)
        command.add_argument('symbol', nargs='?' if name == 'import-preview' else None)
        command.add_argument('--account', required=True, help='Safe local ref_... reference from broker accounts, not an index or number')
        command.add_argument('--provider', default='TOSS')
        command.add_argument('--market')
        command.add_argument('--currency')
        command.add_argument('--language', choices=('en', 'ko'), default='en')
        if name == 'import-position':
            command.add_argument('--confirm', action='store_true', help='Explicitly initialize exactly one local position')
            command.add_argument('--note', default='')
    args = parser.parse_args(argv)
    owner = None
    try:
        if args.command == 'import-position' and not args.confirm:
            raise ImportRejected('CONFIRMATION_REQUIRED', 'Use --confirm after reviewing the opening-balance preview.')
        identity = account_identity(args.provider, args.account)
        if identity.provider != 'TOSS':
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'Only the Toss adapter is currently available in this CLI.')
        check_sensitive(getattr(args, 'note', ''))
        if service is None:
            owner = TossBrokerProvider()
            service = BrokerService(owner)
        discovered = service.get_accounts()
        if discovered.error or discovered.data is None or discovered.data.is_stale:
            raise ImportRejected('UNAVAILABLE', 'Fresh account discovery is required.')
        matches = [a for a in discovered.data.accounts if not a.is_stale
                   and (a.provider, PortfolioAccountIdentity.from_broker(a).account_ref) == (identity.provider, identity.account_ref)]
        if len(matches) != 1:
            raise ImportRejected('IMPORT_NOT_ALLOWED', 'Safe reference must match exactly one current broker account.')
        result = service.get_holdings(matches[0])
        reader = portfolio
        if reader is None and DEFAULT_DB_PATH.exists():
            reader = Portfolio(ReadOnlyLedgerDatabase(DEFAULT_DB_PATH))
        importer = BrokerPositionImporter(reader)
        if args.symbol is not None:
            symbols = [args.symbol]
        elif result.error or result.data is None or result.data.is_stale:
            raise ImportRejected('UNAVAILABLE', 'Fresh holdings are required.')
        else:
            symbols = sorted({h.symbol for h in result.data.holdings})
        if not symbols:
            print('No broker positions to preview.')
        failed = False
        for symbol in symbols:
            try:
                preview = importer.preview(result, identity, symbol, market=args.market, currency=args.currency)
                print_preview(preview, args.language)
                if args.command == 'import-position':
                    # Only this explicit branch may create/migrate/write a database.
                    writer = portfolio if portfolio is not None else Portfolio()
                    event = BrokerPositionImporter(writer).import_position(
                        result, identity, symbol, confirmed=True, market=preview.market,
                        currency=preview.currency, notes=args.note)
                    print(f'OPENING_BALANCE recorded: {event.transaction_id}')
                    print('One local opening event recorded. No broker trade executed.')
                else:
                    print('Preview only. No ledger changes made.')
            except ImportRejected as error:
                if args.command == 'import-position':
                    raise
                print(str(error), file=sys.stderr)
                failed = True
        return 1 if failed else 0
    except PortfolioError as error:
        print(str(error), file=sys.stderr)
        return 1
    except StorageError:
        print('Local ledger is unavailable; preview never migrates it. Initialize its schema with a local portfolio command.', file=sys.stderr)
        return 1
    except ValueError:
        print('Invalid broker account or normalized holding; no opening balance recorded.', file=sys.stderr)
        return 1
    finally:
        if owner is not None:
            owner.close()
