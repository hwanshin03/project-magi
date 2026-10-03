import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from magi.provider import TIMEOUT_SECONDS, call_provider
from magi.history import agent_request
from magi.decision import parse_decision, provider_failure


load_dotenv()


class Melchior:
    provider = "OpenAI"
    model = "gpt-5.5"

    def __init__(self):
        self.historical_context = ''
        self.name = "Melchior"

        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=TIMEOUT_SECONDS,
            max_retries=0,
        )
        with open(
            Path(__file__).parent.parent / "prompts" / "melchior.txt",
            "r",
            encoding="utf-8"
        ) as f:
            self.persona = f.read()
    
    def think(self, question, *, research_selection=None):

        prompt, instructions = agent_request(self.persona, question, self.historical_context,
                                             research_selection=research_selection, agent_name=self.name)

        result = call_provider(self.provider, lambda: self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=prompt
        ))

        failure = provider_failure(result, self.name, self.provider, self.model)
        if failure is not None:
            return failure

        text = getattr(result, "output_text", None)
        return parse_decision(text, self.name, self.provider, self.model)
