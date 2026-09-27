"""Explicit read-only live market requests. No client is created during imports/help."""
import argparse
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
import sys

from .presentation import ERRORS
from .service import MarketDataService
from .toss import TossProvider


def _encode(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError('Unsupported output type')


def main(argv=None, *, service=None):
    parser = argparse.ArgumentParser(prog='python main.py market', description='Read-only market data; no trading.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('quote', 'history', 'candles'):
        command = commands.add_parser(name)
        command.add_argument('symbol')
        command.add_argument('--market', required=True, choices=('US', 'KR'))
        if name == 'history':
            command.add_argument('--days', type=int, default=30, help='Number of trading-day candles')
        if name == 'candles':
            command.add_argument('--interval', choices=('1m',), default='1m')
            command.add_argument('--limit', type=int, default=30)
    fx = commands.add_parser('fx')
    fx.add_argument('base', choices=('USD', 'KRW'))
    fx.add_argument('quote', choices=('USD', 'KRW'))
    args = parser.parse_args(argv)
    provider = None
    try:
        if service is None:
            provider = TossProvider()
            service = MarketDataService(provider)
        if args.command == 'quote':
            result = service.get_quote(args.symbol, args.market)
        elif args.command == 'history':
            result = service.get_daily_candles(args.symbol, args.market, args.days)
        elif args.command == 'candles':
            result = service.get_minute_candles(args.symbol, args.market, args.limit, args.interval)
        else:
            result = service.get_fx_rate(args.base, args.quote)
        if result.error is not None:
            print(ERRORS[result.error], file=sys.stderr)
        if result.data is None:
            return 1
        print(json.dumps(asdict(result.data), default=_encode, ensure_ascii=False, indent=2))
        return 0
    except ValueError:
        print('Invalid market-data arguments.', file=sys.stderr)
        return 2
    finally:
        if provider is not None:
            provider.close()
