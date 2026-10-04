"""Synthetic SEC periods and revisions; no transport or database operations."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import unittest
from test_balancing_foundation import NOW, pack
from magi.research.models import EvidencePack, EvidenceItem, ResearchSource, SourceType, Authority, Category
from magi.research.sec_projection import SECProjectionPolicy, SECAnalyticalProjection
from magi.research.snapshot_policy import SEC_CONCEPTS, SEC, BALANCE
from magi.research.snapshot import build_snapshot
from magi.research.serialization import dumps, loads


def catalog(size):
    """60 observations/year: annual, quarter and amended annual; 20 concepts each."""
    sources = {}; items = []
    balances = {c for key in BALANCE for c in SEC[key]}
    for i in range(size):
        year = 2025-i//60; slot = i%60; edition = slot//20
        concept = SEC_CONCEPTS[(slot%20)%len(SEC_CONCEPTS)]
        quarter = edition == 1
        start = date(year,7,1) if quarter else date(year,1,1)
        end = date(year,9,30) if quarter else date(year,12,31)
        form = ('10-K','10-Q','10-K/A')[edition]
        key = (year,edition)
        if key not in sources:
            accession = f'0001045810-{year%100:02d}-{edition+1:06d}'
            sources[key] = ResearchSource(SourceType.SEC_FILING,Authority.PRIMARY,'SEC','Synthetic financial filing',
                'Synthetic issuer',NOW,published_at=NOW.replace(year=year+1,month=3,day=edition+1),
                ticker='NVDA',market='US',company_name='Synthetic issuer',document_type=form,external_id=accession,
                url=f'https://www.sec.gov/Archives/synthetic/{year}/{edition}.htm',
                metadata={'issuer_id':'SEC:0001045810','cik':'0001045810','accession':accession,
                          'report_date':end.isoformat(),'amendment':edition==2,'publication_precision':'instant'})
        source = sources[key]
        items.append(EvidenceItem(source.source_id,'Synthetic issuer',Category.FINANCIAL,f'Synthetic observation {i}',NOW,
            ticker='NVDA',value=Decimal(i+1),unit='USD/shares' if concept.startswith('EarningsPerShare') else 'USD',
            period_start=None if concept in balances else start,period_end=end,
            metadata={'taxonomy':'us-gaap','concept':concept,'fy':year,'fp':'Q3' if quarter else 'FY','form':form,
                      'accession':source.external_id}))
    return EvidencePack('Synthetic issuer',NOW,tuple(sources.values()),tuple(items),ticker='NVDA',market='US')


class SECProjectionTests(unittest.TestCase):
    def projection(self, c=None, **kwargs):
        return SECAnalyticalProjection(c or catalog(180),SECProjectionPolicy(NOW,'Synthetic financial analysis',**kwargs))

    def test_default_history_and_catalog_preserved(self):
        c=catalog(2700);p=self.projection(c)
        self.assertIs(p.catalog,c);self.assertEqual(len(c.evidence_items),2700)
        self.assertLess(len(p.analytical_pack.evidence_items),100)
        self.assertEqual({e.period_end.year for e in p.analytical_pack.evidence_items},{2024,2025})
        self.assertEqual(len(p.audit),2700)
        self.assertEqual({k for k,_ in p.audit},{e.evidence_id for e in c.evidence_items})
        self.assertTrue(all(s in c.sources for s in p.analytical_pack.sources))

    def test_permutation_and_roundtrip(self):
        c=catalog(120);a=self.projection(c)
        b=self.projection(replace(c,sources=tuple(reversed(c.sources)),evidence_items=tuple(reversed(c.evidence_items)),pack_id=''))
        self.assertEqual(a,b);self.assertEqual(loads(dumps(a)),a)

    def test_required_calculations_match_full_catalog(self):
        c=catalog(180);p=self.projection(c)
        full=build_snapshot(c,currency='USD');bounded=build_snapshot(p.analytical_pack,currency='USD')
        for name in ('income_statement','growth','profitability','balance_sheet','cash_flow','per_share','reporting_context'):
            self.assertEqual(getattr(full,name),getattr(bounded,name))

    def test_quarter_separation(self):
        p=self.projection(report='quarter')
        self.assertTrue(p.analytical_pack.evidence_items)
        self.assertTrue(all(e.metadata['form']=='10-Q' for e in p.analytical_pack.evidence_items))
        self.assertEqual({e.period_end.year for e in p.analytical_pack.evidence_items},{2024,2025})

    def test_mixed_issuer_rejected(self):
        c=pack();e=replace(c.evidence_items[0],ticker='OTHER',evidence_id='')
        with self.assertRaises(ValueError):self.projection(replace(c,evidence_items=(e,),pack_id=''))

    def test_future_publication_and_period_excluded(self):
        c=pack()
        with self.assertRaises(ValueError):replace(c.sources[0],published_at=NOW+timedelta(days=1))
        s=replace(c.sources[0],published_at=None)
        p=self.projection(replace(c,sources=(s,),pack_id=''))
        self.assertEqual(p.audit[0][1],'PUBLICATION_UNAVAILABLE_AT_AS_OF')
        e=replace(c.evidence_items[0],period_end=date(2027,1,1),evidence_id='')
        p=self.projection(replace(c,evidence_items=(e,),pack_id=''))
        self.assertEqual(p.audit[0][1],'PERIOD_UNAVAILABLE_AT_AS_OF')

    def test_year_scope(self):
        p=self.projection(catalog(300),year=2023)
        self.assertEqual({e.period_end.year for e in p.analytical_pack.evidence_items},{2022,2023})

    def test_explicit_horizon_preserves_additional_periods(self):
        p=self.projection(catalog(300),published_since=NOW.replace(year=2023,month=1,day=1))
        self.assertIn(2021,{e.period_end.year for e in p.analytical_pack.evidence_items})

    def test_revision_and_conflict_provenance_not_collapsed(self):
        p=self.projection()
        facts=[e for e in p.analytical_pack.evidence_items if e.metadata['concept']=='Revenues' and e.period_end.year==2025]
        self.assertEqual(len(facts),2)
        self.assertEqual(len({e.source_id for e in facts}),2)
        self.assertEqual(len({e.value for e in facts}),2)
        self.assertTrue(any(s.metadata['amendment'] for s in p.analytical_pack.sources))

    def test_future_catalog_excluded(self):
        p=SECAnalyticalProjection(catalog(60),SECProjectionPolicy(NOW-timedelta(seconds=1),'Cutoff'))
        self.assertFalse(p.analytical_pack.evidence_items)
        self.assertEqual({reason for _,reason in p.audit},{'AFTER_AS_OF'})

    def test_same_date_publication_is_not_backdated(self):
        c=pack();s=replace(c.sources[0],published_at=NOW,metadata={**dict(c.sources[0].metadata),'publication_precision':'date; midnight UTC convention'})
        c=replace(c,sources=(s,),pack_id='');p=self.projection(c)
        self.assertEqual(p.audit[0][1],'PUBLICATION_UNAVAILABLE_AT_AS_OF')

    def test_concept_scope(self):
        p=self.projection(concepts=('Revenues',))
        self.assertEqual({e.metadata['concept'] for e in p.analytical_pack.evidence_items},{'Revenues'})
        self.assertIn('OUTSIDE_CONCEPT_SCOPE',{r for _,r in p.audit})

    def test_excluded_history_changes_projection_identity(self):
        a=self.projection(catalog(180));b=self.projection(catalog(240))
        self.assertEqual(a.analytical_pack,b.analytical_pack)
        self.assertNotEqual(a.catalog_fingerprint,b.catalog_fingerprint)
        self.assertNotEqual(a.projection_id,b.projection_id)

    def test_policy_version_and_scope_bound_identity(self):
        a=self.projection();b=SECAnalyticalProjection(a.catalog,replace(a.policy,scope='Other scope'))
        self.assertNotEqual(a.projection_id,b.projection_id)
        with self.assertRaises(ValueError):replace(a.policy,version='unknown')

    def test_no_hidden_record_cap(self):
        c=catalog(60);base=next(e for e in c.evidence_items if e.metadata['concept']=='Revenues' and e.metadata['form']=='10-K')
        items=tuple(replace(base,statement=f'Synthetic revision {i}',value=Decimal(i),evidence_id='') for i in range(150))
        c=replace(c,evidence_items=items,pack_id='');p=self.projection(c)
        self.assertEqual(len(p.analytical_pack.evidence_items),150)

    def test_orchestrator_retains_catalog_and_serializes_audit(self):
        from test_analysis import request, agents
        from magi.analysis.orchestrator import analyze
        from magi.analysis.models import ResearchInput, dumps as export, loads as restore
        c=catalog(180)
        result=analyze(request(requested=('sec','snapshot')),inputs=(ResearchInput('sec',(c,)),),agents=agents())
        self.assertEqual(result.sec_projections[0].catalog,c)
        self.assertNotIn(c,tuple(s.container for s in result.universe.inputs))
        self.assertEqual(restore(export(result)),result)

if __name__=='__main__':unittest.main()
