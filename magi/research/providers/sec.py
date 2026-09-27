"""Official SEC issuer, recent submissions, and structured company-facts adapter."""
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from ..models import ResearchSource,SourceType,Authority,EvidenceItem,SourceLocator,Category,EvidencePack
from ..selection import deduplicate
from .base import Issuer,CompanyProfile,FinancialFacts,ResearchError,ErrorCode,boundary
from .transport import ResearchHTTP


def cik(value):
    value=str(value)
    if not re.fullmatch(r'\d{1,10}',value) or int(value)==0: raise ResearchError(ErrorCode.INVALID_REQUEST)
    return value.zfill(10)


def day(value):
    return date.fromisoformat(value) if value else None


def midnight(value):
    d=day(value)
    return datetime.combine(d,datetime.min.time(),timezone.utc) if d else None


def accession(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}',value): raise ValueError('Invalid accession')
    return value


def category(concept):
    return {'Revenues':Category.FINANCIAL,'RevenueFromContractWithCustomerExcludingAssessedTax':Category.FINANCIAL,
        'SalesRevenueNet':Category.FINANCIAL,'NetIncomeLoss':Category.PROFITABILITY,
        'ProfitLoss':Category.PROFITABILITY,'CashAndCashEquivalentsAtCarryingValue':Category.BALANCE_SHEET,
        'LongTermDebtCurrent':Category.BALANCE_SHEET,'LongTermDebtNoncurrent':Category.BALANCE_SHEET,
        'Assets':Category.BALANCE_SHEET,'Liabilities':Category.BALANCE_SHEET,
        'NetCashProvidedByUsedInOperatingActivities':Category.CASH_FLOW}.get(concept,Category.OTHER)


class SECProvider:
    def __init__(self,*,http=None,**options):
        self.http=http if http is not None else ResearchHTTP('SEC',**options)
        self._owns=http is None
    def close(self):
        if self._owns: self.http.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def _submissions(self,code):
        payload=self.http.get('/submissions/CIK'+code+'.json');data=payload.json()
        if cik(data['cik'])!=code: raise ValueError('Issuer mismatch')
        return data,payload.retrieved_at

    def _resolve(self,identifier):
        if not isinstance(identifier,str): raise ResearchError(ErrorCode.INVALID_REQUEST)
        normalized=identifier.strip().upper()
        if normalized.isdigit():
            code=cik(normalized);data,stamp=self._submissions(code)
            tickers=data.get('tickers',[]);exchanges=data.get('exchanges',[])
            if not isinstance(tickers,list) or not isinstance(exchanges,list): raise ValueError('Invalid issuer lists')
            return Issuer('SEC',code,data['name'],'US',stamp,tickers[0] if len(tickers)==1 else None,
                exchanges[0] if len(exchanges)==1 else None)
        if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,15}',normalized): raise ResearchError(ErrorCode.INVALID_REQUEST)
        payload=self.http.get('/files/company_tickers_exchange.json');data=payload.json()
        fields=data['fields'];records=data['data']
        if not isinstance(fields,list) or len(fields)!=len(set(fields)) or not {'cik','name','ticker','exchange'}<=set(fields) or not isinstance(records,list):
            raise ValueError('Invalid issuer mapping')
        matches=[]
        for row in records:
            if not isinstance(row,list) or len(row)!=len(fields): raise ValueError('Invalid issuer row')
            item=dict(zip(fields,row))
            if item['ticker']==normalized:
                matches.append(Issuer('SEC',cik(item['cik']),item['name'],'US',payload.retrieved_at,normalized,item['exchange']))
        if not matches: raise ResearchError(ErrorCode.NOT_FOUND)
        if len(matches)!=1: raise ResearchError(ErrorCode.AMBIGUOUS)
        return matches[0]

    @boundary
    def resolve_issuer(self,identifier): return self._resolve(identifier)

    @boundary
    def get_company_profile(self,identifier):
        issuer=self._resolve(identifier);data,stamp=self._submissions(issuer.provider_issuer_id)
        return CompanyProfile(issuer,{k:data[k] for k in ('name','sic','sicDescription','fiscalYearEnd','entityType') if data.get(k) is not None},stamp)

    def _source(self,issuer,acc,form,filed,report,stamp,document=None,accepted=None):
        acc=accession(acc)
        base='https://www.sec.gov/Archives/edgar/data/'+str(int(issuer.provider_issuer_id))+'/'+acc.replace('-','')+'/'
        if document and (not isinstance(document,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',document)):
            raise ValueError('Invalid primary document')
        published=datetime.fromisoformat(accepted.replace('Z','+00:00')) if accepted else midnight(filed)
        return ResearchSource(SourceType.SEC_FILING,Authority.PRIMARY,'SEC',issuer.company_name+' '+form,
            issuer.company_name,stamp,url=base+(document or acc+'-index.html'),published_at=published,
            ticker=issuer.ticker,market='US',company_name=issuer.company_name,document_type=form,external_id=acc,
            metadata={'issuer_id':issuer.issuer_id,'cik':issuer.provider_issuer_id,'accession':acc,'filing_date':filed,
                'report_date':report,'primary_document':document,'amendment':form.endswith('/A'),
                'publication_precision':'instant' if accepted else 'date; midnight UTC convention'})

    def _filings(self,issuer,forms=None,limit=100):
        if type(limit) is not int or not 1<=limit<=1000: raise ResearchError(ErrorCode.INVALID_REQUEST)
        data,stamp=self._submissions(issuer.provider_issuer_id);recent=data['filings']['recent']
        keys=('accessionNumber','filingDate','reportDate','form','primaryDocument')
        if any(not isinstance(recent[k],list) for k in keys): raise ValueError('Invalid submissions')
        count=len(recent['accessionNumber'])
        if any(len(recent[k])!=count for k in keys): raise ValueError('Unequal submission arrays')
        accepted=recent.get('acceptanceDateTime',[None]*count)
        if not isinstance(accepted,list) or len(accepted)!=count: raise ValueError('Invalid acceptance times')
        output=[]
        for i in range(count):
            form=recent['form'][i]
            if forms is not None and form not in forms: continue
            day(recent['filingDate'][i]);day(recent['reportDate'][i])
            output.append(self._source(issuer,recent['accessionNumber'][i],form,recent['filingDate'][i],
                recent['reportDate'][i] or None,stamp,recent['primaryDocument'][i] or None,accepted[i]))
            if len(output)>=limit: break
        return tuple(output)

    @boundary
    def list_filings(self,identifier,*,forms=None,limit=100):
        return self._filings(self._resolve(identifier),forms,limit)

    @boundary
    def get_financial_facts(self,identifier,*,concepts=None,max_facts=25000):
        if type(max_facts) is not int or not 1<=max_facts<=50000: raise ResearchError(ErrorCode.INVALID_REQUEST)
        issuer=self._resolve(identifier);payload=self.http.get('/api/xbrl/companyfacts/CIK'+issuer.provider_issuer_id+'.json')
        data=payload.json()
        if cik(data['cik'])!=issuer.provider_issuer_id: raise ValueError('Issuer mismatch')
        filings={s.external_id:s for s in self._filings(issuer,limit=1000)}
        sources={};evidence=[]
        for taxonomy,items in sorted(data['facts'].items()):
            for concept,info in sorted(items.items()):
                if concepts is not None and concept not in concepts: continue
                for unit,observations in sorted(info['units'].items()):
                    if not isinstance(observations,list): raise ValueError('Invalid fact list')
                    for row in observations:
                        acc=accession(row['accn']);form=row['form'];filed=row['filed'];day(filed)
                        value=row.get('val')
                        if value is not None and (type(value) is not int and not isinstance(value,Decimal)): raise ValueError('Invalid fact number')
                        source=filings.get(acc) or self._source(issuer,acc,form,filed,None,payload.retrieved_at)
                        sources[acc]=source
                        start,end=day(row.get('start')),day(row.get('end'))
                        context={k:row[k] for k in ('fy','fp','form','filed','frame') if k in row}
                        context.update(taxonomy=taxonomy,concept=concept,label=info.get('label'),description=info.get('description'),accession=acc)
                        evidence.append(EvidenceItem(source.source_id,issuer.company_name,category(concept),
                            (info.get('label') or concept)+': '+('not supplied' if value is None else str(value))+' '+unit,
                            payload.retrieved_at,ticker=issuer.ticker,value=Decimal(value) if value is not None else None,unit=unit,
                            period_start=start,period_end=end,as_of=midnight(row.get('end')) if start is None else None,
                            source_locator=SourceLocator(xbrl_concept=taxonomy+':'+concept),metadata=context))
                        if len(evidence)>max_facts: raise ResearchError(ErrorCode.INVALID_REQUEST)
        if not evidence: raise ResearchError(ErrorCode.NO_DATA)
        return FinancialFacts(issuer,deduplicate(tuple(sources.values())),deduplicate(evidence),payload.retrieved_at)

    @boundary
    def build_evidence_pack(self,identifier,**options):
        result=self.get_financial_facts(identifier,**options).require()
        return EvidencePack(result.issuer.company_name,self.http.now(),sources=result.sources,evidence_items=result.evidence_items,
            ticker=result.issuer.ticker,market='US')
