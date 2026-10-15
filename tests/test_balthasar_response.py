"""SDK-typed synthetic Gemini responses; no credentials, network or live bodies."""
from dataclasses import asdict, FrozenInstanceError
import json
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import Mock, patch

import httpx
from google.genai import types
from magi.balthasar import Balthasar
from magi.balthasar_response import response_result
from magi.decision import Availability, unavailable_result
from magi.voting import VotingEngine, FinalAction
from test_resilience import status_error

DATA = dict(position='HOLD', confidence=0.5, reasoning='Synthetic answer',
            key_risks=['Synthetic risk'], evidence_gaps=['Synthetic gap'])
TEXT = json.dumps(DATA)


def message(text=TEXT, finish=types.FinishReason.STOP, *, parts=None, usage=None):
    return types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=finish,
        content=types.Content(parts=parts if parts is not None else [types.Part(text=text)]))],
        usage_metadata=usage)


class BalthasarResponseTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.getaddrinfo', 'httpx.Client.send'):
            guard = patch(name, side_effect=AssertionError('Offline tests only'))
            guard.start(); self.addCleanup(guard.stop)

    def check(self, response, failure=None):
        result, diagnostic = response_result(response, 1)
        self.assertEqual(diagnostic.failure_category, failure)
        if failure:
            self.assertEqual(result.availability, Availability.UNAVAILABLE)
            self.assertEqual(result.error, 'invalid_response')
            self.assertIsNone(result.position)
            self.assertEqual(result.confidence, 0)
            self.assertFalse(result.vote_eligible)
        else:
            self.assertEqual(result.availability, Availability.AVAILABLE)
            self.assertEqual((diagnostic.extraction_validation, diagnostic.json_validation,
                              diagnostic.schema_validation), ('PASS', 'PASS', 'PASS'))
        return result, diagnostic

    def test_stop_valid(self): self.check(message())
    def test_whitespace(self): self.check(message(' \n'+TEXT+'\t'))
    def test_invalid_json(self): self.check(message('{bad}'), 'invalid_json')
    def test_invalid_schema(self): self.check(message('[]'), 'invalid_schema')

    def test_max_tokens_truncated(self):
        self.check(message(TEXT[:-3], types.FinishReason.MAX_TOKENS), 'incomplete_response')

    def test_max_tokens_valid_never_parsed(self):
        with patch('magi.balthasar_response.json.loads', side_effect=AssertionError('Must not parse')):
            _, d = self.check(message(finish=types.FinishReason.MAX_TOKENS), 'incomplete_response')
        self.assertEqual(d.json_validation, 'NOT_ATTEMPTED')

    def test_safety_empty(self):
        self.check(message('', types.FinishReason.SAFETY), 'blocked_response')

    def test_safety_valid(self):
        self.check(message(finish=types.FinishReason.SAFETY), 'blocked_response')

    def test_missing_finish(self): self.check(message(finish=None), 'invalid_completion_metadata')

    def test_unknown_finish_redacted(self):
        # model_construct deliberately bypasses SDK coercion for malformed metadata.
        candidate = types.Candidate.model_construct(finish_reason='SENSITIVE_UNKNOWN',
            content=types.Content(parts=[types.Part(text=TEXT)]))
        _, d = self.check(types.GenerateContentResponse(candidates=[candidate]), 'invalid_completion_metadata')
        self.assertEqual(d.finish_reason, 'UNKNOWN')
        self.assertNotIn('SENSITIVE_UNKNOWN', repr(d))

    def test_unspecified_finish(self):
        self.check(message(finish=types.FinishReason.FINISH_REASON_UNSPECIFIED), 'invalid_completion_metadata')

    def test_all_other_finish_reasons(self):
        blocked = {'SAFETY','RECITATION','BLOCKLIST','PROHIBITED_CONTENT','SPII','IMAGE_SAFETY','IMAGE_PROHIBITED_CONTENT'}
        for finish in types.FinishReason:
            if finish.value in ('STOP','MAX_TOKENS','FINISH_REASON_UNSPECIFIED'): continue
            with self.subTest(finish=finish):
                self.check(message(finish=finish), 'blocked_response' if finish.value in blocked else 'unsupported_completion')

    def test_no_candidates(self): self.check(types.GenerateContentResponse(), 'missing_candidate')
    def test_empty_candidates(self): self.check(types.GenerateContentResponse(candidates=[]), 'missing_candidate')

    def test_multiple_candidates(self):
        self.check(types.GenerateContentResponse(candidates=message().candidates*2), 'unsupported_candidate')

    def test_malformed_candidates(self):
        for candidates in (42, {}, [None], [SimpleNamespace(finish_reason='STOP')]):
            with self.subTest(kind=type(candidates).__name__):
                self.check(types.GenerateContentResponse.model_construct(candidates=candidates), 'unsupported_candidate')
        self.check(SimpleNamespace(text=TEXT), 'unsupported_candidate')

    def test_missing_content(self):
        self.check(types.GenerateContentResponse(candidates=[types.Candidate(finish_reason='STOP')]), 'unsupported_content')

    def test_missing_parts(self):
        self.check(types.GenerateContentResponse(candidates=[types.Candidate(finish_reason='STOP',
            content=types.Content())]), 'unsupported_content')

    def test_empty_text(self): self.check(message(''), 'empty_response')
    def test_empty_parts(self): self.check(message(parts=[]), 'empty_response')

    def test_thought_only(self):
        _, d = self.check(message(parts=[types.Part(text=TEXT, thought=True)]), 'empty_response')
        self.assertEqual(d.thought_part_count, 1); self.assertFalse(d.text_present)

    def test_thought_plus_answer(self):
        _, d = self.check(message(parts=[types.Part(text='Hidden synthetic thought', thought=True,
            thought_signature=b'synthetic signature'), types.Part(text=TEXT)]))
        self.assertEqual(d.content_parts, (('text', 1), ('thought', 1)))

    def test_nontext(self):
        self.check(message(parts=[types.Part(function_call=types.FunctionCall(name='fake', args={}))]), 'unsupported_content')

    def test_mixed_nontext(self):
        for parts in ([types.Part(text=TEXT), types.Part(inline_data=types.Blob(data=b'fake', mime_type='image/png'))],
                      [types.Part(text=TEXT, function_call=types.FunctionCall(name='fake',args={}))]):
            with self.subTest(): self.check(message(parts=parts), 'unsupported_content')

    def test_multiple_text_parts(self):
        result, _ = self.check(message(parts=[types.Part(text=TEXT[:25]), types.Part(text=TEXT[25:])]))
        self.assertEqual(result.reasoning, DATA['reasoning'])

    def test_two_documents(self):
        self.check(message(parts=[types.Part(text=TEXT), types.Part(text=TEXT)]), 'invalid_json')

    def test_fences(self): self.check(message('```json\n'+TEXT+'\n```'), 'invalid_json')
    def test_preamble(self): self.check(message('Answer: '+TEXT), 'invalid_json')
    def test_trailing(self): self.check(message(TEXT+' End.'), 'invalid_json')
    def test_duplicate_keys(self): self.check(message(TEXT[:-1]+',"position":"BUY"}'), 'invalid_json')

    def test_missing_fields(self):
        for key in DATA:
            with self.subTest(key=key):self.check(message(json.dumps({k:v for k,v in DATA.items() if k!=key})), 'invalid_schema')

    def test_invalid_position(self):
        self.check(message(json.dumps(dict(DATA, position='MAYBE'))), 'invalid_schema')

    def test_invalid_confidence(self):
        for value in ('0.5', True, None, [], -0.1, 1.1):
            with self.subTest(value=value):self.check(message(json.dumps(dict(DATA, confidence=value))), 'invalid_schema')

    def test_nonstandard_numbers_anywhere(self):
        for value in ('NaN','Infinity','-Infinity','1e999'):
            with self.subTest(value=value):self.check(message(TEXT[:-1]+',"unused":'+value+'}'), 'invalid_json')

    def test_unicode_and_escaped_controls(self):
        result, _ = self.check(message(json.dumps(dict(DATA, reasoning='가상 분석\n다음\t항목'),ensure_ascii=False)))
        self.assertEqual(result.reasoning, '가상 분석\n다음\t항목')

    def test_invalid_reasoning_and_lists(self):
        for kw in ({'reasoning':''},{'key_risks':'risk'},{'key_risks':['']},{'evidence_gaps':[1]}):
            with self.subTest():self.check(message(json.dumps(dict(DATA,**kw))), 'invalid_schema')

    def test_missing_usage(self):
        _, d = self.check(message())
        self.assertEqual((d.output_tokens,d.thought_tokens,d.total_tokens,d.max_output_tokens),(None,)*4)

    def test_usage_zero_and_invalid(self):
        usage=types.GenerateContentResponseUsageMetadata.model_construct(candidates_token_count=0,
            thoughts_token_count=True,total_token_count=-1)
        _, d=self.check(message(usage=usage))
        self.assertEqual((d.output_tokens,d.thought_tokens,d.total_tokens),(0,None,None))

    def test_safe_immutable_diagnostics(self):
        response=message(parts=[types.Part(text='PRIVATE_THOUGHT',thought=True,thought_signature=b'PRIVATE_SIGNATURE'),
            types.Part(text=json.dumps(dict(DATA,reasoning='PRIVATE_RESPONSE')))],
            usage=types.GenerateContentResponseUsageMetadata(candidates_token_count=100,thoughts_token_count=50,total_token_count=300))
        _, d=self.check(response)
        serialized=json.dumps(asdict(d))
        for value in ('PRIVATE_THOUGHT','PRIVATE_SIGNATURE','PRIVATE_RESPONSE'):
            self.assertNotIn(value,serialized)
        self.assertEqual((d.output_tokens,d.thought_tokens,d.total_tokens),(100,50,300))
        with self.assertRaises(FrozenInstanceError):d.attempts=4

    def test_prompt_block_feedback(self):
        for response in (types.GenerateContentResponse(),message()):
            response.prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason.SAFETY)
            self.check(response,'blocked_response')

    def test_malformed_parts(self):
        for parts in (42,[None],[types.Part.model_construct(text=42)], [types.Part()],
                      [types.Part.model_construct(text=TEXT,thought='yes')]):
            response=message();response.candidates[0].content=types.Content.model_construct(parts=parts)
            with self.subTest():self.check(response,'unsupported_content')

    def invoke(self, response=None, error=None):
        request=Mock(return_value=response,side_effect=error)
        client=SimpleNamespace(models=SimpleNamespace(generate_content=request))
        with patch('magi.balthasar.genai.Client',return_value=client) as constructor,patch('magi.provider.time.sleep') as sleep:
            agent=Balthasar();result=agent.think('Synthetic question')
        return agent,result,request,constructor,sleep

    def test_request_configuration_and_no_extra_calls(self):
        agent,r,request,constructor,_=self.invoke(message())
        self.assertEqual(r.availability,Availability.AVAILABLE)
        request.assert_called_once()
        kwargs=request.call_args.kwargs
        self.assertEqual(set(kwargs),{'model','config','contents'})
        self.assertEqual(kwargs['model'],'gemini-2.5-flash')
        self.assertEqual(set(kwargs['config']),{'system_instruction'})
        self.assertEqual(constructor.call_args.kwargs['http_options'],{'timeout':30000,'retry_options':{'attempts':1}})
        self.assertEqual(agent.last_response_diagnostic.attempts,1)

    def test_provider_timeout(self): self.provider_error(httpx.ReadTimeout('PRIVATE_ERROR'),3,'transient_failure_exhausted')
    def test_provider_connection(self): self.provider_error(httpx.ConnectError('PRIVATE_ERROR'),3,'transient_failure_exhausted')
    def test_retryable_http(self): self.provider_error(status_error('Gemini',503),3,'transient_failure_exhausted')
    def test_nonretryable_http(self): self.provider_error(status_error('Gemini',400),1,'non_retryable_provider_error')

    def provider_error(self,error,attempts,reason):
        agent,r,request,_,sleep=self.invoke(error=error)
        self.assertEqual(request.call_count,attempts);self.assertEqual(r.attempts,attempts)
        self.assertEqual(r.error,'provider_unavailable:'+reason)
        self.assertFalse(r.vote_eligible)
        self.assertEqual(agent.last_response_diagnostic.failure_category,'provider_failure')
        self.assertEqual(agent.last_response_diagnostic.attempts,attempts)
        self.assertNotIn('PRIVATE_ERROR',repr(agent.last_response_diagnostic))
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[0.5,1.0] if attempts==3 else [])

    def test_invalid_response_not_retried_and_diagnostic_reset(self):
        client=Mock();client.models.generate_content.side_effect=[message(),message(finish='MAX_TOKENS')]
        with patch('magi.balthasar.genai.Client',return_value=client):
            a=Balthasar();a.think('Synthetic');a.think('Synthetic')
        self.assertEqual(client.models.generate_content.call_count,2)
        self.assertEqual(a.last_response_diagnostic.attempts,1)
        self.assertEqual(a.last_response_diagnostic.failure_category,'incomplete_response')

    def test_sdk_unknown_enum_and_malformed_feedback(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            response = message(finish='PRIVATE_UNKNOWN_FINISH')
        _, d = self.check(response, 'invalid_completion_metadata')
        self.assertEqual(d.finish_reason, 'UNKNOWN')
        response = message()
        response.prompt_feedback = 'PRIVATE_BAD_FEEDBACK'
        _, d = self.check(response, 'blocked_response')
        self.assertEqual(d.prompt_block_reason, 'UNKNOWN')
        self.assertNotIn('PRIVATE_BAD_FEEDBACK', repr(d))

    def test_success_after_transient_failure_counts_attempts(self):
        agent, r, request, _, sleep = self.invoke(error=[httpx.ReadTimeout('PRIVATE'), message()])
        self.assertEqual(r.availability, Availability.AVAILABLE)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(agent.last_response_diagnostic.attempts, 2)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5])

    def test_invalid_cannot_vote(self):
        b,_=self.check(message(finish='MAX_TOKENS'),'incomplete_response')
        others={name:unavailable_result(name,'fake','fake','offline') for name in ('Melchior','Casper')}
        v=VotingEngine().vote(dict(others,Balthasar=b))
        self.assertEqual(v.final_action,FinalAction.INSUFFICIENT_PARTICIPATION)
        self.assertEqual(v.eligible_voters,())


if __name__ == '__main__': unittest.main()
