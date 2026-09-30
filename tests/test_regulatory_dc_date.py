"""Synthetic FSC Dublin Core date compatibility tests."""
import unittest
from datetime import date,datetime,timezone
from pathlib import Path
from magi.research.regulatory.parsing import parse
from magi.research.regulatory.service import build_regulatory_bundle
RAW=(Path(__file__).parent/'fixtures/regulatory/fsc_dc_date.xml').read_bytes()
NOW=datetime(2026,10,1,tzinfo=timezone.utc)
class FscDcDateTests(unittest.TestCase):
    def parse(self,raw=RAW):return parse('fsc_releases',raw,NOW)
    def test_observed_midnight_is_calendar_date(self):
        item=self.parse()[0]
        self.assertIs(type(item.published_at),date);self.assertEqual(item.published_at,date(2026,9,30))
        self.assertEqual(item.title,'가상 금융정책 안내');self.assertEqual(item.summary,'합성 한국어 설명입니다.')
        self.assertEqual(item.status.value,'UNKNOWN');self.assertEqual(item.regulatory_type.value,'PUBLIC_NOTICE');self.assertIsNone(item.effective_at)
        bundle=build_regulatory_bundle([item],created_at=NOW)
        self.assertEqual(bundle.events,());self.assertEqual(bundle.sources[0].metadata['published_at'],'2026-09-30')
        self.assertEqual(bundle.sources[0].metadata['publication_precision'],'DATE')
    def test_pubdate_precedence(self):
        raw=RAW.replace(b'</item>',b'<pubDate>Tue, 29 Sep 2026 09:00:00 +0900</pubDate></item>')
        self.assertEqual(self.parse(raw)[0].published_at.isoformat(),'2026-09-29T09:00:00+09:00')
    def test_empty_pubdate_does_not_fallback(self):
        self.assertIsNone(self.parse(RAW.replace(b'</item>',b'<pubDate/></item>'))[0].published_at)
    def test_invalid_pubdate_does_not_fallback(self):
        result=self.parse(RAW.replace(b'</item>',b'<pubDate>invalid</pubDate></item>'))
        self.assertEqual(len(result),0);self.assertEqual(result.skipped_count,1)
    def test_missing_dates_no_prose_inference(self):
        raw=RAW.replace(b'<dc:date>2026-09-30 00:00:00</dc:date>',b'').replace(b'</description>',b' 2026-09-30</description>')
        self.assertIsNone(self.parse(raw)[0].published_at)
    def test_malformed_and_unzoned_nonmidnight_rejected(self):
        for value in [b'2026-02-30 00:00:00',b'2026-09-30 12:30:00',b'September 30, 2026',b'bad']:
            with self.subTest(value=value):
                result=self.parse(RAW.replace(b'2026-09-30 00:00:00',value))
                self.assertEqual(len(result),0);self.assertEqual(result.skipped[0].code,'REGULATORY_INVALID_DATE')
    def test_namespace_identity_not_prefix(self):
        self.assertEqual(self.parse(RAW.replace(b'xmlns:dc=',b'xmlns:other=').replace(b'dc:date',b'other:date'))[0].published_at,date(2026,9,30))
        self.assertIsNone(self.parse(RAW.replace(b'http://purl.org/dc/elements/1.1/',b'https://example.invalid/'))[0].published_at)
    def test_existing_shared_aware_format(self):
        item=self.parse(RAW.replace(b'2026-09-30 00:00:00',b'2026-09-30T09:00:00+09:00'))[0]
        self.assertEqual(item.published_at.isoformat(),'2026-09-30T09:00:00+09:00')
