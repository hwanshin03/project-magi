"""Gemini-only completion checks and immutable, content-free diagnostics."""
from collections import Counter
from dataclasses import dataclass
import json
import math

from google.genai import types
from magi.decision import _unique_object, parse_decision, unavailable_result

MODEL = 'gemini-2.5-flash'
_BLOCKED = frozenset((types.FinishReason.SAFETY, types.FinishReason.RECITATION,
    types.FinishReason.BLOCKLIST, types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.SPII, types.FinishReason.IMAGE_SAFETY,
    types.FinishReason.IMAGE_PROHIBITED_CONTENT))


def _category(value, enum):
    if value is None: return 'MISSING'
    return value.value if isinstance(value, enum) and value in tuple(enum) else 'UNKNOWN'


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**63-1 else None


@dataclass(frozen=True)
class BalthasarDiagnostic:
    finish_reason: str
    prompt_block_reason: str
    candidate_count: int | None
    content_parts: tuple
    thought_part_count: int
    text_present: bool
    output_tokens: int | None
    thought_tokens: int | None
    total_tokens: int | None
    extraction_validation: str
    json_validation: str
    schema_validation: str
    failure_category: str | None
    attempts: int
    provider: str = 'Gemini'
    model: str = MODEL
    max_output_tokens: int | None = None  # Unconfigured, not zero or a server default.


def _part_kind(part):
    if not isinstance(part, types.Part): return 'unsupported'
    # Signatures are opaque metadata: never decode, copy, or retain them.
    if any(getattr(part, name) is not None for name in types.Part.model_fields
           if name not in ('text', 'thought', 'thought_signature')):
        return 'unsupported'
    if part.thought is not None and type(part.thought) is not bool: return 'unsupported'
    if part.text is not None and type(part.text) is not str: return 'unsupported'
    if part.thought is True: return 'thought'
    return 'text' if part.text is not None else 'missing_text'


def _invalid_number(_):
    raise ValueError('Nonstandard JSON number')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number): _invalid_number(value)
    return number


def response_result(response, attempts, *, provider_failure=None):
    """Only one STOP candidate can reach strict JSON and existing schema validation.

    STOP includes natural stopping and configured stop sequences in the SDK.
    Thought text is ignored; ordinary text parts concatenate in original order.
    Diagnostics never retain any response, prompt, signature, or exception text.
    """
    candidates = getattr(response, 'candidates', None)
    candidate_count = len(candidates) if isinstance(candidates, list) else None
    candidate = candidates[0] if candidate_count == 1 else None
    candidate = candidate if isinstance(candidate, types.Candidate) else None
    stop = _category(candidate.finish_reason if candidate else None, types.FinishReason)
    feedback = getattr(response, 'prompt_feedback', None)
    block = ('MISSING' if feedback is None else
             _category(feedback.block_reason, types.BlockedReason)
             if isinstance(feedback, types.GenerateContentResponsePromptFeedback) else 'UNKNOWN')
    content = candidate.content if candidate else None
    parts = content.parts if isinstance(content, types.Content) else None
    kinds = tuple(_part_kind(p) for p in parts) if isinstance(parts, list) else ()
    counts = Counter(kinds)
    text_present = any(kind == 'text' and bool(part.text.strip()) for kind, part in zip(kinds, parts if isinstance(parts, list) else ()))
    usage = getattr(response, 'usage_metadata', None)
    tokens = tuple(_count(getattr(usage, name, None)) for name in
                   ('candidates_token_count', 'thoughts_token_count', 'total_token_count'))
    extraction = json_status = schema = 'NOT_ATTEMPTED'

    def finish(category, result=None):
        diagnostic = BalthasarDiagnostic(stop, block, candidate_count, tuple(sorted(counts.items())),
            counts['thought'], text_present, *tokens, extraction, json_status, schema, category, attempts)
        if result is None:
            result = unavailable_result('Balthasar', 'Gemini', MODEL, 'invalid_response')
        return result, diagnostic

    if provider_failure is not None: return finish('provider_failure', provider_failure)
    if not isinstance(response, types.GenerateContentResponse): return finish('unsupported_candidate')
    if block not in ('MISSING', 'BLOCKED_REASON_UNSPECIFIED'): return finish('blocked_response')
    if candidates is None or candidates == []: return finish('missing_candidate')
    if candidate_count != 1 or candidate is None: return finish('unsupported_candidate')
    if stop == types.FinishReason.MAX_TOKENS: return finish('incomplete_response')
    if stop in _BLOCKED: return finish('blocked_response')
    if stop in ('MISSING', 'UNKNOWN', 'FINISH_REASON_UNSPECIFIED'): return finish('invalid_completion_metadata')
    if stop != types.FinishReason.STOP: return finish('unsupported_completion')
    extraction = 'FAIL'
    if not isinstance(parts, list) or counts['unsupported'] or counts['missing_text']:
        return finish('unsupported_content')
    text = ''.join(part.text for kind, part in zip(kinds, parts) if kind == 'text')
    if not text.strip(): return finish('empty_response')
    extraction = 'PASS'
    json_status = 'FAIL'
    try:
        json.loads(text, object_pairs_hook=_unique_object,
                   parse_constant=_invalid_number, parse_float=_finite_float)
    except (ValueError, RecursionError):
        return finish('invalid_json')
    json_status = 'PASS'
    result = parse_decision(text, 'Balthasar', 'Gemini', MODEL)
    schema = 'FAIL' if result.error else 'PASS'
    return finish('invalid_schema' if result.error else None, result)
