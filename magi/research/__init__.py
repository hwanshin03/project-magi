"""Offline research foundation; no provider clients, agent hooks, or persistence."""
from .models import (SourceType, Authority, Category, ClaimStatus, RelationKind, WarningCode,
                     ResearchSource, SourceLocator, EvidenceItem, ResearchClaim, EvidenceRelation,
                     EvidencePack, FrozenMetadata, SCHEMA_VERSION)
from .serialization import dumps, loads, to_dict, from_dict
from .selection import SelectionPolicy, select, deduplicate, AGENT_POLICIES
from .context import render_context, ResearchContext

from .snapshot_models import (CompanyResearchSnapshot, BaseMetric, DerivedMetric, MetricValue,
                              MetricStatus, FinancialPeriod, PeriodKind, Calculation)
from .snapshot import build_snapshot
from .snapshot_presentation import render_snapshot, render_snapshot_context
