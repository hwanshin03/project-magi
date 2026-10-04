"""Sequential in-memory coordination. Existing layers own every analytical rule."""
from magi.research.validation import operation
from dataclasses import replace
from magi.research.balancing.inputs import build_universe
from magi.research.balancing.models import Availability
from magi.research.balancing.grouping import GroupedEvidence
from magi.research.balancing.assessment import AssessmentSet
from magi.research.balancing.selection import EvidenceSelection
from magi.decision import AgentResult
from magi.voting import VotingEngine
from magi.provider import ProviderUnavailable
from .models import AnalysisRequest, ResearchInput, AnalysisResult, AnalysisError, CHANNELS
from .collection import ResearchServices


@operation
def analyze(request, *, inputs=(), services=None, agents, explainer=None):
    """No default live agents/services. Optional failures are data; corruption stops."""
    try:
        if type(request) is not AnalysisRequest or replace(request)!=request:
            raise ValueError('Invalid request')
        agents=tuple(agents)
        if len(agents)!=3 or {a.name for a in agents}!=set(VotingEngine.AGENTS):
            raise ValueError('Invalid agent identities')
        by_name={a.name:a for a in agents}
        supplied={}
        for entry in inputs:
            if type(entry) is not ResearchInput or replace(entry)!=entry or entry.channel not in request.requested:
                raise ValueError('Invalid supplied research')
            if entry.channel in supplied and supplied[entry.channel]!=entry: raise ValueError('Conflicting supplied research')
            supplied[entry.channel]=entry
        services=services if services is not None else ResearchServices()
        # Snapshot depends on filings; other channels use a fixed operational order,
        # never provider completion time or an analytical ranking.
        projections = []
        for channel in sorted(request.requested,key=lambda k:(k=='snapshot',k)):
            if channel not in supplied:
                entry=services.collect(channel,request,tuple(supplied[k] for k in sorted(supplied)))
                if type(entry) is not ResearchInput or entry.channel!=channel: raise ValueError('Invalid collector result')
                supplied[channel]=replace(entry)
            if channel == 'sec':
                from magi.research.sec_projection import SECProjectionPolicy, SECAnalyticalProjection
                policy = SECProjectionPolicy(request.as_of, request.selection_request.scope,
                    request.report, request.year, published_since=request.assessment_policy.published_since,
                    currency=request.currency)
                # Historical callers can supply arbitrary research graphs in
                # this family. Project only normalized financial catalogs;
                # existing claim/relation graphs keep their original semantics.
                containers = []
                for c in supplied[channel].containers:
                    if (not c.claims and not c.relations and c.evidence_items
                            and all(e.metadata.get('taxonomy') and e.metadata.get('concept') for e in c.evidence_items)):
                        projection = SECAnalyticalProjection(c,policy)
                        projections.append(projection)
                        containers.append(projection.analytical_pack)
                    else: containers.append(c)
                supplied[channel] = replace(supplied[channel],containers=tuple(containers))
        for channel in request.required:
            if not supplied[channel].complete: raise AnalysisError('REQUIRED_RESEARCH_UNAVAILABLE')
        containers=tuple(c for k in sorted(supplied) for c in supplied[k].containers)
        universe=build_universe(request.selection_request,containers)
        manifest=[]
        for row in universe.availability:
            entries=[e for e in supplied.values() if CHANNELS[e.channel]==row.family]
            errors=tuple(sorted({e.channel.upper()+'.'+code for e in entries for code in e.error_codes}))
            omissions=tuple(sorted(set(row.omissions)|{e.channel.upper()+'.'+code for e in entries for code in e.omissions}))
            if errors or omissions:
                state=Availability.PARTIAL if row.snapshot_ids else Availability.UNAVAILABLE
                row=replace(row,state=state,error_codes=errors,omissions=omissions)
            manifest.append(row)
        universe=replace(universe,availability=tuple(manifest),universe_id='')
        grouped=GroupedEvidence(universe)
        assessed=AssessmentSet(grouped,request.assessment_policy)
        selection=EvidenceSelection(assessed,request.selection_policy)
    except AnalysisError: raise
    except (ValueError,TypeError,AttributeError,KeyError):
        raise AnalysisError('INVALID_ANALYSIS_INPUT') from None
    except Exception:
        raise AnalysisError('ORCHESTRATION_FAILED') from None
    responses=[]
    for name in VotingEngine.AGENTS:
        agent=by_name[name]
        sentinel=object(); previous=getattr(agent,'historical_context',sentinel)
        try:
            agent.historical_context=request.history
            result=agent.think(request.question,research_selection=selection)
            if type(result) is not AgentResult or result.agent!=name or replace(result)!=result:
                raise AnalysisError('INVALID_AGENT_RESULT')
            responses.append(result)
        except AnalysisError: raise
        except Exception:
            # Provider errors should already be structured by the existing agent.
            # Unexpected failures are not fabricated votes or HOLD results.
            raise AnalysisError('AGENT_EXECUTION_FAILED') from None
        finally:
            if previous is sentinel: del agent.historical_context
            else: agent.historical_context=previous
    try:
        result=AnalysisResult(request,selection,tuple(responses),sec_projections=tuple(projections))
        if explainer is not None:
            try: explanation=explainer.explain(request.question,result.vote)
            except Exception: explanation=ProviderUnavailable('Consensus',0,'explanation_failure')
            if isinstance(explanation,ProviderUnavailable):
                result=replace(result,explanation_error='EXPLANATION_UNAVAILABLE',artifact_id='')
            elif isinstance(explanation,str):
                result=replace(result,explanation=explanation,artifact_id='')
            else: raise AnalysisError('INVALID_EXPLANATION_RESULT')
        return result
    except AnalysisError: raise
    except (ValueError,TypeError): raise AnalysisError('INVALID_ANALYSIS_RESULT') from None
