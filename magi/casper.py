from email.mime import message
import os
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic


load_dotenv()


class Casper:
    def __init__(self):
        self.name = "Casper"

        self.client = Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        with open(
            Path(__file__).parent.parent / "prompts" / "casper.txt",
            "r",
            encoding="utf-8"
        ) as f:
            self.persona = f.read()


    def think(self, question):

        prompt = f"""
    {self.persona}

    Question:

    {question}
    """

        message = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        return f"[{self.name}]\n{message.content[0].text}"
