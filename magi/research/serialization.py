"""Versioned, deterministic, lossless JSON. No eval, dynamic imports, or I/O."""
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from . import models as m
from .security import safe_text
from . import snapshot_models as sm

MODEL_TYPES = {cls.__name__:cls for cls in (m.ResearchSource,m.SourceLocator,m.EvidenceItem,
    m.ResearchClaim,m.EvidenceRelation,m.EvidencePack,sm.FinancialPeriod,sm.SnapshotIdentity,
    sm.MetricValue,sm.BaseMetric,sm.DerivedMetric,sm.ReportingContext,sm.CompanyResearchSnapshot)}
ENUM_TYPES = {cls.__name__:cls for cls in (m.SourceType,m.Authority,m.Category,m.ClaimStatus,m.RelationKind,m.WarningCode,sm.MetricStatus,sm.PeriodKind,sm.Calculation)}
ROOT_TYPES = (m.ResearchSource,m.EvidenceItem,m.ResearchClaim,m.EvidencePack,sm.CompanyResearchSnapshot,sm.BaseMetric,sm.DerivedMetric)


# Additive offline news types; existing research and snapshot contracts are unchanged.
from .news import models as nm
_NEWS_MODELS = (nm.NewsClassification,nm.NewsArticle,nm.ResearchEvent,
                nm.ResearchCatalyst,nm.EventCluster,nm.NewsEvidencePack)
MODEL_TYPES.update({cls.__name__:cls for cls in _NEWS_MODELS})
ENUM_TYPES.update({cls.__name__:cls for cls in (nm.EventType,nm.CatalystType,nm.Direction,
    nm.TimeHorizon,nm.VerificationStatus,nm.ContentKind,nm.Relevance,nm.Freshness,nm.Sentiment,nm.NewsWarning)})
ROOT_TYPES += _NEWS_MODELS

from .company_sources.models import OfficialCompanyItem, OfficialItemType
MODEL_TYPES['OfficialCompanyItem'] = OfficialCompanyItem
ENUM_TYPES['OfficialItemType'] = OfficialItemType
ROOT_TYPES += (OfficialCompanyItem,)

from .regulatory.models import (RegulatoryItem, RegulatoryEvent, RegulatoryRelationship,
                                RegulatoryBundle, RegulatoryType, RegulatoryStatus)
_REGULATORY_MODELS = (RegulatoryItem, RegulatoryEvent, RegulatoryRelationship, RegulatoryBundle)
MODEL_TYPES.update({cls.__name__:cls for cls in _REGULATORY_MODELS})
ENUM_TYPES.update({cls.__name__:cls for cls in (RegulatoryType, RegulatoryStatus)})
ROOT_TYPES += _REGULATORY_MODELS


# Additive Phase 7E.1 reference foundation; no selector or agent invocation.
from .balancing import models as bm
from .balancing.inputs import TemporalObservation
_BALANCING_MODELS = (bm.InstrumentIdentity, bm.EntityIdentity, bm.TargetIdentity,
    bm.SelectionRequest, bm.QualifiedReference, bm.InputSnapshot,
    bm.InputAvailability, bm.LineageReference, bm.EvidenceUniverse, TemporalObservation)
MODEL_TYPES.update({cls.__name__: cls for cls in _BALANCING_MODELS})
ENUM_TYPES.update({cls.__name__: cls for cls in (bm.InputFamily, bm.Availability, bm.LineageKind)})
ROOT_TYPES += _BALANCING_MODELS

from .balancing.grouping import GroupAnchor, AnalyticalGroup, GroupedEvidence
_GROUPING_MODELS = (GroupAnchor, AnalyticalGroup, GroupedEvidence)
MODEL_TYPES.update({cls.__name__: cls for cls in _GROUPING_MODELS})
ROOT_TYPES += _GROUPING_MODELS

from .balancing import assessment as am
_ASSESSMENT_MODELS = (am.TargetExposure, am.AssessmentPolicy, am.AssessmentBasis,
    am.ReferenceFitness, am.GroupAssessment, am.AssessmentSet)
MODEL_TYPES.update({cls.__name__: cls for cls in _ASSESSMENT_MODELS})
ENUM_TYPES.update({cls.__name__: cls for cls in (am.RelevanceLevel, am.AttentionLevel, am.TemporalFitness)})
ROOT_TYPES += _ASSESSMENT_MODELS


def encode(value):
    if isinstance(value,Enum): return {'$enum':type(value).__name__,'value':value.value}
    if isinstance(value,m.FrozenMetadata): return {'$map':[[k,encode(v)] for k,v in value.entries]}
    if is_dataclass(value) and type(value).__name__ in MODEL_TYPES:
        return {'$model':type(value).__name__,'fields':{f.name:encode(getattr(value,f.name)) for f in fields(value)}}
    if isinstance(value,datetime): return {'$datetime':value.isoformat()}
    if isinstance(value,date): return {'$date':value.isoformat()}
    if isinstance(value,Decimal):
        if not value.is_finite(): raise ValueError('Nonfinite Decimal')
        return {'$decimal':str(value)}
    if isinstance(value,tuple): return {'$tuple':[encode(v) for v in value]}
    if value is None or type(value) in (str,bool,int):
        if isinstance(value,str): safe_text(value)
        return value
    raise ValueError('Unsupported serialized value')


def decode(node):
    if node is None or type(node) in (str,bool,int):
        if isinstance(node,str): safe_text(node)
        return node
    if not isinstance(node,dict): raise ValueError('Invalid tagged value')
    keys=set(node)
    if keys=={'$decimal'}:
        if not isinstance(node['$decimal'],str): raise ValueError('Decimal requires text')
        result=Decimal(node['$decimal'])
        if not result.is_finite(): raise ValueError('Nonfinite Decimal')
        return result
    if keys=={'$datetime'}:
        result=datetime.fromisoformat(node['$datetime']);m.instant(result);return result
    if keys=={'$date'}: return date.fromisoformat(node['$date'])
    if keys=={'$enum','value'}: return ENUM_TYPES[node['$enum']](node['value'])
    if keys=={'$tuple'}:
        if not isinstance(node['$tuple'],list): raise ValueError('Invalid tuple')
        return tuple(decode(v) for v in node['$tuple'])
    if keys=={'$map'}:
        if not isinstance(node['$map'],list) or any(not isinstance(p,list) or len(p)!=2 for p in node['$map']): raise ValueError('Invalid metadata')
        return m.FrozenMetadata(tuple((k,decode(v)) for k,v in node['$map']))
    if keys=={'$model','fields'}:
        cls=MODEL_TYPES[node['$model']];raw=node['fields']
        # Phase 7A snapshots predate optional per-fact context metadata.
        if cls is m.EvidenceItem and isinstance(raw,dict) and 'metadata' not in raw:
            raw={**raw,'metadata':{'$map':[]}}
        if not isinstance(raw,dict) or set(raw)!={f.name for f in fields(cls)}: raise ValueError('Invalid model fields')
        values={k:decode(v) for k,v in raw.items()}
        result=cls(**{f.name:values[f.name] for f in fields(cls) if f.init})
        for f in fields(cls):
            if not f.init and values[f.name]!=getattr(result,f.name): raise ValueError('Forged derived field')
        return result
    raise ValueError('Unknown serialization tag')


def to_dict(value):
    if not isinstance(value,ROOT_TYPES): raise ValueError('Unsupported research root')
    return {'schema_version':m.SCHEMA_VERSION,'data':encode(value)}


def from_dict(value):
    try:
        if not isinstance(value,dict) or set(value)!={'schema_version','data'} or type(value['schema_version']) is not int or value['schema_version']!=m.SCHEMA_VERSION:
            raise ValueError('Unsupported research schema')
        result=decode(value['data'])
        if not isinstance(result,ROOT_TYPES): raise ValueError('Unsupported research root')
        return result
    except (KeyError,TypeError,InvalidOperation,OverflowError,RecursionError):
        raise ValueError('Malformed research serialization') from None


def dumps(value):
    return json.dumps(to_dict(value),sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def _pairs(pairs):
    result={}
    for k,v in pairs:
        if k in result: raise ValueError('Duplicate JSON key')
        result[k]=v
    return result


def loads(value):
    try:
        parsed=json.loads(value,object_pairs_hook=_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
        return from_dict(parsed)
    except (json.JSONDecodeError,RecursionError):
        raise ValueError('Malformed research JSON') from None
