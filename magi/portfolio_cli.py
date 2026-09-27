"""Bookkeeping CLI over the existing portfolio service; never executes trades."""

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import re
import sys

from magi.accounts import account_identity, account_label
from magi.memory import AnalysisMemory
from magi.portfolio import Portfolio, PortfolioError, decimal_value, TradeAction, HistoryCompleteness
from magi.portfolio_presentation import HISTORY_WARNINGS
from magi.storage import StorageError


class CLIError(ValueError):
    """Expected user-facing error, distinct from programming errors."""


def _amount(value, label, positive=False):
    try:
        candidate = Decimal(value)
        if candidate.is_finite() and candidate < 0:
            message = 'Quantity must be greater than zero.' if positive else f'{label} cannot be negative.'
            raise argparse.ArgumentTypeError(message)
        number = decimal_value(candidate)
        if positive and number == 0:
            raise argparse.ArgumentTypeError('Quantity must be greater than zero.')
        return number
    except (InvalidOperation, PortfolioError):
        raise argparse.ArgumentTypeError(f'{label} must be a finite decimal within supported precision.') from None


def _date(value):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        date.fromisoformat(value)
        return value
    except ValueError:
        raise argparse.ArgumentTypeError('Date must use YYYY-MM-DD format.') from None


def _limit(value):
    try:
        limit = int(value)
        if limit < 1:
            raise ValueError()
        return limit
    except ValueError:
        raise argparse.ArgumentTypeError('Limit must be a positive integer.') from None


def _currency(value):
    if not re.fullmatch(r'[A-Za-z]{3}', value):
        raise argparse.ArgumentTypeError('Currency must be a three-letter code.')
    return value.upper()


def _account_options(parser, all_accounts=False):
    parser.add_argument('--broker', help='provider, paired with --account')
    parser.add_argument('--account', help='safe local reference; never an account number')
    if all_accounts:
        parser.add_argument('--all-accounts', action='store_true', help='show separate rows for all accounts')


def _account_filters(args):
    broker, account = getattr(args, 'broker', None), getattr(args, 'account', None)
    if getattr(args, 'all_accounts', False) and (broker is not None or account is not None):
        raise CLIError('--all-accounts cannot be combined with --broker or --account.')
    if broker is None and account is None and args.command in ('list', 'recent', 'accounts'):
        return {}
    try:
        identity = account_identity(broker, account)
    except ValueError:
        raise CLIError("Invalid account selection: specify both broker and safe account reference.") from None
    return dict(broker_provider=identity.provider, broker_account_ref=identity.account_ref)


def _identity_options(parser):
    _account_options(parser)
    parser.add_argument('--currency', type=_currency, default='USD', help='currency (default: USD)')
    parser.add_argument('--market', help='market; omitted means no inferred exchange')


def build_parser():
    parser = argparse.ArgumentParser(
        prog='main.py', description='No arguments: MAGI analysis. Portfolio commands: bookkeeping only.')
    commands = parser.add_subparsers(dest='mode', required=True)
    portfolio = commands.add_parser('portfolio', help='record and inspect trades that already occurred')
    actions = portfolio.add_subparsers(dest='command', required=True)
    for action in ('buy', 'sell'):
        command = actions.add_parser(action, help=f'record an already executed {action.upper()}')
        command.add_argument('symbol')
        command.add_argument('quantity', type=lambda v: _amount(v, 'Quantity', positive=True))
        command.add_argument('price', type=lambda v: _amount(v, 'Price'))
        command.add_argument('--date', type=_date, help='execution date YYYY-MM-DD (default: now)')
        command.add_argument('--fees', type=lambda v: _amount(v, 'Fees'), default=Decimal('0'))
        _identity_options(command)
        command.add_argument('--asset-name', help='optional descriptive name')
        command.add_argument('--note', default='', help='optional entry/sale rationale')
        command.add_argument('--run-id', help='optional existing MAGI analysis run ID')
    actions.add_parser('live', help='explicit read-only live valuation; use portfolio live --help')
    show = actions.add_parser('show', help='show a position; no prices are fetched')
    show.add_argument('symbol')
    _identity_options(show)
    show.add_argument('--current-price', type=lambda v: _amount(v, 'Current price'),
                      help='manually supplied price in the position currency')
    listing = actions.add_parser('list', help='list open positions across all currencies and markets')
    _account_options(listing, all_accounts=True)
    listing.add_argument('--closed', action='store_true', help='show closed positions instead')
    history = actions.add_parser('history', help='show execution-ordered ledger history for one position')
    history.add_argument('symbol')
    _identity_options(history)
    recent = actions.add_parser('recent', help='show newest executions across all symbols')
    _account_options(recent, all_accounts=True)
    actions.add_parser('accounts', help='list local ledger accounts only')
    recent.add_argument('--limit', type=_limit, default=10, help='positive record limit (default: 10)')
    return parser


def number(value):
    if value is None:
        return 'N/A'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def signed(value):
    return ('+' if value >= 0 else '') + number(value)


def percent(value):
    if value is None:
        return 'N/A'
    with localcontext() as context:
        context.prec = 80
        return format(value * 100, '+.2f') + '%'


def print_transaction(trade):
    if trade.action == TradeAction.OPENING_BALANCE:
        print(f'OPENING_BALANCE {number(trade.quantity)} {trade.symbol} | Opening unit cost: {number(trade.opening_unit_cost)} {trade.currency}')
        print(f'Opening book cost: {number(trade.opening_book_cost)} {trade.currency}')
        print(f'As of / tracking starts: {trade.as_of}')
        print('Original purchase date: UNKNOWN; prior realized P/L: UNKNOWN')
        print(HISTORY_WARNINGS['en'])
    else:
        print(f'{trade.action.value} {number(trade.quantity)} {trade.symbol} @ {number(trade.price_per_share)} {trade.currency}')
    print(f'Account: {account_label(trade.broker_provider, trade.broker_account_ref)}')
    if trade.action != TradeAction.OPENING_BALANCE:
        print(f'Date: {trade.timestamp}')
        print(f'Fees: {number(trade.fees)} {trade.currency}')
    print(f'Market: {trade.market or "UNSET"}')
    print(f'Transaction ID: {trade.transaction_id}')
    if trade.asset_name:
        print(f'Asset name: {trade.asset_name}')
    if trade.notes:
        print(f'Note: {trade.notes}')
    if trade.linked_analysis_run_id:
        print(f'Linked analysis run ID: {trade.linked_analysis_run_id}')


def print_position(position, asset_name=None):
    print('\n=== CURRENT POSITION ===\n')
    print(f'Account: {account_label(position.broker_provider, position.broker_account_ref)}')
    print(f'Symbol: {position.symbol}')
    if asset_name:
        print(f'Asset name: {asset_name}')
    print(f'Market: {position.market or "UNSET"}')
    print(f'Currency: {position.currency}')
    print(f'History: {position.history_completeness.value}')
    if position.history_completeness == HistoryCompleteness.OPENING_BALANCE_HISTORY:
        print(f'First tracked by MAGI: {position.tracking_start_date}')
        print('Original purchase date: UNKNOWN')
        print(f'First recorded BUY: {position.first_recorded_buy_date or "NONE"}')
        print('Historical realized P/L before tracking: UNKNOWN')
        print(HISTORY_WARNINGS['en'])
    else:
        print(f'First purchase: {position.first_purchase_date}')
    print(f'Latest transaction: {position.latest_transaction_date}')
    print(f'Total shares purchased: {number(position.total_shares_purchased)}')
    print(f'Total shares sold: {number(position.total_shares_sold)}')
    print(f'Shares remaining: {number(position.shares_held)}')
    print(f'Average book cost: {number(position.average_book_cost)}')
    print(f'Remaining book cost: {number(position.book_cost)}')
    print('\n=== REALIZED PERFORMANCE ===\n')
    scope = 'Tracked realized P/L' if position.history_completeness == HistoryCompleteness.OPENING_BALANCE_HISTORY else 'Realized P/L'
    print(f'{scope}: {signed(position.realized_pnl)} {position.currency}')
    print(f'Realized return: {percent(position.realized_return)}')
    print('\n=== MANUAL PRICE VALUATION ===\n')
    print(f'Current price: {number(position.current_price) if position.current_price is not None else "NOT PROVIDED"}')
    print(f'Market value: {number(position.market_value)}')
    print(f'Unrealized P/L: {signed(position.unrealized_pnl) if position.unrealized_pnl is not None else "N/A"}')
    print(f'Unrealized return: {percent(position.unrealized_return)}')


def _asset_name(portfolio, position):
    trades = portfolio.get_transactions(position.symbol, currency=position.currency, market=position.market,
                                        broker_provider=position.broker_provider, broker_account_ref=position.broker_account_ref)
    return next((trade.asset_name for trade in reversed(trades) if trade.asset_name), None)


def _record(args, portfolio):
    linked = None
    if args.run_id is not None:
        linked = AnalysisMemory(portfolio.database).get_analysis(args.run_id)
        if linked is None:
            raise CLIError(f'Analysis run {args.run_id} was not found.')
    try:
        trade = portfolio.record_transaction(
            args.symbol, args.command.upper(), args.quantity, args.price,
            currency=args.currency, market=args.market, executed_at=args.date, fees=args.fees,
            asset_name=args.asset_name, notes=args.note, linked_analysis_run_id=args.run_id, **_account_filters(args))
    except PortfolioError as error:
        if str(error).startswith('Sell exceeds holdings'):
            raise CLIError(
                f'Cannot sell {number(args.quantity)} shares of {args.symbol.upper()}: '
                'this would exceed holdings in execution order. No transaction was recorded.') from None
        raise
    print('\n=== RECORDED TRANSACTION ===\n')
    print_transaction(trade)
    # The write has committed. A subsequent read/display failure must not suggest
    # re-recording an already successful transaction.
    try:
        position = portfolio.get_position(trade.symbol, currency=trade.currency, market=trade.market,
                                          broker_provider=trade.broker_provider, broker_account_ref=trade.broker_account_ref)
        print_position(position, _asset_name(portfolio, position))
    except StorageError:
        print('Transaction recorded; position display is currently unavailable. Do not re-record this trade.', file=sys.stderr)
    if linked:
        print('\n=== LINKED MAGI ANALYSIS ===\n')
        print(f'Run ID: {linked.run_id}')
        print(f'Final action: {linked.vote.final_action.value}')
        for agent in linked.vote.agent_results:
            position = agent.position.value if agent.position else 'NO POSITION'
            print(f'{agent.agent}: {position} ({agent.availability.value})')


def _run(args, portfolio):
    if args.command in ('buy', 'sell'):
        _record(args, portfolio)
    elif args.command == 'show':
        position = portfolio.get_position(args.symbol, current_price=args.current_price,
                                          currency=args.currency, market=args.market, **_account_filters(args))
        if position is None:
            raise CLIError(f'No position found for {args.symbol.upper()} ({args.currency}).')
        print_position(position, _asset_name(portfolio, position))
    elif args.command == 'list':
        positions = portfolio.get_closed_positions(**_account_filters(args)) if args.closed else portfolio.get_open_positions(**_account_filters(args))
        print(f'\n=== {"CLOSED" if args.closed else "OPEN"} POSITIONS ===\n')
        if not positions:
            print(f'No {"closed" if args.closed else "open"} positions.')
            return
        rows = [('SYMBOL', 'SHARES', 'AVG COST', 'CURRENCY', 'MARKET', 'TRACKED REALIZED P/L', 'ACCOUNT', 'HISTORY')]
        rows.extend((p.symbol, number(p.shares_held), number(p.average_book_cost), p.currency,
                     p.market or 'UNSET', signed(p.realized_pnl), account_label(p.broker_provider, p.broker_account_ref), p.history_completeness.value) for p in positions)
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        for row in rows:
            print('  '.join(value.ljust(width) for value, width in zip(row, widths)).rstrip())
    elif args.command == 'history':
        history = portfolio.get_position_history(args.symbol, currency=args.currency, market=args.market, **_account_filters(args))
        print(f'\n=== {args.symbol.upper()} TRANSACTION HISTORY ===\n')
        if not history:
            print('No transactions found.')
        for entry in history:
            print_transaction(entry.transaction)
            print()
    elif args.command == 'accounts':
        print('\n=== MAGI PORTFOLIO ACCOUNTS ===\n')
        accounts = portfolio.get_accounts_with_positions()
        if not accounts:
            print('No local ledger accounts.')
        for entry in accounts:
            identity = entry.identity
            print(account_label(identity.provider, identity.account_ref))
            print(f'Provider: {identity.provider}; local reference: {identity.account_ref}')
            print(f'Open positions: {entry.open_positions}; closed positions: {entry.closed_positions}')
    elif args.command == 'recent':
        trades = portfolio.get_transactions(**_account_filters(args))
        print(f'\n=== RECENT TRANSACTIONS (up to {args.limit}) ===\n')
        if not trades:
            print('No transactions found.')
        for trade in reversed(trades[-args.limit:]):
            print_transaction(trade)
            print()


def main(argv=None, *, portfolio=None):
    argv = sys.argv[1:] if argv is None else argv
    if list(argv[:2]) == ['portfolio', 'live']:
        from magi.portfolio_live_cli import main as live_main
        return live_main(argv[2:], portfolio=portfolio)
    args = build_parser().parse_args(argv)
    try:
        _account_filters(args)
        _run(args, portfolio if portfolio is not None else Portfolio())
    except StorageError:
        print('Portfolio memory is currently unavailable.', file=sys.stderr)
        return 1
    except (PortfolioError, CLIError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0
