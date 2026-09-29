"""Explicit news inspection only. No agent calls, trading or database writes."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import sys

from ..cli import plain
from ..serialization import to_dict
from .base import NewsQuery
from .presentation import render_news
from .selection import select_news, NewsSelectionPolicy
from .service import NewsService
from .providers import MarketauxProvider, MarketauxError


def main(argv=None, *, provider=None, now=None):
    parser = argparse.ArgumentParser(prog='python main.py research news',
        description='Explicit Marketaux news inspection; invoking a command may use API quota.')
    parser.add_argument('command', choices=('entity', 'latest', 'pack'))
    parser.add_argument('ticker')
    parser.add_argument('--provider', choices=('marketaux',), default='marketaux')
    parser.add_argument('--country', default='us')
    parser.add_argument('--market', choices=('US', 'KR'))
    parser.add_argument('--exchange')
    parser.add_argument('--type', dest='entity_type', default='equity')
    parser.add_argument('--search', help='Explicit provider search text, e.g. company name')
    parser.add_argument('--hours', type=int, default=72)
    parser.add_argument('--limit', type=int, default=3, help='One page only; provider may return fewer articles')
    parser.add_argument('--language', choices=('ko', 'en'), default='ko', help='Presentation language')
    parser.add_argument('--news-language', action='append', default=[], help='Provider article language filter')
    args = parser.parse_args(argv)
    if not 1 <= args.hours <= 8760 or not 1 <= args.limit <= 1000:
        parser.error('hours must be 1..8760 and limit 1..1000')
    owned = provider is None
    try:
        if provider is None:
            provider = MarketauxProvider(country=args.country, market=args.market,
                exchange=args.exchange, entity_type=args.entity_type, search=args.search,
                languages=tuple(args.news_language), now=now)
        if args.command == 'entity':
            result = provider.resolve_entity(args.ticker, search=args.search, country=args.country,
                market=args.market, exchange=args.exchange)
            print('종목 확인' if args.language == 'ko' else 'Entity resolution')
            print('UNTRUSTED RESEARCH DATA')
            print(json.dumps(plain(result), ensure_ascii=False, indent=2))
            return 0 if result.status == 'RESOLVED' else 1
        as_of = (now or (lambda: datetime.now(timezone.utc)))()
        query = NewsQuery(args.ticker, args.search or args.ticker, as_of,
                          as_of - timedelta(hours=args.hours), args.limit)
        pack = NewsService(provider).collect(query)
        policy = NewsSelectionPolicy(max_articles=min(args.limit, 20))
        selected = select_news(pack, policy)
        print(render_news(selected, language=args.language))
        if args.command == 'pack':
            print(json.dumps(to_dict(selected), ensure_ascii=False, indent=2))
        if provider.status:
            print(('제공자 사용량' if args.language == 'ko' else 'Provider usage') + ': ' +
                  json.dumps(provider.status, sort_keys=True))
        return 0
    except MarketauxError as error:
        print(('뉴스 오류: ' if args.language == 'ko' else 'News error: ') + error.code,
              file=sys.stderr)
        return 1
    except ValueError:
        print('MARKETAUX_INVALID_RESPONSE', file=sys.stderr)
        return 1
    finally:
        if owned and provider is not None:
            provider.close()
