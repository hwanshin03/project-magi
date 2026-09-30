"""Phase 7D.3a narrow compatibility, entirely synthetic/offline."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import httpx
from magi.research.regulatory.catalog import SOURCES, item_url
from magi.research.regulatory.parsing import parse
from magi.research.regulatory.service import build_regulatory_bundle
from magi.research.regulatory.transport import RegulatoryTransport

FIX=Path(__file__).parent/'fixtures/regulatory'
NOW=datetime(2026,9,30,tzinfo=timezone.utc)
class RegulatoryCompatibilityTests(unittest.TestCase):
    def test_exact_fsc_endpoint(self):
        endpoint='https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111'
        self.assertEqual(SOURCES['fsc_releases'].endpoint,endpoint)
        seen=[]
        def handle(request):
            seen.append(request);return httpx.Response(200,headers={'content-type':'application/rss+xml'},content=b'<rss/>')
        transport=RegulatoryTransport(transport=httpx.MockTransport(handle))
        try:transport.get('fsc_releases')
        finally:transport.close()
        self.assertEqual(len(seen),1);self.assertEqual(str(seen[0].url),endpoint);self.assertEqual(seen[0].method,'GET')
    def test_no_arbitrary_request_parameters(self):
        seen=[]
        transport=RegulatoryTransport(transport=httpx.MockTransport(lambda request:seen.append(request)))
        try:
            for endpoint in ['https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0112','https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111&extra=1','fsc_releases?fid=0112','fsc_releases?extra=1']:
                with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):transport.get(endpoint)
            with self.assertRaises(TypeError):transport.get('fsc_releases',fid='0112')
        finally:transport.close()
        self.assertEqual(seen,[])
    def test_standard_rss_conservative_pipeline(self):
        result=parse('fsc_releases',(FIX/'fsc_press_release.xml').read_bytes(),NOW)
        self.assertEqual(len(result),1);self.assertEqual(result.skipped_count,0)
        item=result[0];self.assertEqual(item.language,'ko-KR');self.assertIn('최종 확정',item.title)
        self.assertEqual(item.status.value,'UNKNOWN');self.assertEqual(item.regulatory_type.value,'PUBLIC_NOTICE')
        self.assertIsNone(item.effective_at);self.assertEqual(item.affected_entities,());self.assertEqual(item.affected_industries,())
        self.assertEqual(item.published_at.isoformat(),'2026-09-29T09:00:00+09:00')
        bundle=build_regulatory_bundle(result,created_at=NOW)
        self.assertEqual(bundle.events,());self.assertEqual(len(bundle.evidence_items),1)
    def test_fsc_canonical_paths_unchanged(self):
        for path in ['/no010101/99901','/po040301/99901']:
            url='https://www.fsc.go.kr'+path;self.assertEqual(item_url(url,'fsc_releases'),url)
        for path in ['/no010101/99901?fid=0111','/no010101/99901#x','/about/fsc_bbs_rss/?fid=0111','/no010101/%39','/no010101/../99901','/arbitrary/99901']:
            with self.subTest(path=path),self.assertRaises(ValueError):item_url('https://www.fsc.go.kr'+path,'fsc_releases')
    def row(self,number):
        return {'agencies':[{'slug':'industry-and-security-bureau'}],'document_number':number,'title':'Synthetic correction-style identifier',
            'html_url':'https://www.federalregister.gov/documents/2026/08/28/'+number+'/synthetic-record','publication_date':'2026-08-28','type':'Rule'}
    def test_standard_and_c1_identifiers(self):
        for number in ['2026-16628','C1-2026-16628']:
            with self.subTest(number=number):
                result=parse('bis_rules',json.dumps({'results':[self.row(number)]}).encode(),NOW)
                self.assertEqual(len(result),1);self.assertEqual(result[0].docket_or_reference,number)
                self.assertEqual(item_url(result[0].url,'bis_rules'),result[0].url)
                self.assertIsNone(result[0].effective_at);self.assertIsNone(result[0].legal_reference)
    def test_malformed_prefixes_rejected(self):
        for number in ['C2-2026-16628','C01-2026-16628','C-2026-16628','c1-2026-16628','X1-2026-16628','C1-C1-2026-16628','C1-2026-16628/extra','C1-２０２６-16628','C1-2026-123','C1-2026-1234567']:
            with self.subTest(number=number):
                result=parse('bis_rules',json.dumps({'results':[self.row(number)]}).encode(),NOW)
                self.assertEqual(len(result),0);self.assertEqual(result.skipped[0].code,'REGULATORY_INVALID_REFERENCE')
                with self.assertRaises(ValueError):item_url(self.row(number)['html_url'],'bis_rules')
    def test_c1_no_inferred_relationship(self):
        result=parse('bis_rules',json.dumps({'results':[self.row('2026-16628'),self.row('C1-2026-16628')]}).encode(),NOW)
        bundle=build_regulatory_bundle(result,created_at=NOW)
        self.assertEqual(len(bundle.items),2);self.assertEqual(bundle.relationships,())
        self.assertTrue(all('related_references' not in item.metadata for item in result))
    def test_unsafe_c1_urls(self):
        good=self.row('C1-2026-16628')['html_url']
        for url in [good.replace('https:','http:'),good+'?x=1',good+'#x',good.replace('www.federalregister.gov','user:pass@www.federalregister.gov'),good.replace('C1-','%431-'),good.replace('www.federalregister.gov','127.0.0.1')]:
            with self.subTest(url=url),self.assertRaises(ValueError):item_url(url,'bis_rules')
