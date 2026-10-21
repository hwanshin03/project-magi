"""Synthetic finalization security/identity regressions; no live output or facts."""
from dataclasses import asdict, replace, FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import unittest
from unittest.mock import patch
from test_analysis import run, agents, state
from magi.analysis.models import (AnalysisRequest, ResearchInput, AnalysisResult, AnalysisError,
    FinalizationStage, FinalizationDiagnostic, dumps, loads)
from magi.analysis.orchestrator import analyze
from magi.decision import AgentResult, Position, Availability as AgentAvailability
from magi.voting import VotingEngine, FinalAction
from magi.research.models import ResearchSource, SourceType, Authority, EvidenceItem, Category, EvidencePack
from magi.research.news.models import NewsArticle, NewsClassification
from magi.research.news.service import build_news_pack
from magi.research.balancing.models import (TargetIdentity, InstrumentIdentity, InputFamily,
    InputAvailability, Availability, fingerprint)
from magi.research.serialization import encode
from magi.research.sec_projection import SECAnalyticalProjection


def legacy_id(result):
    # Independent copy of the pre-correction canonical hash contract, not its
    # erroneous validation of JSON escape sequences as original path content.
    payload = (result.request.request_id, result.selection.selection_id,
               tuple(str(r) for r in result.agent_results), result.explanation, result.explanation_error)
    if result.sec_projections:
        payload += (tuple(p.projection_id for p in result.sec_projections),)
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                        default=str).encode()).hexdigest()
    return 'AN_' + digest('BF_' + digest(encode(payload)))


def synthetic_ko():
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    request = AnalysisRequest(TargetIdentity(InstrumentIdentity('US', 'KO')),
        'Synthetic diagnostic only', now, requested=('company', 'news', 'sec'),
        required=('sec',), report='annual', currency='USD')
    sources, items = [], []
    for year in (2024, 2025):
        source = ResearchSource(SourceType.SEC_FILING, Authority.PRIMARY, 'SEC',
            'Synthetic annual diagnostic', 'Synthetic issuer', now,
            published_at=datetime(year + 1, 2, 1, 12, tzinfo=timezone.utc),
            url='https://example.invalid/synthetic/' + str(year), ticker='KO', market='US',
            company_name='Synthetic issuer', document_type='10-K', external_id='synthetic-' + str(year),
            metadata={'issuer_id': 'SEC:0000000001', 'cik': '0000000001',
                      'report_date': str(year) + '-12-31', 'publication_precision': 'instant'})
        sources.append(source)
        for concept, category, start in (('Revenues', Category.FINANCIAL, date(year, 1, 1)),
                                         ('Assets', Category.BALANCE_SHEET, None)):
            items.append(EvidenceItem(source.source_id, 'Synthetic issuer', category,
                'Synthetic diagnostic observation', now, ticker='KO', value=Decimal(100), unit='USD',
                period_start=start, period_end=date(year, 12, 31),
                metadata={'taxonomy': 'us-gaap', 'concept': concept, 'form': '10-K', 'fp': 'FY', 'fy': year}))
    pack = EvidencePack('Synthetic issuer', now, tuple(sources), tuple(items), ticker='KO', market='US')
    articles = tuple(NewsArticle('synthetic', 'Synthetic publisher ' + str(i),
        'Synthetic diagnostic headline ' + str(i), 'Synthetic diagnostic summary',
        'https://example.invalid/synthetic-news/' + str(i), now - timedelta(days=1), now,
        'en', ('KO',), ('Synthetic issuer',),
        classification=NewsClassification(classification_source='synthetic-diagnostic')) for i in range(3))
    news = build_news_pack(articles, ticker='KO', subject='Synthetic issuer', created_at=now)
    aa = agents()
    for agent, confidence in zip(aa, (.9, .4, .15)):
        agent.result = replace(agent.result, position=Position.ABSTAIN, confidence=confidence,
            reasoning='Synthetic findings:\n진단 only', key_risks=('Synthetic risks:\tbounded',),
            evidence_gaps=('Synthetic gaps:"missing concepts"',))
    return analyze(request, inputs=(ResearchInput('sec', (pack,)), ResearchInput('news', (news,))), agents=aa)


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo',
                     'socket.create_connection', 'httpx.HTTPTransport.handle_request', 'sqlite3.connect'):
            guard = patch(name, side_effect=AssertionError('Offline finalization test'))
            guard.start(); self.addCleanup(guard.stop)
        self.base = run()

    def changed_agent(self, **kwargs):
        return (replace(self.base.agent_results[0], **kwargs), *self.base.agent_results[1:])

    def reject(self, stage, **kwargs):
        with self.assertRaises(AnalysisError) as caught:
            replace(self.base, artifact_id='', **kwargs)
        error = caught.exception
        self.assertEqual(error.code, 'INVALID_ANALYSIS_RESULT')
        self.assertEqual(str(error), 'INVALID_ANALYSIS_RESULT')
        self.assertEqual(error.diagnostic, FinalizationDiagnostic(stage))
        self.assertEqual(set(asdict(error.diagnostic)), {'stage', 'category'})
        return error

    def test_formatting_fields_and_legacy_hash(self):
        texts = ('Synthetic plain', 'Synthetic\nline', 'Synthetic: space', 'Synthetic:\nline',
                 'Synthetic:\tline', 'Synthetic:"quote"', 'Synthetic:\nA:\tB:"C"',
                 '합성 진단:\n한국어:\t안전:"인용"', r'Synthetic literal \ slash')
        for field in ('reasoning', 'key_risks', 'evidence_gaps', 'provider', 'model', 'error'):
            for text in texts:
                with self.subTest(field=field, text=text):
                    value = (text,) if field in ('key_risks', 'evidence_gaps') else text
                    result = replace(self.base, agent_results=self.changed_agent(**{field: value}), artifact_id='')
                    self.assertEqual(result.artifact_id, legacy_id(result))
                    self.assertEqual(result.request.request_id, self.base.request.request_id)
                    self.assertEqual(result.selection.selection_id, self.base.selection.selection_id)
                    self.assertEqual(result.vote.final_action, self.base.vote.final_action)
                    self.assertEqual(loads(dumps(result)), result)

    def test_prohibited_original_fields(self):
        texts = (r'C:\synthetic', r'C:\Users\synthetic', '/tmp/synthetic', '/Users/synthetic/item',
                 '/home/synthetic', '/private/synthetic', '/var/synthetic', '/etc/synthetic',
                 'file://synthetic', 'API_KEY=synthetic-placeholder',
                 'https://example.invalid/?token=synthetic', 'Authorization: synthetic',
                 '{"synthetic":"C:\\Users\\synthetic"}')
        for field in ('reasoning', 'key_risks', 'evidence_gaps', 'provider', 'model', 'error'):
            for text in texts:
                with self.subTest(field=field, text=text):
                    value = (text,) if field in ('key_risks', 'evidence_gaps') else text
                    error = self.reject(FinalizationStage.AGENT_CONTENT_SAFETY,
                                        agent_results=self.changed_agent(**{field: value}))
                    self.assertNotIn(text, repr(error.diagnostic))
                    self.assertNotIn(text, str(error))

    def test_shared_fingerprint_has_no_json_exemption(self):
        with self.assertRaises(ValueError): fingerprint('Synthetic:\\nline')
        with self.assertRaises(ValueError): fingerprint('{"x":"C:\\synthetic"}')
        fingerprint('Synthetic:\nline')

    def test_existing_valid_identity_uses_legacy_contract(self):
        self.assertEqual(self.base.artifact_id, legacy_id(self.base))
        # Captured by running the committed pre-correction constructor on this fixture.
        self.assertEqual(self.base.artifact_id,
                         'AN_d6efa506ae6beed0c2aa8dafa6cddf6466cbd19d6badb66d103298b51af9d3bf')
        self.assertEqual(self.base.request.request_id,
                         'AR_04ff7727e8719fb20e27157e0c7388a1b42e6312da2de2faee476a45704f4fd1')
        self.assertEqual(self.base.selection.selection_id,
                         'ES_e4810506e4dcb1e0c57982caf1d92a497caf559883b36dce476b593ff6f7b524')
        self.assertEqual(self.base, run())

    def test_explanation_paths_still_rejected(self):
        self.reject(FinalizationStage.ARTIFACT_FINGERPRINT, explanation='/tmp/synthetic')
        self.reject(FinalizationStage.EXPLANATION_VALIDATION, explanation='API_KEY=synthetic-placeholder')

    def test_agent_schema_reconstruction(self):
        bad = replace(self.base.agent_results[0])
        object.__setattr__(bad, 'confidence', 2)
        self.reject(FinalizationStage.AGENT_CONTENT_SAFETY, agent_results=(bad, *self.base.agent_results[1:]))
        self.reject(FinalizationStage.AGENT_CONTENT_SAFETY, agent_results=({}, *self.base.agent_results[1:]))
        with self.assertRaises(TypeError): AgentResult(agent='Melchior')

    def test_missing_duplicate_and_unknown_agents(self):
        self.reject(FinalizationStage.AGENT_CONTENT_SAFETY, agent_results=self.base.agent_results[:2])
        self.reject(FinalizationStage.AGENT_CONTENT_SAFETY, agent_results=(self.base.agent_results[0],) * 3)
        self.reject(FinalizationStage.VOTING_VALIDATION, agent_results=self.changed_agent(agent='Unknown'))

    def test_forged_request(self):
        request = replace(self.base.request)
        object.__setattr__(request, 'request_id', 'forged')
        self.reject(FinalizationStage.REQUEST_VALIDATION, request=request)

    def test_forged_artifact(self):
        with self.assertRaises(AnalysisError) as caught: replace(self.base, artifact_id='forged')
        self.assertEqual(caught.exception.diagnostic.stage, FinalizationStage.ARTIFACT_FINGERPRINT)

    def test_corrupt_selection(self):
        selection = replace(self.base.selection)
        object.__setattr__(selection, 'selection_id', 'forged')
        self.reject(FinalizationStage.SELECTION_VALIDATION, selection=selection)

    def test_corrupt_citation_closure(self):
        selection = replace(self.base.selection)
        group = replace(selection.common_core[0], citations=())
        object.__setattr__(selection, 'common_core', (group, *selection.common_core[1:]))
        self.reject(FinalizationStage.SELECTION_VALIDATION, selection=selection)

    def test_projection_policy(self):
        result = synthetic_ko(); self.base = result
        p = result.sec_projections[0]
        bad = SECAnalyticalProjection(p.catalog, replace(p.policy, scope='Synthetic incompatible'))
        self.reject(FinalizationStage.SEC_PROJECTION_VALIDATION, sec_projections=(bad,))

    def test_projection_audit(self):
        self.base = synthetic_ko()
        bad = replace(self.base.sec_projections[0])
        object.__setattr__(bad, 'audit', ())
        self.reject(FinalizationStage.SEC_PROJECTION_VALIDATION, sec_projections=(bad,))

    def test_invalid_availability(self):
        for status in (Availability.AVAILABLE, Availability.NOT_SUPPLIED, Availability.EMPTY):
            with self.subTest(status=status), self.assertRaises(ValueError):
                InputAvailability(InputFamily.NEWS, status, error_codes=('SYNTHETIC_FAILURE',))
        with self.assertRaises(ValueError): InputAvailability(InputFamily.NEWS, Availability.PARTIAL)

    def test_serialization_tampering(self):
        raw = json.loads(dumps(self.base)); raw['artifact_id'] = 'forged'
        with self.assertRaises(ValueError): loads(json.dumps(raw))
        with self.assertRaises(ValueError): loads('{"schema":"7F-v1",' + dumps(self.base)[1:])
        del raw['agent_results'][0]['reasoning']
        with self.assertRaises(ValueError): loads(json.dumps(raw))

    def test_diagnostic_bounded_and_immutable(self):
        diagnostic = FinalizationDiagnostic(FinalizationStage.ARTIFACT_FINGERPRINT)
        with self.assertRaises(FrozenInstanceError): diagnostic.category = 'other'
        with self.assertRaises(ValueError): FinalizationDiagnostic('untrusted')
        with self.assertRaises(ValueError): FinalizationDiagnostic(FinalizationStage.ARTIFACT_FINGERPRINT, 'untrusted')
        self.assertNotIn('diagnostic', json.loads(dumps(self.base)))

    def test_orchestrator_preserves_safe_stage(self):
        aa = agents(); aa[0].result = replace(aa[0].result, reasoning='/tmp/synthetic')
        with self.assertRaises(AnalysisError) as caught: run(agents=aa)
        self.assertEqual(caught.exception.code, 'INVALID_ANALYSIS_RESULT')
        self.assertEqual(caught.exception.diagnostic.stage, FinalizationStage.AGENT_CONTENT_SAFETY)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_ko_shape_and_round_trip(self):
        result = synthetic_ko(); selection = result.selection
        self.assertEqual(len(selection.assessment.grouped.groups), 5)
        self.assertEqual(len(selection.assessment.assessments), 5)
        self.assertEqual(len(selection.common_core), 3)
        self.assertEqual(sum(len(g.units) for g in selection.common_core), 3)
        self.assertEqual(sum(len(g.citations) for g in selection.common_core), 4)
        refs = {r for g in selection.common_core for r in (*g.units, *g.citations)}
        self.assertEqual(len(refs), 7)
        self.assertEqual(len(selection.omissions), 2)
        self.assertEqual({o.reason for o in selection.omissions}, {'REVIEW_LIMIT'})
        self.assertEqual(state(result, InputFamily.NEWS).state, Availability.PARTIAL)
        self.assertEqual(state(result, InputFamily.NEWS).error_codes, ('COMPANY.COLLECTOR_NOT_CONFIGURED',))
        self.assertEqual(state(result, InputFamily.RESEARCH).state, Availability.AVAILABLE)
        for family in set(InputFamily) - {InputFamily.NEWS, InputFamily.RESEARCH}:
            self.assertEqual(state(result, family).state, Availability.NOT_SUPPLIED)
        items = result.sec_projections[0].analytical_pack.evidence_items
        self.assertEqual({(e.period_start, e.period_end) for e in items if e.period_start},
                         {(date(y, 1, 1), date(y, 12, 31)) for y in (2024, 2025)})
        self.assertEqual([e.period_end for e in items if e.period_start is None], [date(2025, 12, 31)])
        self.assertEqual({e.metadata['concept'] for e in items}, {'Assets', 'Revenues'})
        self.assertTrue(all(a.availability == AgentAvailability.AVAILABLE for a in result.agent_results))
        vote = VotingEngine().vote({a.agent: a for a in result.agent_results})
        self.assertEqual(vote, result.vote)
        self.assertEqual((vote.eligible_voters, vote.buy_votes, vote.hold_votes, vote.sell_votes), ((), 0, 0, 0))
        self.assertEqual(vote.final_action, FinalAction.INSUFFICIENT_PARTICIPATION)
        self.assertEqual(loads(dumps(result)), result)
        self.assertEqual(result.artifact_id, legacy_id(result))
        self.assertEqual(result, synthetic_ko())
