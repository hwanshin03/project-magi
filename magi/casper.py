import os
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic
from magi.provider import TIMEOUT_SECONDS, call_provider
from magi.history import agent_request
from magi.decision import parse_decision, provider_failure


load_dotenv()


class Casper:
    provider = "Anthropic"
    model = "claude-sonnet-4-6"

    def __init__(self):
        self.historical_context = ''
        self.name = "Casper"

        self.client = Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            timeout=TIMEOUT_SECONDS,
            max_retries=0,
        )
        with open(
            Path(__file__).parent.parent / "prompts" / "casper.txt",
            "r",
            encoding="utf-8"
        ) as f:
            self.persona = f.read()


    def think(self, question):

        prompt, instructions = agent_request(self.persona, question, self.historical_context)

        message = call_provider(self.provider, lambda: self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=instructions,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        ))

        failure = provider_failure(message, self.name, self.provider, self.model)
        if failure is not None:
            return failure

        text = "".join(getattr(block, "text", "") for block in (getattr(message, "content", None) or []))
        return parse_decision(text, self.name, self.provider, self.model)
