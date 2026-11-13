"""Shared request retries and explicit, printable provider failures."""

import time
from dataclasses import dataclass
from typing import Optional

import anthropic
import httpx
import openai
from google.genai.errors import APIError as GeminiAPIError


TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}
NETWORK_ERRORS = (
    openai.APIConnectionError,
    anthropic.APIConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    TimeoutError,
    ConnectionError,
)
PROVIDER_ERRORS = (openai.APIError, anthropic.APIError, GeminiAPIError) + NETWORK_ERRORS


@dataclass(frozen=True)
class ProviderUnavailable:
    provider: str
    attempts: int
    reason: str
    status_code: Optional[int] = None
    status: str = "provider_unavailable"
    diagnostic_code: Optional[str] = None

    def __str__(self):
        text = (
            f"[{self.status}] provider={self.provider}; attempts={self.attempts}; "
            f"reason={self.reason}"
        )
        if self.status_code is not None:
            text += f"; http_status={self.status_code}"
        return text


def call_provider(provider, request, *, max_attempts=MAX_ATTEMPTS):
    """Allow callers to lower the retry bound; never expose exception contents."""
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError('Invalid provider attempt limit')
    for attempt in range(1, max_attempts + 1):
        try:
            return request()
        except PROVIDER_ERRORS as error:
            status = getattr(error, "status_code", None)
            if status is None:
                status = getattr(error, "code", None)
            # SDK metadata is untrusted: validate before hashing/comparing it.
            status = status if type(status) is int and 100 <= status <= 599 else None
            transient = status in RETRY_STATUSES or isinstance(error, NETWORK_ERRORS)
            if transient and attempt < max_attempts:
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            return ProviderUnavailable(
                provider=provider,
                attempts=attempt,
                reason="transient_failure_exhausted" if transient else "non_retryable_provider_error",
                status_code=status,
                diagnostic_code=_failure_code(error, status),
            )


def _failure_code(error, status):
    """Classify observed types/status only; never inspect exception text or URLs."""
    if isinstance(error, (openai.APITimeoutError, anthropic.APITimeoutError,
                          httpx.TimeoutException, TimeoutError)):
        return 'provider_timeout'
    if isinstance(error, NETWORK_ERRORS): return 'provider_transport_error'
    if type(status) is int:
        if status in (401, 403): return 'provider_authentication_failed'
        if status == 429: return 'provider_rate_limited'
        if status == 503: return 'provider_service_unavailable'
        if 500 <= status <= 599: return 'provider_server_error'
    return 'provider_error'
