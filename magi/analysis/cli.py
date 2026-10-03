"""Existing argparse CLI style. Default plan is offline; --execute opts into APIs."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import sys
from magi.research.balancing.models import InstrumentIdentity, TargetIdentity, EntityIdentity
from magi.research.serialization import encode
from magi.research.balancing.assessment import AssessmentPolicy
from .models import AnalysisRequest, AnalysisError, CHANNELS
from .collection import ResearchServices
from .orchestrator import analyze


def live_services(request, stack):
    """Create only requested existing adapters. The stack owns every live client."""
    from magi.research.providers import SECProvider, DARTProvider, ResearchError
    from magi.research.news.providers import MarketauxProvider, MarketauxError
    from magi.research.news.service import NewsService
    from magi.research.company_sources.nvidia import NvidiaProvider
    from magi.research.company_sources.samsung import SamsungProvider
    from magi.research.company_sources.transport import OfficialTransport
    from magi.research.company_sources.service import CompanySourceService
    from magi.research.regulatory.transport import RegulatoryTransport
    from magi.research.regulatory.provider import GovernmentProvider
    from magi.research.regulatory.service import RegulatoryService
    def owned(client):
        stack.callback(client.close)
        return client
    services=ResearchServices()
    instrument=request.target.instrument
    for channel in request.requested:
        try:
            if channel=='sec': services.sec=owned(SECProvider())
            elif channel=='dart': services.dart=owned(DARTProvider())
            elif channel=='news':
                services.news=NewsService(owned(MarketauxProvider(country=instrument.market.lower(),
                    market=instrument.market,languages=request.languages)))
            elif channel=='company':
                provider={('US','NVDA'):NvidiaProvider,('KR','005930'):SamsungProvider}.get((instrument.market,instrument.symbol))
                if provider: services.company=CompanySourceService(provider(transport=owned(OfficialTransport())))
            elif channel=='regulatory':
                services.regulatory=RegulatoryService(GovernmentProvider(transport=owned(RegulatoryTransport())))
        except (ValueError,ResearchError,MarketauxError):
            # Missing configuration remains an explicit unavailable channel.
            continue
    return services


def main(argv=None, *, services=None, agents=None, now=None):
    parser=argparse.ArgumentParser(prog='python main.py analyze',description='In-memory research analysis. Default: offline plan. No trades or persistence.')
    parser.add_argument('symbol')
    parser.add_argument('--market',required=True,choices=('US','KR'))
    parser.add_argument('--question',required=True)
    parser.add_argument('--as-of',help='Timezone-aware ISO timestamp; defaults to captured CLI-start time')
    parser.add_argument('--family',action='append',choices=tuple(CHANNELS))
    parser.add_argument('--required',action='append',default=[],choices=tuple(CHANNELS))
    parser.add_argument('--language',action='append',default=[])
    parser.add_argument('--since',help='Timezone-aware publication horizon')
    parser.add_argument('--issuer',help='Explicit SEC CIK or DART corp code; requires mapping attribution')
    parser.add_argument('--mapping-reference')
    parser.add_argument('--year',type=int)
    parser.add_argument('--report',default='annual',choices=('annual','quarter','q1','half','q3'))
    parser.add_argument('--currency')
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--execute',action='store_true',help='Explicitly call research and model APIs; may consume quota')
    args=parser.parse_args(argv)
    try:
        target=TargetIdentity(InstrumentIdentity(args.market,args.symbol),
            EntityIdentity('SEC' if args.market=='US' else 'DART',args.issuer) if args.issuer else None,
            'explicit-cli-input' if args.issuer else None,args.mapping_reference)
        as_of=datetime.fromisoformat(args.as_of) if args.as_of else (now or (lambda: datetime.now(timezone.utc)))()
        request=AnalysisRequest(target,args.question,as_of,
            requested=tuple(args.family or ('company','news','regulatory')),required=tuple(args.required),
            languages=tuple(args.language),year=args.year,report=args.report,currency=args.currency,limit=args.limit,
            assessment_policy=AssessmentPolicy(published_since=datetime.fromisoformat(args.since) if args.since else None))
        if not args.execute:
            print(json.dumps({'mode':'OFFLINE_PLAN','request_id':request.request_id,
                'target':encode(request.target),'as_of':request.as_of.isoformat(),'requested':request.requested,
                'required':request.required,'policy':request.version,'market_provenance':'DEFERRED',
                'pipeline':['collection','universe','grouping','assessment','selection','agents','deterministic_vote']},sort_keys=True))
            return 0
        with ExitStack() as stack:
            if services is None: services=live_services(request,stack)
            if agents is None:
                from magi.melchior import Melchior
                from magi.balthasar import Balthasar
                from magi.casper import Casper
                agents=[]
                for cls in (Melchior,Balthasar,Casper):
                    agent=cls(); agents.append(agent)
                    close=getattr(agent.client,'close',None)
                    if callable(close): stack.callback(close)
            result=analyze(request,services=services,agents=agents)
            # Compact audit output. Full lossless export is an explicit Python API.
            print(json.dumps({'artifact_id':result.artifact_id,'request_id':request.request_id,
                'target':encode(request.target),'as_of':request.as_of.isoformat(),
                'selection_id':result.selection.selection_id,'availability':encode(result.universe.availability),
                'warnings':result.warnings,'vote':json.loads(str(result.vote))},sort_keys=True,ensure_ascii=False))
            return 0
    except AnalysisError as error:
        print(error.code,file=sys.stderr);return 1
    except Exception:
        print('ANALYSIS_CONFIGURATION_OR_EXECUTION_FAILED',file=sys.stderr);return 1
