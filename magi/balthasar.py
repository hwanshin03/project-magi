import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from magi.provider import TIMEOUT_SECONDS, call_provider
from magi.history import agent_request
from magi.decision import parse_decision, provider_failure

class Balthasar:
    provider = "Gemini"
    model = "gemini-2.5-flash"

    def __init__(self):
        load_dotenv()

        self.historical_context = ''
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

    def think(self, question):

        prompt, instructions = agent_request(self.persona, question, self.historical_context)

        response = call_provider(self.provider, lambda: self.client.models.generate_content(
            model=self.model,
            config={"system_instruction": instructions},
            contents=prompt
        ))

        failure = provider_failure(response, self.name, self.provider, self.model)
        if failure is not None:
            return failure

        text = getattr(response, "text", None)
        return parse_decision(text, self.name, self.provider, self.model)
