"""Offline news foundation tests. All content is synthetic, not live provider data."""
import json
import unittest
from dataclasses import replace, FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from magi.research.models import Authority, SourceType
from magi.research.serialization import dumps, loads
from magi.research.news.models import (NewsArticle, NewsClassification, ResearchEvent, ResearchCatalyst,
    EventCluster, NewsEvidencePack, EventType, CatalystType, Direction, TimeHorizon,
    VerificationStatus as Status, ContentKind, Relevance, Freshness, Sentiment, NewsWarning)
from magi.research.news.base import NewsQuery
from magi.research.news.service import build_news_pack, NewsService
from magi.research.news.deduplication import deduplicate
from magi.research.news.recency import freshness, age
from magi.research.news.relevance import relevance
from magi.research.news.selection import select_news, NewsSelectionPolicy, FUTURE_VIEWS
from magi.research.news.presentation import render_news, render_news_context
from magi.research.news.urls import canonical_url

NOW=datetime(2026,9,27,13,tzinfo=timezone.utc)
ENUMS={'event_type':EventType,'catalyst_type':CatalystType,'direction':Direction,'time_horizon':TimeHorizon,
       'status':Status,'content_kind':ContentKind,'sentiment':Sentiment}


def fixtures():
    output={}
    for raw in json.loads((Path(__file__).parent/'fixtures/news/articles.json').read_text()):
        name=raw.pop('fixture_name');c=raw.pop('classification',{})
        for k,cls in ENUMS.items():
            if k in c: c[k]=cls(c[k])
        if 'confidence' in c: c['confidence']=Decimal(c['confidence'])
        if c.get('event_time'): c['event_time']=datetime.fromisoformat(c['event_time'])
        for k in ('published_at','retrieved_at'):
            if raw.get(k): raw[k]=datetime.fromisoformat(raw[k])
        for k,cls in [('authority',Authority),('source_type',SourceType)]:
            if k in raw: raw[k]=cls(raw[k])
        output[name]=NewsArticle(**raw,classification=NewsClassification(**c))
    return output


def changed(article,**kwargs): return replace(article,article_id='',**kwargs)
def annotated(article,**kwargs): return changed(article,classification=replace(article.classification,**kwargs))


class NewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.fixture=fixtures()

    def setUp(self):
        for name in ('socket.socket.connect','socket.socket.connect_ex','socket.create_connection','socket.getaddrinfo'):
            guard=patch(name,side_effect=AssertionError('Offline news test attempted network'));guard.start();self.addCleanup(guard.stop)
        self.a=self.fixture['professional']

    def pack(self,*names):
        return build_news_pack([self.fixture[n] for n in names] if names else self.fixture.values(),ticker='ACMP',subject='Aster Compute',created_at=NOW)

    def test_article_creation(self): self.assertEqual(self.a.publisher,'Example Wire')
    def test_deterministic_id(self): self.assertEqual(changed(self.a).article_id,self.a.article_id)
    def test_content_revision_changes_id(self): self.assertNotEqual(changed(self.a,summary='Correction').article_id,self.a.article_id)
    def test_ticker_normalization(self): self.assertEqual(changed(self.a,tickers=[' acmp ','peer']).tickers,('ACMP','PEER'))
    def test_multiple_tickers(self): self.assertEqual(len(self.a.tickers),2)
    def test_leading_zero(self):
        a=changed(self.a,tickers=['005930'],classification=NewsClassification());self.assertEqual(a.tickers,('005930',))
    def test_missing_title(self):
        with self.assertRaises(ValueError): changed(self.a,title='')
    def test_missing_url(self):
        with self.assertRaises(ValueError): changed(self.a,url=None)
    def test_missing_summary_allowed(self): self.assertIsNone(changed(self.a,summary=None).summary)
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError): changed(self.a,retrieved_at=datetime(2026,9,27))
    def test_publication_after_retrieval(self):
        with self.assertRaises(ValueError): changed(self.a,published_at=NOW+timedelta(days=1))
    def test_url_canonicalization(self):
        self.assertEqual(canonical_url('https://EXAMPLE.invalid:443/a?utm_source=x&b=2&a=1#fragment'),'https://example.invalid/a?a=1&b=2')
    def test_url_identity_query_retained(self): self.assertNotEqual(canonical_url('https://x.invalid/a?id=1'),canonical_url('https://x.invalid/a?id=2'))
    def test_credential_url_rejected(self):
        with self.assertRaises(ValueError): changed(self.a,url='https://x.invalid/?api_key=synthetic')
    def test_sensitive_metadata_rejected(self):
        with self.assertRaises(ValueError): changed(self.a,metadata={'access_token':'synthetic'})
    def test_immutable(self):
        with self.assertRaises(FrozenInstanceError): self.a.title='changed'
    def test_same_exact_article(self): self.assertEqual(len(deduplicate([self.a,self.a])),1)
    def test_same_url(self):
        b=changed(self.a,provider='other',external_id='other',url=self.a.url+'?utm_source=x')
        groups=deduplicate([self.a,b]);self.assertEqual(len(groups),1);self.assertEqual(len(groups[0].members),2)
    def test_same_external_id(self):
        b=changed(self.a,url='https://x.invalid/other');self.assertEqual(len(deduplicate([self.a,b])),1)
    def test_external_id_provider_scoped(self):
        b=changed(self.a,provider='other',url='https://x.invalid/other',metadata={})
        self.assertEqual(len(deduplicate([self.a,b])),2)
    def test_syndicated_group(self):
        groups=deduplicate([self.a,self.fixture['syndicated']]);self.assertEqual(len(groups),1);self.assertTrue(groups[0].likely_syndicated)
    def test_independent_preserved(self): self.assertEqual(len(deduplicate([self.a,self.fixture['official']])),2)
    def test_correction_retained(self): self.assertEqual(len(deduplicate([self.a,self.fixture['correction']])[0].members),2)
    def test_dedup_order_independent(self):
        articles=tuple(self.fixture.values());self.assertEqual(deduplicate(articles),deduplicate(reversed(articles)))
    def test_event_types(self):
        for name,kind in [('earnings',EventType.EARNINGS),('professional',EventType.GUIDANCE),('product',EventType.PRODUCT),('regulatory',EventType.REGULATORY),('legal',EventType.LEGAL),('rumor',EventType.M_AND_A),('macro',EventType.MACRO)]:
            with self.subTest(name=name): self.assertEqual(self.pack(name).events[0].event_type,kind)
    def test_multi_source_event(self):
        p=self.pack('professional','official');self.assertEqual(len(p.event_clusters),1);self.assertEqual(len(p.event_clusters[0].source_ids),2)
    def test_primary_secondary_cluster(self):
        c=self.pack('professional','official').event_clusters[0];self.assertEqual(len(c.primary_source_ids),1);self.assertEqual(len(c.secondary_source_ids),1)
    def test_no_semantic_clustering(self):
        a=annotated(self.a,event_key=None,event_namespace=None)
        b=changed(a,title='Similar headline',url='https://other.invalid/a',external_id='new')
        p=build_news_pack([a,b],ticker='ACMP',subject='Aster',created_at=NOW);self.assertEqual(len(p.event_clusters),2)
    def test_different_event_dates_separate(self):
        b=annotated(self.a,event_time=self.a.classification.event_time-timedelta(days=1))
        p=build_news_pack([self.a,b],ticker='ACMP',subject='Aster',created_at=NOW);self.assertEqual(len(p.event_clusters),2)
    def test_positive_catalyst(self): self.assertEqual(self.pack('professional').catalysts[0].direction,Direction.POSITIVE)
    def test_negative_catalyst(self): self.assertEqual(self.pack('legal').catalysts[0].direction,Direction.NEGATIVE)
    def test_mixed_catalyst(self): self.assertEqual(self.pack('macro').catalysts[0].direction,Direction.MIXED)
    def test_horizon(self): self.assertEqual(self.pack('professional').catalysts[0].time_horizon,TimeHorizon.SHORT_TERM)
    def test_catalyst_references(self):
        p=self.pack('professional');c=p.catalysts[0];self.assertEqual(c.evidence_ids,(p.evidence_items[0].evidence_id,));self.assertEqual(c.source_ids,(p.sources[0].source_id,))
    def test_confidence_validation(self):
        for value in [Decimal('-1'),Decimal('2'),Decimal('NaN'),0.5]:
            with self.subTest(value=value),self.assertRaises(ValueError): annotated(self.a,confidence=value)
    def test_no_keyword_classification(self):
        a=changed(self.a,title='BUY strong growth bullish earnings beat',classification=NewsClassification())
        p=build_news_pack([a],ticker='ACMP',subject='Aster',created_at=NOW);self.assertFalse(p.catalysts);self.assertEqual(p.events[0].event_type,EventType.OTHER)
    def test_classification_provenance_required(self):
        with self.assertRaises(ValueError): NewsClassification(sentiment=Sentiment.POSITIVE)
    def test_provider_sentiment_separate(self):
        a=annotated(self.a,sentiment=Sentiment.NEGATIVE)
        p=build_news_pack([a],ticker='ACMP',subject='Aster',created_at=NOW)
        self.assertEqual(p.events[0].metadata['sentiment'],'NEGATIVE');self.assertNotIn('NEGATIVE',p.evidence_items[0].statement)
    def test_primary_relevance(self): self.assertEqual(relevance(self.a,'acmp'),Relevance.PRIMARY_SUBJECT)
    def test_mention_relevance(self): self.assertEqual(relevance(self.a,'PEER'),Relevance.MENTION_ONLY)
    def test_direct_relevance(self):
        a=annotated(self.a,relevance={'ACMP':'DIRECTLY_RELATED'});self.assertEqual(relevance(a,'ACMP'),Relevance.DIRECTLY_RELATED)
    def test_unknown_ticker_not_matched(self): self.assertIsNone(relevance(self.a,'OTHER'))
    def test_relevance_unknown_entity_rejected(self):
        with self.assertRaises(ValueError): annotated(self.a,relevance={'OTHER':'PRIMARY_SUBJECT'})
    def test_official_status(self): self.assertEqual(self.pack('official').event_clusters[0].status,Status.CONFIRMED_OFFICIAL)
    def test_official_cannot_be_secondary(self):
        with self.assertRaises(ValueError): annotated(self.a,status=Status.CONFIRMED_OFFICIAL)
    def test_corroborated_explicit(self):
        a=annotated(self.a,status=Status.CORROBORATED)
        p=build_news_pack([a],ticker='ACMP',subject='Aster',created_at=NOW);self.assertEqual(p.event_clusters[0].status,Status.CORROBORATED)
    def test_single_source(self): self.assertEqual(self.pack('professional').event_clusters[0].status,Status.SINGLE_SOURCE)
    def test_syndication_not_corroboration(self): self.assertEqual(self.pack('professional','syndicated').event_clusters[0].status,Status.SINGLE_SOURCE)
    def test_rumor_preserved(self): self.assertEqual(self.pack('rumor').event_clusters[0].status,Status.RUMOR)
    def test_conflict_preserves_both(self):
        p=self.pack('rumor','conflict');self.assertEqual(p.event_clusters[0].status,Status.DISPUTED);self.assertEqual(len(p.evidence_items),2);self.assertIn(NewsWarning.CONFLICTING_NEWS,p.warnings)
    def test_attribution_preserved(self):
        e=self.pack('professional').evidence_items[0];self.assertIn('Chief executive',e.statement);self.assertIn('subject to demand uncertainty',e.statement);self.assertEqual(e.metadata['content_kind'],'REPORTED_CLAIM')
    def test_opinion_preserved(self):
        a=annotated(self.a,content_kind=ContentKind.OPINION)
        p=build_news_pack([a],ticker='ACMP',subject='Aster',created_at=NOW);self.assertTrue(p.evidence_items[0].statement.startswith('OPINION'))
    def test_freshness_categories(self):
        for hours,kind in [(0,Freshness.BREAKING),(1,Freshness.BREAKING),(2,Freshness.RECENT),(24,Freshness.RECENT),(25,Freshness.CURRENT),(168,Freshness.CURRENT),(169,Freshness.AGING),(720,Freshness.AGING),(721,Freshness.STALE)]:
            with self.subTest(hours=hours): self.assertEqual(freshness(NOW-timedelta(hours=hours),NOW),kind)
    def test_missing_date_freshness(self): self.assertEqual(freshness(None,NOW),Freshness.UNKNOWN)
    def test_age(self): self.assertEqual(age(NOW-timedelta(minutes=5),NOW),timedelta(minutes=5))
    def test_future_age_rejected(self):
        with self.assertRaises(ValueError): age(NOW+timedelta(seconds=1),NOW)
    def test_pack_deterministic_order(self):
        values=tuple(self.fixture.values());p=self.pack();q=build_news_pack(reversed(values),ticker='ACMP',subject='Aster Compute',created_at=NOW)
        self.assertEqual(p,q);self.assertEqual(dumps(p),dumps(q))
    def test_pack_exact_duplicate_dropped(self):
        p=build_news_pack([self.a,self.a],ticker='ACMP',subject='Aster',created_at=NOW);self.assertEqual(len(p.articles),1)
    def test_pack_roundtrip(self): self.assertEqual(loads(dumps(self.pack())),self.pack())
    def test_all_models_roundtrip(self):
        p=self.pack('professional')
        for value in [self.a,self.a.classification,p.events[0],p.catalysts[0],p.event_clusters[0]]:
            with self.subTest(kind=type(value).__name__): self.assertEqual(loads(dumps(value)),value)
    def test_decimal_roundtrip(self): self.assertIsInstance(loads(dumps(self.pack('professional'))).catalysts[0].confidence,Decimal)
    def test_invalid_schema(self):
        with self.assertRaises(ValueError): changed(self.a,schema_version=2)
    def test_forged_identity_rejected(self):
        with self.assertRaises(ValueError): replace(self.a,article_id='N_forged')
    def test_missing_evidence_rejected(self):
        with self.assertRaises(ValueError): replace(self.pack('professional'),evidence_items=(),pack_id='')
    def test_forged_event_rejected(self):
        p=self.pack('professional');event=replace(p.events[0],description='fabrication',event_id='')
        with self.assertRaises(ValueError): replace(p,events=(event,),pack_id='')
    def test_future_retrieval_rejected(self):
        with self.assertRaises(ValueError): build_news_pack([self.a],ticker='ACMP',subject='Aster',created_at=NOW-timedelta(days=1))
    def test_empty_pack_warnings(self):
        p=build_news_pack([],ticker='ACMP',subject='Aster',created_at=NOW);self.assertIn(NewsWarning.NO_RECENT_NEWS,p.warnings);self.assertIn(NewsWarning.NO_PRIMARY_SOURCE,p.warnings)
    def test_missing_publication_warning(self): self.assertIn(NewsWarning.MISSING_PUBLICATION_DATE,self.pack('missing_date').warnings)
    def test_stale_warning(self): self.assertIn(NewsWarning.STALE_NEWS,self.pack('stale').warnings)
    def test_low_diversity_warning(self): self.assertIn(NewsWarning.LOW_SOURCE_DIVERSITY,self.pack('professional').warnings)
    def test_rumor_warning(self): self.assertIn(NewsWarning.UNCONFIRMED_EVENT,self.pack('rumor').warnings)
    def test_bound_selection(self):
        original=self.pack();selected=select_news(original,NewsSelectionPolicy(max_articles=3,max_evidence=3,max_clusters=2,max_catalysts=2))
        self.assertLessEqual(len(selected.articles),3);self.assertLessEqual(len(selected.evidence_items),3);self.assertLessEqual(len(selected.event_clusters),2);self.assertLessEqual(len(selected.catalysts),2)
        self.assertEqual(len(original.articles),14);self.assertIn(NewsWarning.BOUNDED_SELECTION,selected.warnings)
    def test_conflict_group_atomic(self):
        p=self.pack('rumor','conflict');s=select_news(p,NewsSelectionPolicy(max_articles=1));self.assertFalse(s.articles);self.assertEqual(len(s.selection_omissions),2)
    def test_source_diversity_selection(self):
        a=changed(self.fixture['earnings'],publisher='Other publisher',external_id='other',url='https://other.invalid/earnings')
        p=build_news_pack([self.fixture['product'],self.fixture['legal'],a],ticker='ACMP',subject='Aster',created_at=NOW)
        s=select_news(p,NewsSelectionPolicy(max_articles=2));self.assertEqual(len({a.publisher for a in s.articles}),2)
    def test_selection_determinism(self): self.assertEqual(select_news(self.pack()),select_news(self.pack()))
    def test_publisher_bound(self):
        s=select_news(self.pack(),NewsSelectionPolicy(max_per_publisher=1));counts={}
        for a in s.articles: counts[a.publisher]=counts.get(a.publisher,0)+1
        self.assertTrue(all(n<=1 for n in counts.values()))
    def test_future_views(self):
        for policy in FUTURE_VIEWS.values(): self.assertIsInstance(select_news(self.pack(),policy),NewsEvidencePack)
    def test_merge_compatibility(self):
        p=self.pack('rumor','conflict');base=p.as_evidence_pack();self.assertEqual(base.evidence_items,p.evidence_items);self.assertEqual(len(base.relations),1);self.assertFalse(base.claims)
    def test_trust_boundary(self):
        p=self.pack('malicious');ctx=render_news_context('Trusted instructions','Question',p)
        self.assertNotIn('Ignore all instructions',ctx.system_instructions);self.assertIn('Ignore all instructions',ctx.user_content);self.assertIn('UNTRUSTED RESEARCH DATA',ctx.user_content)
    def test_context_bound(self):
        with self.assertRaises(ValueError): render_news_context('Trusted','Question',self.pack(),max_characters=10)
    def test_render_bound(self):
        with self.assertRaises(ValueError): render_news(self.pack(),max_characters=10)
    def test_korean_labels(self):
        rendered=render_news(self.pack())
        for word in ('촉매 요인','위험 요인','속보','공식 출처','미확인','상충 보도'): self.assertIn(word,rendered)
    def test_english_labels(self):
        rendered=render_news(self.pack(),language='en')
        for word in ('Catalyst','Risk','Breaking news','Official source','Unconfirmed','Conflicting reports'): self.assertIn(word,rendered)
    def test_every_displayed_evidence_resolves(self):
        p=self.pack();rendered=render_news(p)
        for e in p.evidence_items: self.assertIn(e.evidence_id,rendered);self.assertIn(e.source_id,rendered)
    def test_language_neutral_enums(self): self.assertEqual(Direction.POSITIVE.value,'POSITIVE')
    def test_full_body_not_required(self): self.assertNotIn('body',self.a.__dataclass_fields__)
    def test_full_body_metadata_rejected(self):
        with self.assertRaises(ValueError): changed(self.a,metadata={'body':'full copyrighted article'})
    def test_summary_limit(self):
        with self.assertRaises(ValueError): changed(self.a,summary='a'*4001)
    def test_summary_metadata_serialization(self): self.assertEqual(loads(dumps(self.a)).summary,self.a.summary)
    def test_query_validation(self):
        self.assertEqual(NewsQuery('acmp','Aster',NOW).ticker,'ACMP')
        with self.assertRaises(ValueError): NewsQuery('ACMP','Aster',NOW,limit=0)
    def test_injected_provider(self):
        article=self.a
        class FakeProvider:
            def fetch(self,query): return (article,)
        p=NewsService(FakeProvider()).collect(NewsQuery('ACMP','Aster',NOW));self.assertEqual(len(p.articles),1)
    def test_market_moving_metadata(self):
        a=annotated(self.a,market_moving=True)
        p=build_news_pack([a],ticker='ACMP',subject='Aster',created_at=NOW);self.assertTrue(p.events[0].metadata['market_moving'])
    def test_internal_history_authority(self):
        with self.assertRaises(ValueError): changed(self.a,source_type=SourceType.MAGI_MEMORY)
    def test_timezone_event_group(self):
        a=self.a
        b=annotated(changed(a,provider='other',external_id='other',url='https://other.invalid/a'),event_time=a.classification.event_time.astimezone(timezone(timedelta(hours=14))))
        p=build_news_pack([a,b],ticker='ACMP',subject='Aster',created_at=NOW);self.assertEqual(len(p.event_clusters),1)
    def test_query_provider_limit(self):
        article=self.a
        class FakeProvider:
            def fetch(self,query): return (article,article)
        with self.assertRaises(ValueError): NewsService(FakeProvider()).collect(NewsQuery('ACMP','Aster',NOW,limit=1))
    def test_query_since(self):
        article=self.a
        class FakeProvider:
            def fetch(self,query): return (article,)
        p=NewsService(FakeProvider()).collect(NewsQuery('ACMP','Aster',NOW,since=NOW-timedelta(minutes=1)))
        self.assertFalse(p.articles)
