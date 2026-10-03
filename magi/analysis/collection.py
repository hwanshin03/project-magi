"""Coordinate existing service APIs; no URL fetching, transport or metric logic."""
from magi.research.providers.base import ResearchError, Issuer
from magi.research.company_sources.catalog import CompanySourceError
from magi.research.regulatory.catalog import RegulatoryError
from magi.research.news.providers.marketaux import MarketauxError
from magi.research.news.base import NewsQuery
from magi.research.regulatory.base import RegulatoryQuery
from magi.research.snapshot import build_snapshot
from magi.research.snapshot_policy import SEC_CONCEPTS
from magi.research.models import EvidencePack
from .models import ResearchInput, AnalysisError


class ResearchServices:
    """Caller owns injected clients and their lifecycle. No implicit clients."""
    def __init__(self, *, sec=None, dart=None, news=None, company=None, regulatory=None):
        self.sec,self.dart,self.news,self.company,self.regulatory=sec,dart,news,company,regulatory

    def collect(self, channel, request, collected):
        try:
            if channel=='snapshot':
                packs=[c for entry in collected for c in entry.containers if type(c) is EvidencePack]
                if len(packs)!=1: return ResearchInput(channel,error_codes=('SNAPSHOT_INPUT_UNAVAILABLE',))
                try:
                    snapshot=build_snapshot(packs[0],report=request.report,year=request.year,
                                            currency=request.currency,division=request.division)
                except ValueError:
                    return ResearchInput(channel,error_codes=('SNAPSHOT_UNAVAILABLE',))
                return ResearchInput(channel,(snapshot,))
            service=getattr(self,channel)
            if service is None: return ResearchInput(channel,error_codes=('COLLECTOR_NOT_CONFIGURED',))
            if channel in ('sec','dart'):
                return self._filing(channel,service,request)
            instrument=request.target.instrument
            if channel in ('news','company'):
                if instrument is None: return ResearchInput(channel,error_codes=('INSTRUMENT_REQUIRED',))
                if channel=='company' and (instrument.market,instrument.symbol) not in (('US','NVDA'),('KR','005930')):
                    return ResearchInput(channel,error_codes=('UNSUPPORTED_TARGET',))
                query=NewsQuery(instrument.symbol,request.target.entity.display_name if request.target.entity and request.target.entity.display_name else instrument.symbol,
                    request.as_of,request.assessment_policy.published_since,request.limit)
                pack=service.collect(query)
                if pack.ticker != instrument.symbol: raise AnalysisError('COLLECTION_IDENTITY_MISMATCH')
                # Keep upstream skipped-item information as a limitation, never raw diagnostics.
                diagnostics=getattr(getattr(service,'provider',None),'last_diagnostics',())
                skipped=sum(len(v) for v in diagnostics.values()) if isinstance(diagnostics,dict) else len(diagnostics)
                return ResearchInput(channel,(pack,),omissions=('PROVIDER_SKIPPED_ITEMS',) if skipped else ())
            bundles=[]; errors=[]; omissions=[]
            for source in request.regulatory_sources:
                try:
                    bundles.append(service.collect(RegulatoryQuery(source,request.as_of,request.limit)))
                    if getattr(getattr(service,'provider',None),'last_diagnostics',()):
                        omissions.append('PROVIDER_SKIPPED_ITEMS')
                except RegulatoryError:
                    errors.append(source.upper()+'.PROVIDER_UNAVAILABLE')
            return ResearchInput(channel,tuple(bundles),tuple(errors),tuple(omissions))
        except ResearchError as error:
            return ResearchInput(channel,error_codes=(error.code.value,))
        except (MarketauxError,CompanySourceError,RegulatoryError):
            return ResearchInput(channel,error_codes=('PROVIDER_UNAVAILABLE',))

    @staticmethod
    def _filing(channel, provider, request):
        target=request.target; market='US' if channel=='sec' else 'KR'; namespace=channel.upper()
        if target.instrument and target.instrument.market!=market:
            return ResearchInput(channel,error_codes=('UNSUPPORTED_MARKET',))
        if target.entity and target.entity.namespace!=namespace:
            return ResearchInput(channel,error_codes=('UNSUPPORTED_ENTITY_NAMESPACE',))
        if channel=='dart' and request.year is None:
            return ResearchInput(channel,error_codes=('FINANCIAL_YEAR_REQUIRED',))
        identifier=target.instrument.symbol if target.instrument else target.entity.identifier
        issuer=provider.resolve_issuer(identifier).require()
        if type(issuer) is not Issuer or issuer.market!=market or issuer.provider!=namespace:
            raise AnalysisError('COLLECTION_IDENTITY_MISMATCH')
        if target.instrument and issuer.ticker!=target.instrument.symbol:
            raise AnalysisError('COLLECTION_IDENTITY_MISMATCH')
        if target.entity and issuer.provider_issuer_id!=target.entity.identifier:
            raise AnalysisError('COLLECTION_IDENTITY_MISMATCH')
        options={'concepts':SEC_CONCEPTS} if channel=='sec' else {'year':request.year,'report':request.report,'division':request.division}
        pack=provider.build_evidence_pack(identifier,**options).require()
        if type(pack) is not EvidencePack or pack.market!=market or (target.instrument and pack.ticker!=target.instrument.symbol):
            raise AnalysisError('COLLECTION_IDENTITY_MISMATCH')
        if any(s.metadata.get('issuer_id') not in (None,issuer.issuer_id) for s in pack.sources):
            raise AnalysisError('COLLECTION_IDENTITY_MISMATCH')
        return ResearchInput(channel,(pack,))
