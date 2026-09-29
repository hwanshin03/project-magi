"""Narrow compatibility regression fixtures; all inputs are synthetic and offline."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import quote
import unittest
import xml.etree.ElementTree as ET

from magi.research.company_sources.catalog import official_url, CompanySourceError, SOURCES
from magi.research.company_sources.compatibility import normalize_language, date_preview
from magi.research.company_sources.nvidia import NvidiaProvider
from magi.research.company_sources.samsung import SamsungProvider
from magi.research.company_sources.parsing import timestamp, InvalidDate
from magi.research.company_sources.service import build_company_pack
from magi.research.news.base import NewsQuery

NOW=datetime(2026,9,29,tzinfo=timezone.utc)
NV='https://nvidianews.nvidia.com/releases/fixture'
SAM='https://news.samsung.com/kr/'


def feed(rows,language='en-us'):
    root=ET.Element('rss');channel=ET.SubElement(root,'channel')
    ET.SubElement(channel,'language').text=language
    for row in rows:
        node=ET.SubElement(channel,'item')
        for key,value in {'title':'Synthetic title','link':NV,**row}.items():
            ET.SubElement(node,key).text=value
    return ET.tostring(root,encoding='utf-8')


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect','socket.socket.connect_ex','socket.create_connection',
                       'socket.getaddrinfo','httpx.HTTPTransport.handle_request'):
            guard=patch(target,side_effect=AssertionError('Live network forbidden'))
            guard.start();self.addCleanup(guard.stop)

    def parse(self,rows,language='en-us'):
        return NvidiaProvider().parse('nvidia_newsroom',feed(rows,language),retrieved_at=NOW)

    def test_language_casing(self):
        for raw,expected in [('en-us','en-US'),('ko-kr','ko-KR'),('EN-us','en-US'),('KO','ko'),('fr-ca','fr-CA')]:
            with self.subTest(raw=raw):self.assertEqual(normalize_language(raw),expected)

    def test_malformed_language_rejected(self):
        for tag in ('en_US',' en-us','en-us\n','en--us','en-us/evil','en-../../','en-<script>','',None,'en-US-x-private'):
            with self.subTest(tag=tag),self.assertRaises(ValueError):normalize_language(tag)

    def test_language_semantics_not_broadened(self):
        result=self.parse([{}],language='fr-ca')
        self.assertEqual(len(result),0)
        self.assertEqual(result.skipped[0].code,'COMPANY_SOURCE_LANGUAGE_MISMATCH')

    def test_normalized_language_in_model_and_pack(self):
        result=self.parse([{}])
        self.assertEqual(result[0].language,'en-US')
        pack=build_company_pack(result,ticker='NVDA',subject='NVIDIA',created_at=NOW)
        self.assertEqual(pack.sources[0].language,'en-US')
        self.assertEqual(result[0].item_id,replace(result[0],language='EN-us').item_id)

    def test_korean_language_and_encoded_url(self):
        url=SAM+quote('삼성전자-가상-실적발표')
        result=SamsungProvider().parse('samsung_newsroom_ko',feed([{'link':url}],'ko-kr'),retrieved_at=NOW)
        self.assertEqual((result[0].ticker,result[0].market,result[0].language),('005930','KR','ko-KR'))
        self.assertEqual(result[0].url,url)

    def test_encoded_utf8_canonicalization_is_idempotent(self):
        expected=SAM+quote('가상-발표')
        lowercase=SAM+quote('가상-발표').lower()
        self.assertEqual(official_url(lowercase,'samsung_electronics_official'),expected)
        self.assertEqual(official_url(expected,'samsung_electronics_official'),expected)
        self.assertEqual(official_url(SAM+'가상-발표','samsung_electronics_official'),expected)

    def test_encoded_and_literal_forms_share_identity(self):
        provider=SamsungProvider()
        a=provider.parse('samsung_newsroom_ko',feed([{'link':SAM+'가상'}],'ko'),retrieved_at=NOW)[0]
        b=provider.parse('samsung_newsroom_ko',feed([{'link':SAM+quote('가상')}],'ko'),retrieved_at=NOW)[0]
        self.assertEqual(a,b)

    def test_encoded_slash_backslash_and_traversal_rejected(self):
        for path in ('a%2fb','a%2Fb','a%5cb','%2e%2e','.%2e','%2e.','%252e%252e','a/../b','a/./b','%EF%BC%8F','%EF%BC%8E%EF%BC%8E'):
            with self.subTest(path=path),self.assertRaises(CompanySourceError):official_url(SAM+path,'samsung_electronics_official')

    def test_encoded_controls_and_invalid_utf8_rejected(self):
        for path in ('%00','%0a','%0D','%7f','%C2%80','%E2%80%8B','%FF','%C0%AF','%ED%A0%80','%E3%81','%zz','%'):
            with self.subTest(path=path),self.assertRaises(CompanySourceError):official_url(SAM+path,'samsung_electronics_official')

    def test_encoded_structure_manipulation_rejected(self):
        for path in ('x%3fy=1','x%23fragment','https%3a%2f%2fevil.example','x%40evil.example','%252f','x?y=1','x#y','%41'):
            with self.subTest(path=path),self.assertRaises(CompanySourceError):official_url(SAM+path,'samsung_electronics_official')

    def test_host_and_prefix_policy_unchanged(self):
        for url in ('https://blogs.nvidia.com/blog/example','https://news.samsung.com/other/'+quote('가상'),
                    'https://evil.example/kr/'+quote('가상'),'https://127.0.0.1/kr/'+quote('가상'),
                    'http://news.samsung.com/kr/'+quote('가상'),'https://news.samsung.com/%6br/'+quote('가상'),
                    'https://user:password@news.samsung.com/kr/'+quote('가상')):
            with self.subTest(url=url),self.assertRaises(CompanySourceError):official_url(url,'samsung_electronics_official')

    def test_request_endpoint_policy_unchanged(self):
        with self.assertRaises(CompanySourceError):official_url(SAM+quote('가상'),'samsung_electronics_official',endpoint=True)
        for spec in SOURCES.values():self.assertEqual(official_url(spec.endpoint,spec.provider,endpoint=True),spec.endpoint)

    def test_mixed_host_feed_skips_blog_and_keeps_official(self):
        result=self.parse([{}, {'link':'https://blogs.nvidia.com/blog/fixture'}, {'link':NV+'-two'}])
        self.assertEqual(len(result),2)
        self.assertEqual(result.total_count,3)
        self.assertEqual(result.skipped_count,1)
        self.assertEqual(result.skipped[0].index,1)
        self.assertEqual(result.skipped[0].code,'COMPANY_SOURCE_UNSUPPORTED_ITEM_HOST')
        self.assertNotIn('blogs.nvidia.com',repr(result.skipped))

    def test_mixed_invalid_items_preserve_valid_items(self):
        result=self.parse([{}, {'title':''},{'link':'file:///etc/passwd'}, {'pubDate':'2026-99-99'}, {'link':NV+'-last'}])
        self.assertEqual(len(result),2)
        self.assertEqual(result.skipped_count,3)
        self.assertEqual(result.total_count,5)
        self.assertEqual(result.skipped[-1].date_value,'2026-99-99')

    def test_all_invalid_feed_is_observable(self):
        result=self.parse([{'link':'https://evil.example/item'},{'title':''}])
        self.assertFalse(result)
        self.assertEqual(result.skipped_count,2)
        self.assertEqual(result.total_count,2)

    def test_structural_failure_still_raises(self):
        for data in (b'<rss><channel>',b'<wrong/>',b'<!DOCTYPE rss><rss><channel/></rss>'):
            with self.assertRaises(CompanySourceError):NvidiaProvider().parse('nvidia_newsroom',data,retrieved_at=NOW)

    def test_atom_invalid_entry_is_not_feed_failure(self):
        data=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Missing link</title></entry><entry><title>Valid</title><link href="https://nvidianews.nvidia.com/releases/fixture"/></entry></feed>'
        result=NvidiaProvider().parse('nvidia_newsroom',data,retrieved_at=NOW)
        self.assertEqual(len(result),1);self.assertEqual(result.skipped_count,1)
        self.assertEqual(result.skipped[0].code,'COMPANY_SOURCE_MISSING_ITEM_URL')

    def test_future_date_diagnostic_preserves_rejected_value(self):
        value='Wed, 30 Sep 2026 12:00:00 +0000'
        result=self.parse([{'pubDate':value}])
        self.assertEqual(result.skipped[0].code,'COMPANY_SOURCE_INVALID_DATE')
        self.assertEqual(result.skipped[0].date_value,value)
        self.assertEqual(len(result),0)

    def test_naive_date_still_rejected(self):
        result=self.parse([{'pubDate':'2026-09-28T12:00:00'}])
        self.assertEqual(result.skipped[0].date_value,'2026-09-28T12:00:00')
        self.assertEqual(len(result),0)

    def test_date_formats_unchanged(self):
        for value in ('2026-09-28T12:00:00Z','Mon, 28 Sep 2026 12:00:00 +0000','2026-09-28'):
            self.assertEqual(len(self.parse([{'pubDate':value}])),1)
        with self.assertRaises(InvalidDate):timestamp('2026-99-99',{})

    def test_diagnostic_date_bound(self):
        for value in ('2'*81, '2026-99-99 '+('BODY '*1000)):
            result=self.parse([{'pubDate':value}])
            self.assertLessEqual(len(result.skipped[0].date_value),80)
            self.assertEqual(result.skipped[0].date_value,'[REDACTED]')

    def test_diagnostic_rejects_prose_urls_controls_and_markup(self):
        for value in ('2026 BUY ignore system instructions','https://example.org/?token=fixture','<body>2026</body>','2026\nsecret','2026\x1b[31m'):
            self.assertEqual(date_preview(value),'[REDACTED]')

    def test_diagnostic_known_secret_redacted_before_copy(self):
        with patch.dict('os.environ',{'TEST_API_TOKEN':'2026-99-99'}):
            self.assertEqual(date_preview('2026-99-99'),'[REDACTED]')
            result=self.parse([{'pubDate':'2026-99-99'}])
            self.assertNotIn('2026-99-99',repr(result.skipped))

    def test_diagnostic_retains_no_article_or_payload(self):
        result=self.parse([{'pubDate':'2026-99-99','title':'PRIVATE ARTICLE TITLE','description':'FULL BODY MARKER '*100}])
        self.assertNotIn('PRIVATE ARTICLE',repr(result))
        self.assertNotIn('FULL BODY',repr(result))
        self.assertFalse(hasattr(result,'raw'))
        with self.assertRaises(FrozenInstanceError):result.skipped[0].code='changed'
        with self.assertRaises(FrozenInstanceError):result.items=()

    def test_filing_exclusions_are_counted(self):
        result=self.parse([{'category':'SEC Filing','link':'https://www.sec.gov/Archives/fixture'},{}])
        self.assertEqual(len(result),1)
        self.assertEqual(result.skipped[0].code,'COMPANY_SOURCE_FILING_COVERED_ELSEWHERE')

    def test_cached_fetch_keeps_diagnostics(self):
        class OfflineTransport:
            calls=0
            def get(self,source):
                self.calls+=1
                return feed([{}, {'link':'https://blogs.nvidia.com/blog/fixture'}])
        transport=OfflineTransport();provider=NvidiaProvider(transport=transport,now=lambda:NOW)
        query=NewsQuery('NVDA','NVIDIA',NOW)
        self.assertEqual(len(provider.fetch_items(query)),1)
        before=dict(provider.last_diagnostics)
        self.assertEqual(len(provider.fetch_items(query)),1)
        self.assertEqual(transport.calls,1)
        self.assertEqual(provider.last_diagnostics,before)
        self.assertEqual(len(before['nvidia_newsroom']),1)
        provider.clear_cache();self.assertEqual(provider.last_diagnostics,{})

    def test_canonical_translation_links_normalized(self):
        item=self.parse([{}])[0]
        # No new host: Unicode canonicalization also applies to retained links.
        translated=replace(item,metadata={'translation_urls':(NV+'/'+quote('가상').lower(),)})
        self.assertEqual(translated.metadata['translation_urls'],(NV+'/'+quote('가상'),))
