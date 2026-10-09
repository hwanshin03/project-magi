"""Historical/publication boundaries with synthetic late retrieval; offline only."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import permutations
import unittest
from unittest.mock import patch
from test_balancing_foundation import NOW, pack, regulatory
from test_balancing_assessment import assess, target, financial
from test_balancing_selection import selected, contested, reports
from test_balancing_grouping import grouped
from test_sec_projection import catalog
from magi.research.temporal import publication_time, availability, available
from magi.research.sec_projection import SECAnalyticalProjection, SECProjectionPolicy
from magi.research.balancing.assessment import AssessmentPolicy, TemporalFitness as T, AttentionLevel
from magi.research.balancing.inputs import temporal_observations, resolve
from magi.research.balancing.selection import render_view
from magi.research.models import SourceType
from magi.research.snapshot import build_snapshot
from magi.research.serialization import dumps, loads, to_dict, from_dict
from magi.research.news.service import build_news_pack
from magi.research.regulatory.service import build_regulatory_bundle

LATER = NOW+timedelta(seconds=5)


def collected(c, stamp=LATER):
    return replace(c,created_at=stamp,sources=tuple(replace(s,retrieved_at=stamp) for s in c.sources),
                   evidence_items=tuple(replace(e,retrieved_at=stamp) for e in c.evidence_items),pack_id='')


def projection(c, boundary=NOW, **kwargs):
    return SECAnalyticalProjection(c,SECProjectionPolicy(boundary,'Temporal fixture',**kwargs))


class TemporalEligibilityTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'httpx.HTTPTransport.handle_request','sqlite3.connect'):
            guard=patch(name,side_effect=AssertionError('Offline only'));guard.start();self.addCleanup(guard.stop)

    def test_historical_publication_not_later_retrieval_controls(self):
        cutoff=datetime(2025,6,1,tzinfo=timezone.utc)
        retrieved=datetime(2026,10,9,tzinfo=timezone.utc)
        for month,fitness in ((5,T.AS_OF_COMPATIBLE),(7,T.FUTURE_RELATIVE_TO_AS_OF)):
            with self.subTest(month=month):
                c=collected(pack(),retrieved)
                c=replace(c,sources=(replace(c.sources[0],published_at=cutoff.replace(month=month)),),pack_id='')
                req=replace(target(),as_of=cutoff,request_id='')
                self.assertEqual(assess(c,req=req).assessments[0].temporal,fitness)

    def test_current_run_does_not_move_cutoff(self):
        c=collected(pack());c=replace(c,sources=(replace(c.sources[0],published_at=NOW-timedelta(days=30)),),pack_id='')
        s=selected(c)
        self.assertTrue(s.common_core);self.assertEqual(s.assessment.grouped.universe.request.as_of,NOW)
        self.assertIn(c.evidence_items[0].statement,render_view(s))

    def test_exact_timestamp_boundary_inclusive(self):
        for seconds,want in ((-1,True),(0,True),(1,False)):
            with self.subTest(seconds=seconds):
                s=replace(pack().sources[0],published_at=NOW+timedelta(seconds=seconds),retrieved_at=LATER)
                self.assertEqual(available(s,NOW),want)

    def test_date_only_same_day_not_resolved_by_early_retrieval(self):
        item=regulatory().items[0]
        self.assertEqual(availability(item,NOW),('published_at','SAME_DATE_UNORDERED'))
        self.assertFalse(available(item,NOW));self.assertIs(type(publication_time(item)),date)

    def test_date_only_before_and_after_local_boundary(self):
        cutoff=datetime(2025,6,1,10,tzinfo=timezone(timedelta(hours=-7)))
        for day,want in ((date(2025,5,31),True),(date(2025,6,1),False),(date(2025,6,2),False)):
            with self.subTest(day=day):
                item=replace(regulatory().items[0],published_at=day,retrieved_at=LATER,item_id='')
                self.assertEqual(available(item,cutoff),want)

    def test_legacy_sec_and_dart_midnight_conventions(self):
        for precision,zone,kind in [('date; midnight UTC convention',timezone.utc,SourceType.SEC_FILING),
                ('date; midnight KST convention',timezone(timedelta(hours=9)),SourceType.DART_FILING)]:
            for days,want in ((-1,True),(0,False),(1,False)):
                with self.subTest(precision=precision,days=days):
                    pub=(NOW+timedelta(days=days)).replace(hour=0,tzinfo=zone)
                    c=collected(pack(),NOW+timedelta(days=2))
                    source=replace(c.sources[0],source_type=kind,published_at=pub,
                        metadata={**c.sources[0].metadata,'publication_precision':precision})
                    c=replace(c,sources=(source,),pack_id='')
                    self.assertEqual(bool(selected(c).common_core),want)
                    self.assertIs(type(publication_time(source)),date)
                    self.assertEqual(source.published_at,pub)

    def test_missing_publication_retrieval_fallback_is_conservative(self):
        for stamp,want in ((NOW-timedelta(days=1),True),(NOW,True),(LATER,False)):
            with self.subTest(stamp=stamp):
                c=collected(pack(),max(stamp,NOW))
                c=replace(c,sources=(replace(c.sources[0],published_at=None,retrieved_at=stamp),),pack_id='')
                a=assess(c).assessments[0]
                self.assertEqual(a.temporal==T.AS_OF_COMPATIBLE,want)
                self.assertIn('RETRIEVAL_TIME_FALLBACK',a.uncertainties)

    def test_retrieval_cannot_establish_publication_horizon(self):
        c=pack();c=replace(c,sources=(replace(c.sources[0],published_at=None),),pack_id='')
        a=assess(c,policy=AssessmentPolicy(published_since=NOW-timedelta(days=5))).assessments[0]
        self.assertEqual(a.temporal,T.UNKNOWN)

    def test_temporal_audit_retains_later_retrieval(self):
        c=collected(pack());u=grouped(c).universe
        r=next(r for r in u.references if r.object_kind=='ResearchSource')
        observations={o.field:o for o in temporal_observations(u,r)}
        self.assertEqual(observations['retrieved_at'].relation_to_as_of,'AFTER')
        self.assertEqual(observations['retrieved_at'].value,LATER)
        self.assertEqual(assess(c).assessments[0].temporal,T.AS_OF_COMPATIBLE)
        self.assertEqual(loads(dumps(c)),c)

    def test_current_large_sec_projection_nonempty_bounded(self):
        c=collected(catalog(2700));p=projection(c)
        self.assertIs(p.catalog,c);self.assertEqual(len(p.catalog.evidence_items),2700)
        self.assertEqual(len(p.analytical_pack.evidence_items),64)
        self.assertEqual({e.period_end.year for e in p.analytical_pack.evidence_items},{2024,2025})
        self.assertEqual(len(p.audit),2700)
        self.assertTrue(all(e.retrieved_at==LATER for e in p.analytical_pack.evidence_items))

    def test_historical_projection_partial_and_empty(self):
        c=collected(catalog(180))
        partial=projection(c,datetime(2025,6,1,tzinfo=timezone.utc))
        self.assertEqual({e.period_end.year for e in partial.analytical_pack.evidence_items},{2023,2024})
        empty=projection(c,datetime(2023,1,1,tzinfo=timezone.utc))
        self.assertFalse(empty.analytical_pack.evidence_items)
        self.assertEqual({r for _,r in empty.audit},{'AFTER_AS_OF'})

    def test_future_required_comparable_period_never_selected(self):
        c=collected(catalog(120),NOW+timedelta(days=2))
        future={s.source_id for s in c.sources if s.metadata['report_date'].startswith('2024')}
        c=replace(c,sources=tuple(replace(s,published_at=NOW+timedelta(days=1)) if s.source_id in future else s for s in c.sources),pack_id='')
        p=projection(c)
        self.assertTrue(p.analytical_pack.evidence_items)
        self.assertFalse(any(e.source_id in future for e in p.analytical_pack.evidence_items))
        self.assertTrue(all(dict(p.audit)[e.evidence_id]=='AFTER_AS_OF' for e in c.evidence_items if e.source_id in future))

    def test_past_revisions_preserved_future_revision_excluded(self):
        c=collected(catalog(180),NOW+timedelta(days=2))
        future=next(s for s in c.sources if s.document_type=='10-K/A' and s.metadata['report_date'].startswith('2025'))
        c=replace(c,sources=tuple(replace(s,published_at=NOW+timedelta(days=1)) if s==future else s for s in c.sources),pack_id='')
        p=projection(c)
        self.assertNotIn(future.source_id,{s.source_id for s in p.analytical_pack.sources})
        self.assertTrue(any(s.document_type=='10-K/A' for s in p.analytical_pack.sources))
        self.assertTrue(any(s.document_type=='10-K' for s in p.analytical_pack.sources))

    def test_annual_quarter_policy_and_phase7c_unchanged(self):
        c=collected(catalog(180))
        for report,forms in (('annual',{'10-K','10-K/A'}),('quarter',{'10-Q'})):
            with self.subTest(report=report):
                p=projection(c,report=report)
                self.assertEqual({e.metadata['form'] for e in p.analytical_pack.evidence_items},forms)
                full=build_snapshot(c,report=report,currency='USD')
                bounded=build_snapshot(p.analytical_pack,report=report,currency='USD')
                for name in ('income_statement','growth','profitability','balance_sheet','cash_flow','per_share','reporting_context'):
                    self.assertEqual(getattr(full,name),getattr(bounded,name))

    def test_news_late_retrieval_and_future_publication(self):
        a=reports().articles[0]
        a=replace(a,published_at=NOW-timedelta(days=1),retrieved_at=LATER,article_id='')
        n=build_news_pack([a],ticker='NVDA',subject='Synthetic',created_at=LATER)
        s=selected(n);self.assertTrue(s.common_core)
        future=replace(a,published_at=NOW+timedelta(seconds=1),article_id='')
        n=build_news_pack([future],ticker='NVDA',subject='Synthetic',created_at=LATER)
        self.assertFalse(selected(n).common_core)

    def test_official_nvidia_late_retrieval(self):
        from pathlib import Path
        from magi.research.company_sources.nvidia import NvidiaProvider
        from magi.research.company_sources.service import build_company_pack
        items=NvidiaProvider().parse('nvidia_newsroom',(Path(__file__).parent/'fixtures/company_sources/nvidia.xml').read_bytes(),retrieved_at=LATER)
        n=build_company_pack(items,ticker='NVDA',subject='NVIDIA',created_at=LATER)
        s=selected(n)
        self.assertTrue(s.common_core)
        self.assertTrue(all(resolve(s.assessment.grouped.universe,r).retrieved_at==LATER for g in s.common_core for r in g.citations))

    def test_fsc_date_only_and_future_effective_date(self):
        item=replace(regulatory().items[0],published_at=NOW.date()-timedelta(days=1),retrieved_at=LATER,item_id='')
        b=build_regulatory_bundle([item],created_at=LATER)
        s=selected(b);self.assertTrue(s.common_core)
        self.assertEqual(loads(dumps(b)).items[0].published_at,item.published_at)
        self.assertGreater(item.effective_at,NOW.date())
        self.assertEqual(b.sources[0].published_at,None)

    def test_future_explicit_measurement_boundary_not_bypassed(self):
        c=collected(pack());e=replace(c.evidence_items[0],as_of=LATER,evidence_id='')
        c=replace(c,evidence_items=(e,),pack_id='')
        self.assertFalse(projection(c).analytical_pack.evidence_items)
        self.assertFalse(any(r.object_id==e.evidence_id for g in selected(c).common_core for r in g.units))

    def test_later_authored_claim_not_backdated(self):
        c=contested(relation=False)
        c=replace(c,created_at=LATER,claims=tuple(replace(x,created_at=LATER,claim_id='') for x in c.claims),pack_id='')
        s=selected(c)
        self.assertTrue(s.common_core)
        self.assertFalse(any(g.conflicts for g in s.common_core))

    def test_derived_metric_inherits_all_source_availability(self):
        c=collected(financial());s=build_snapshot(c)
        policy=AssessmentPolicy(requested_metrics=('revenue_yoy',))
        self.assertEqual(assess(c,s,policy=policy).assessments[0].attention,AttentionLevel.ELEVATED)
        c=replace(c,sources=tuple(replace(x,published_at=NOW+timedelta(seconds=1)) for x in c.sources),pack_id='')
        self.assertEqual(assess(c,build_snapshot(c),policy=policy).assessments[0].attention,AttentionLevel.UNASSESSED)

    def test_grouping_unchanged_by_temporal_assessment(self):
        c=collected(pack());g=grouped(c,req=target())
        before=dumps(g);a=assess(c)
        self.assertEqual(dumps(g),before);self.assertEqual(a.grouped,g)
        early=assess(pack()).assessments[0];late=a.assessments[0]
        self.assertEqual((early.relevance,early.attention,early.temporal),(late.relevance,late.attention,late.temporal))
        self.assertEqual([(g.notice,len(g.units)) for g in selected(pack()).common_core],[(g.notice,len(g.units)) for g in selected(c).common_core])

    def test_permutation_and_roundtrip(self):
        c=collected(catalog(120));p=projection(c)
        self.assertEqual(p,projection(replace(c,sources=tuple(reversed(c.sources)),evidence_items=tuple(reversed(c.evidence_items)),pack_id='')))
        self.assertEqual(loads(dumps(p)),p)
        containers=(collected(pack()),regulatory())
        for order in permutations(containers): self.assertEqual(selected(*order),selected(*containers))
        self.assertEqual(loads(dumps(selected(collected(pack())))),selected(collected(pack())))

    def test_temporal_policy_is_identity_bound_and_tamper_rejected(self):
        for policy in (AssessmentPolicy(),SECProjectionPolicy(NOW,'fixture')):
            self.assertEqual(policy.temporal_version,'public-availability-v1')
            with self.assertRaises(ValueError): replace(policy,temporal_version='future')
        raw=to_dict(projection(collected(pack())))
        raw['data']['fields']['catalog']['fields']['created_at']={'$datetime':(LATER+timedelta(days=1)).isoformat()}
        with self.assertRaises(ValueError): from_dict(raw)
        raw=to_dict(selected(collected(pack())))
        raw['data']['fields']['assessment']['fields']['policy']['fields']['temporal_version']='forged'
        with self.assertRaises(ValueError): from_dict(raw)

    def test_naive_publication_rejected(self):
        with self.assertRaises(ValueError): replace(pack().sources[0],published_at=datetime(2025,5,1))

    def test_explicit_boundary_no_clock(self):
        c=collected(pack())
        with patch('time.time',side_effect=AssertionError('No wall clock')):
            self.assertEqual(projection(c),projection(c))
            self.assertEqual(selected(c),selected(c))

    def test_full_offline_analysis_with_late_collected_catalog(self):
        from test_analysis import request,agents
        from magi.analysis.models import ResearchInput,dumps as export,loads as restore
        from magi.analysis.orchestrator import analyze
        c=collected(catalog(120));req=request(requested=('sec','snapshot'))
        result=analyze(req,inputs=(ResearchInput('sec',(c,)),),agents=agents())
        self.assertTrue(result.selection.common_core)
        self.assertEqual(result.request.as_of,NOW)
        self.assertEqual(result.sec_projections[0].catalog,c)
        self.assertEqual(restore(export(result)),result)
