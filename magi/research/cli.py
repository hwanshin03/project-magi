"""Explicit manual official research inspection. Never calls LLMs or the ledger."""
import argparse
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from collections.abc import Mapping
import json
import sys
from .models import EvidencePack, ResearchSource, EvidenceItem
from .serialization import to_dict
from .security import safe_text
from .providers import SECProvider,DARTProvider,ResearchError

REPORT_LABELS={'ko':{'annual':'사업보고서','q1':'1분기 보고서','half':'반기보고서','q3':'3분기 보고서'},
               'en':{'annual':'Annual report','q1':'First-quarter report','half':'Half-year report','q3':'Third-quarter report'}}

LABELS={'en':{'issuer':'Issuer','profile':'Company profile','filings':'Official filings','facts':'Financial facts',
              'financials':'Financial statements','pack':'Evidence pack','error':'Research error'},
        'ko':{'issuer':'발행사','profile':'회사 개요','filings':'공시','facts':'재무 근거',
              'financials':'재무제표','pack':'근거 자료 묶음','error':'리서치 오류'}}


def plain(value):
    if isinstance(value,(ResearchSource,EvidenceItem,EvidencePack)): return to_dict(value)
    if isinstance(value,Enum): return value.value
    if isinstance(value,(datetime,date)): return value.isoformat()
    if isinstance(value,Decimal): return str(value)
    if isinstance(value,Mapping): return {k:plain(v) for k,v in value.items()}
    if is_dataclass(value): return {f.name:plain(getattr(value,f.name)) for f in fields(value)}
    if isinstance(value,(tuple,list)): return [plain(v) for v in value]
    return value


def main(argv=None,*,provider=None):
    parser=argparse.ArgumentParser(prog='python main.py research',description='Explicit official-source research; no recommendations or trades.')
    parser.add_argument('provider',choices=('sec','dart'))
    parser.add_argument('command',choices=('issuer','profile','filings','facts','financials','pack'))
    parser.add_argument('identifier')
    parser.add_argument('--language',choices=('ko','en'),default='ko')
    parser.add_argument('--year',type=int)
    parser.add_argument('--report',choices=('annual','q1','half','q3'),default='annual')
    parser.add_argument('--division',choices=('CFS','OFS'),default='CFS')
    parser.add_argument('--concept',action='append',help='SEC concept filter; may repeat')
    parser.add_argument('--max-facts',type=int,default=25000)
    parser.add_argument('--start',help='DART filing start YYYYMMDD')
    parser.add_argument('--end',help='DART filing end YYYYMMDD')
    parser.add_argument('--page',type=int,default=1)
    args=parser.parse_args(argv)
    if args.provider=='dart' and args.command in ('facts','financials','pack') and args.year is None:
        parser.error('--year is required for DART financials/pack')
    owned=provider is None
    try:
        if provider is None: provider=SECProvider() if args.provider=='sec' else DARTProvider()
        if args.command=='issuer': result=provider.resolve_issuer(args.identifier)
        elif args.command=='profile': result=provider.get_company_profile(args.identifier)
        elif args.command=='filings':
            options={} if args.provider=='sec' else dict(start=args.start,end=args.end,page=args.page)
            result=provider.list_filings(args.identifier,**options)
        else:
            options=dict(concepts=args.concept,max_facts=args.max_facts) if args.provider=='sec' else dict(year=args.year,report=args.report,division=args.division)
            method=provider.build_evidence_pack if args.command=='pack' else provider.get_financial_facts
            result=method(args.identifier,**options)
        if result.error: raise ResearchError(result.error)
        content=json.dumps(plain(result.data),ensure_ascii=False,indent=2,allow_nan=False)
        safe_text(content)
        print('=== '+args.provider.upper()+' '+LABELS[args.language][args.command]+' ===')
        if args.provider=='dart' and args.command in ('facts','financials','pack'):
            print(REPORT_LABELS[args.language][args.report])
        print(content)
        return 0
    except ResearchError as error:
        print(LABELS[args.language]['error']+': '+error.code.value,file=sys.stderr);return 1
    except ValueError:
        print(LABELS[args.language]['error']+': INVALID_RESPONSE',file=sys.stderr);return 1
    finally:
        if owned and provider is not None: provider.close()
