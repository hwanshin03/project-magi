"""Synthetic completion diagnostics; no credentials, sockets, or database access."""
import json
import httpx
import anthropic
import unittest
from dataclasses import asdict
from types import SimpleNamespace as NS
from unittest.mock import patch

from anthropic.types import Message
from magi.casper import Casper
from magi.casper_response import MAX_TOKENS, response_result
from magi.decision import Availability, parse_decision
from magi.voting import VotingEngine, FinalAction
from test_resilience import fake_clients, status_error


def body(**overrides):
    return json.dumps(dict(position='HOLD', confidence=.7, reasoning='Synthetic rationale',
                          key_risks=[], evidence_gaps=[], **overrides))


def response(text=None, *, stop='end_turn', blocks=None, tokens=100):
    return NS(content=blocks if blocks is not None else [NS(type='text', text=body() if text is None else text)],
              stop_reason=stop, usage=NS(output_tokens=tokens))


class CasperResponseTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo',
                     'httpx.HTTPTransport.handle_request', 'sqlite3.connect'):
            p=patch(name, side_effect=AssertionError('Offline test'));p.start();self.addCleanup(p.stop)

    def check_failure(self, message, category):
        r,d=response_result(message,1)
        self.assertEqual(d.failure_category,category)
        self.assertEqual(r.error,'invalid_response');self.assertEqual(r.availability,Availability.UNAVAILABLE)
        self.assertIsNone(r.position);self.assertEqual(r.confidence,0);self.assertFalse(r.vote_eligible)
        return d

    def test_completed(self):
        r,d=response_result(response(),1)
        self.assertEqual(r,parse_decision(body(),'Casper','Anthropic','claude-sonnet-4-6'))
        self.assertIsNone(d.failure_category);self.assertEqual(d.content_blocks,(('text',1),))
        self.assertEqual((d.provider,d.model,d.max_tokens,d.output_tokens,d.attempts),
                         ('Anthropic','claude-sonnet-4-6',3072,100,1))

    def test_typed_sdk_message(self):
        m=Message(id='synthetic',role='assistant',type='message',model='claude-sonnet-4-6',
                  content=[{'type':'text','text':body()}],stop_reason='end_turn',
                  usage={'input_tokens':10,'output_tokens':2500})
        result,diagnostic=response_result(m,1)
        self.assertEqual(result.availability,Availability.AVAILABLE)
        self.assertIsNone(diagnostic.failure_category)
        self.assertEqual(diagnostic.output_tokens,2500)

    def test_max_tokens_truncated(self):
        for tokens in (2000,3072):
            with self.subTest(tokens=tokens):
                self.check_failure(response(body()[:-1],stop='max_tokens',tokens=tokens),'incomplete_response')

    def test_max_tokens_valid_json(self):
        for tokens in (2000,3072):
            with self.subTest(tokens=tokens):
                self.check_failure(response(stop='max_tokens',tokens=tokens),'incomplete_response')

    def test_other_incomplete_stops(self):
        for stop in ('pause_turn','model_context_window_exceeded'):
            with self.subTest(stop=stop):self.check_failure(response(stop=stop),'incomplete_response')

    def test_missing_unknown_completion(self):
        for stop in (None,'unexpected',[1],True):
            with self.subTest(stop=stop):self.check_failure(response(stop=stop),'invalid_completion_metadata')

    def test_unsupported_completion(self):
        for stop in ('stop_sequence','tool_use','refusal'):
            with self.subTest(stop=stop):self.check_failure(response(stop=stop),'unsupported_completion')

    def test_empty_content(self):
        for blocks in ([],[NS(type='text',text='')],[NS(type='text',text=' \n\t')]):
            self.check_failure(response(blocks=blocks),'empty_response')

    def test_split_blocks(self):
        s=body();m=response(blocks=[NS(type='text',text=''),NS(type='text',text=s[:20]),NS(type='text',text=s[20:])])
        r,d=response_result(m,1);self.assertIsNone(d.failure_category);self.assertEqual(d.content_blocks,(('text',3),))
        self.assertEqual(r.availability,Availability.AVAILABLE)

    def test_separate_json_objects_rejected(self):
        self.check_failure(response(blocks=[NS(type='text',text=body())]*2),'json_decoding_failure')

    def test_unsupported_blocks_not_silently_discarded(self):
        for kind in ('thinking','redacted_thinking','tool_use','unknown',None):
            with self.subTest(kind=kind):
                self.check_failure(response(blocks=[NS(type=kind),NS(type='text',text=body())]),'unsupported_content_blocks')

    def test_invalid_text_shape(self):
        for b in (NS(type='text',text=None),NS(type='text',text=42),{'type':'text','text':body()},NS(text=body())):
            self.check_failure(response(blocks=[b]),'unsupported_content_blocks')

    def test_invalid_content_container(self):
        for content in (None,{},'text'):
            self.check_failure(NS(stop_reason='end_turn',content=content),'unsupported_content_blocks')

    def test_strict_json(self):
        for text in ('not json','```json\n'+body()+'\n```','preamble '+body(),body()+' trailer',body()[:-1],
                     body()[:-1]+',"confidence":0.1}'):
            with self.subTest(text=text):self.check_failure(response(text),'json_decoding_failure')

    def test_schema_errors(self):
        data=json.loads(body())
        for value in ({},[],{**data,'position':'MAYBE'},{**data,'confidence':'0.5'},
                      {**data,'confidence':True},{**data,'confidence':2},{**data,'reasoning':''},
                      {**data,'key_risks':'risk'},{**data,'evidence_gaps':[42]}):
            with self.subTest(value=value):self.check_failure(response(json.dumps(value)),'schema_validation_failure')

    def test_unicode_whitespace_and_escapes(self):
        data=json.loads(body());data['reasoning']='한국어 🙂 "quoted"\n\t'
        r,d=response_result(response(' \n'+json.dumps(data,ensure_ascii=False)+'\t'),1)
        self.assertEqual(r.reasoning,data['reasoning']);self.assertIsNone(d.failure_category)

    def test_safe_diagnostics(self):
        marker='DO_NOT_RETAIN_SYNTHETIC_CONTENT'
        m=response(marker,stop=marker,blocks=[NS(type=marker,text=marker)],tokens=marker)
        m.model=marker;m.provider=marker
        d=self.check_failure(m,'invalid_completion_metadata')
        encoded=json.dumps(asdict(d));self.assertNotIn(marker,encoded)
        self.assertEqual(d.stop_reason,'UNKNOWN');self.assertEqual(d.content_blocks,(('UNKNOWN',1),))
        self.assertIsNone(d.output_tokens)
        d=self.check_failure(response(marker),'json_decoding_failure')
        self.assertNotIn(marker,json.dumps(asdict(d)))

    def test_usage_validation(self):
        for value in (None,True,-1,3073,1.5,'100'):
            self.assertIsNone(response_result(response(tokens=value),1)[1].output_tokens)
        for value in (0,1000,1001,2000,2001,2500,3072):self.assertEqual(response_result(response(tokens=value),1)[1].output_tokens,value)

    def test_request_configuration_and_no_parse_retry(self):
        with fake_clients() as (calls,_),patch('magi.provider.time.sleep') as sleep:
            calls['Anthropic'].return_value=response('bad json')
            a=Casper();r=a.think('Synthetic question')
            kw=calls['Anthropic'].call_args.kwargs
            self.assertEqual(set(kw),{'model','max_tokens','system','messages'})
            self.assertEqual(MAX_TOKENS,3072);self.assertEqual(kw['max_tokens'],3072);self.assertEqual(kw['model'],'claude-sonnet-4-6')
            self.assertEqual(calls['Anthropic'].call_count,1);sleep.assert_not_called()
            self.assertEqual(a.last_response_diagnostic.attempts,1);self.assertEqual(r.error,'invalid_response')

    def test_other_agents_request_options_unchanged(self):
        from magi.melchior import Melchior
        from magi.balthasar import Balthasar
        with fake_clients() as (calls,_):
            for cls in (Melchior,Balthasar):
                agent=cls();result=agent.think('Synthetic question')
                self.assertEqual(result.availability,Availability.AVAILABLE)
            self.assertEqual(set(calls['OpenAI'].call_args.kwargs),{'model','instructions','input'})
            gemini=calls['Gemini'].call_args.kwargs
            self.assertEqual(set(gemini),{'model','config','contents'})
            self.assertEqual(set(gemini['config']),{'system_instruction'})
            self.assertEqual(calls['OpenAI'].call_args.kwargs['model'],'gpt-5.5')
            self.assertEqual(gemini['model'],'gemini-2.5-flash')

    def test_provider_failures_never_retry(self):
        request=httpx.Request('POST','https://invalid.test')
        errors=[httpx.ReadTimeout('Synthetic'),httpx.ConnectTimeout('Synthetic'),
                anthropic.APITimeoutError(request=request),
                status_error('Anthropic',429),status_error('Anthropic',503),
                status_error('Anthropic',401)]
        for error in errors:
            with self.subTest(error=type(error).__name__,status=getattr(error,'status_code',None)):
                with fake_clients() as (calls,_),patch('magi.provider.time.sleep') as sleep:
                    calls['Anthropic'].side_effect=[error,response()]
                    a=Casper();r=a.think('Question')
                    self.assertEqual(calls['Anthropic'].call_count,1);sleep.assert_not_called()
                    self.assertEqual(r.availability,Availability.UNAVAILABLE)
                    self.assertIsNone(r.position);self.assertEqual(r.confidence,0);self.assertFalse(r.vote_eligible)
                    reason='non_retryable_provider_error' if getattr(error,'status_code',None)==401 else 'transient_failure_exhausted'
                    self.assertEqual(r.error,'provider_unavailable:'+reason)
                    self.assertEqual(r.attempts,1)
                    self.assertEqual(r.http_status,getattr(error,'status_code',None))
                    self.assertEqual(a.last_response_diagnostic.failure_category,'provider_failure')
                    self.assertEqual(a.last_response_diagnostic.attempts,1)

    def test_sdk_timeout_transport_contract(self):
        calls=[]
        def transport(request):
            payload=json.loads(request.content)
            self.assertEqual(payload['model'],'claude-sonnet-4-6')
            self.assertEqual(payload['max_tokens'],3072)
            calls.append(request.extensions['timeout'])
            raise httpx.ReadTimeout('Synthetic',request=request)
        def client(**options):
            self.assertEqual(options['max_retries'],0)
            options['api_key']='synthetic-offline-key'
            return anthropic.Anthropic(**options,http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        with patch('magi.casper.Anthropic',side_effect=client),patch('magi.provider.time.sleep') as sleep:
            a=Casper()
            try:r=a.think('Synthetic question')
            finally:a.client.close()
        self.assertEqual(calls,[dict(connect=30,read=60,write=30,pool=30)])
        sleep.assert_not_called()
        self.assertEqual(r.error,'provider_unavailable:transient_failure_exhausted')
        self.assertEqual(r.attempts,1);self.assertFalse(r.vote_eligible)
        self.assertEqual(a.last_response_diagnostic.attempts,1)

    def test_success_one_attempt(self):
        with fake_clients() as (calls,_),patch('magi.provider.time.sleep') as sleep:
            a=Casper();r=a.think('Synthetic question')
            self.assertEqual(r.availability,Availability.AVAILABLE)
            self.assertEqual(calls['Anthropic'].call_count,1);sleep.assert_not_called()
            self.assertEqual(a.last_response_diagnostic.attempts,1)
            self.assertIsNone(a.last_response_diagnostic.failure_category)

    def test_diagnostic_reset(self):
        with fake_clients() as (calls,_):
            a=Casper();calls['Anthropic'].return_value=response('bad');a.think('Question')
            calls['Anthropic'].return_value=response();a.think('Question')
            self.assertIsNone(a.last_response_diagnostic.failure_category)
            with patch('magi.casper.agent_request',side_effect=ValueError('synthetic')):
                with self.assertRaises(ValueError):a.think('Question')
            self.assertIsNone(a.last_response_diagnostic)

    def test_failure_cannot_vote(self):
        failed,_=response_result(response(stop='max_tokens'),1)
        results={name:parse_decision(body(),name,'Synthetic','Synthetic') for name in ('Melchior','Balthasar')}
        results['Casper']=failed;vote=VotingEngine().vote(results)
        self.assertEqual(vote.final_action,FinalAction.HOLD);self.assertNotIn('Casper',vote.eligible_voters)
        self.assertEqual(vote.excluded_agents[0].reason,'UNAVAILABLE')
        results['Melchior']=parse_decision(body().replace('HOLD','ABSTAIN'),'Melchior','Synthetic','Synthetic')
        self.assertEqual(VotingEngine().vote(results).final_action,FinalAction.INSUFFICIENT_PARTICIPATION)


if __name__=='__main__':unittest.main()
