import os
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic
import httpx
from magi.provider import TIMEOUT_SECONDS, call_provider
from magi.history import agent_request
from magi.decision import provider_failure
from magi.casper_response import MODEL, MAX_TOKENS, response_result


load_dotenv()


class Casper:
    provider = "Anthropic"
    model = MODEL

    def __init__(self):
        self.historical_context = ''
        self.last_response_diagnostic = None
        self.name = "Casper"

        self.client = Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            timeout=httpx.Timeout(TIMEOUT_SECONDS, read=60),
            max_retries=0,
        )
        with open(
            Path(__file__).parent.parent / "prompts" / "casper.txt",
            "r",
            encoding="utf-8"
        ) as f:
            self.persona = f.read()


    def think(self, question, *, research_selection=None):

        self.last_response_diagnostic = None
        prompt, instructions = agent_request(self.persona, question, self.historical_context,
                                             research_selection=research_selection, agent_name=self.name)
        attempts = 0

        def request():
            nonlocal attempts
            attempts += 1
            return self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=instructions,
                messages=[{"role": "user", "content": prompt}],
            )

        message = call_provider(self.provider, request, max_attempts=1)
        failure = provider_failure(message, self.name, self.provider, self.model)
        result, self.last_response_diagnostic = response_result(message, attempts, provider_failure=failure)
        return result
