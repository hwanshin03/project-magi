import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from magi.provider import TIMEOUT_SECONDS, call_provider
from magi.history import agent_request
from magi.decision import provider_failure
from magi.balthasar_response import MODEL, response_result

class Balthasar:
    provider = "Gemini"
    model = MODEL

    def __init__(self):
        load_dotenv()

        self.historical_context = ''
        self.last_response_diagnostic = None
        self.name = "Balthasar"
        self.client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options={
                "timeout": TIMEOUT_SECONDS * 1000,
                "retry_options": {"attempts": 1},
            },
        )
        with open(
            Path(__file__).parent.parent / "prompts" / "balthasar.txt",
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
            return self.client.models.generate_content(
                model=self.model,
                config={"system_instruction": instructions},
                contents=prompt
            )

        response = call_provider(self.provider, request)

        failure = provider_failure(response, self.name, self.provider, self.model)
        result, self.last_response_diagnostic = response_result(response, attempts, provider_failure=failure)
        return result
