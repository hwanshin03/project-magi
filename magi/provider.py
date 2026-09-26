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

    def __str__(self):
        text = (
            f"[{self.status}] provider={self.provider}; attempts={self.attempts}; "
            f"reason={self.reason}"
        )
        if self.status_code is not None:
            text += f"; http_status={self.status_code}"
        return text


def call_provider(provider, request):
    """Try a request three times at most; never include exception bodies/secrets."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return request()
        except PROVIDER_ERRORS as error:
            status = getattr(error, "status_code", None)
            if status is None:
                status = getattr(error, "code", None)
            transient = status in RETRY_STATUSES or isinstance(error, NETWORK_ERRORS)
            if transient and attempt < MAX_ATTEMPTS:
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            return ProviderUnavailable(
                provider=provider,
                attempts=attempt,
                reason="transient_failure_exhausted" if transient else "non_retryable_provider_error",
                status_code=status,
            )
