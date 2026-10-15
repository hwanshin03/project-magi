"""Anthropic completion checks and bounded, text-free in-memory diagnostics."""
from collections import Counter
from dataclasses import dataclass
import json

from magi.decision import _unique_object, parse_decision, unavailable_result

MODEL = 'claude-sonnet-4-6'
MAX_TOKENS = 3072
_STOPS = ('end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn',
          'refusal', 'model_context_window_exceeded')
_BLOCKS = ('text', 'tool_use', 'thinking', 'redacted_thinking')


def _category(value, allowed):
    if value is None: return 'MISSING'
    return value if type(value) is str and value in allowed else 'UNKNOWN'


@dataclass(frozen=True)
class CasperDiagnostic:
    stop_reason: str
    output_tokens: int | None
    content_blocks: tuple
    failure_category: str | None
    attempts: int
    provider: str = 'Anthropic'
    model: str = MODEL
    max_tokens: int = MAX_TOKENS


def response_result(message, attempts, *, provider_failure=None):
    """Never retain response content or arbitrary provider strings in diagnostics.

    Only a natural completed text response is eligible for strict parsing.
    AgentResult's existing public failure contract remains unchanged.
    """
    stop = _category(getattr(message, 'stop_reason', None), _STOPS)
    usage = getattr(getattr(message, 'usage', None), 'output_tokens', None)
    tokens = usage if type(usage) is int and 0 <= usage <= MAX_TOKENS else None
    blocks = getattr(message, 'content', None)
    valid_container = isinstance(blocks, (list, tuple))
    counts = Counter(_category(getattr(b, 'type', None), _BLOCKS) for b in blocks) if valid_container else Counter()

    def finish(category, result=None):
        diagnostic = CasperDiagnostic(stop, tokens, tuple(sorted(counts.items())), category, attempts)
        if result is None:
            result = unavailable_result('Casper', 'Anthropic', MODEL, 'invalid_response')
        return result, diagnostic

    if provider_failure is not None: return finish('provider_failure', provider_failure)
    if stop in ('max_tokens', 'pause_turn', 'model_context_window_exceeded'):
        return finish('incomplete_response')
    if stop in ('MISSING', 'UNKNOWN'): return finish('invalid_completion_metadata')
    if stop != 'end_turn': return finish('unsupported_completion')
    if not valid_container: return finish('unsupported_content_blocks')
    if any(getattr(b, 'type', None) != 'text' or not isinstance(getattr(b, 'text', None), str) for b in blocks):
        return finish('unsupported_content_blocks')
    text = ''.join(b.text for b in blocks)
    if not text.strip(): return finish('empty_response')
    try:
        # Use the same duplicate-key rejection as the shared strict parser.
        json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return finish('json_decoding_failure')
    result = parse_decision(text, 'Casper', 'Anthropic', MODEL)
    return finish('schema_validation_failure' if result.error else None, result)
