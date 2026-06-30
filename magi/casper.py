import os

from dotenv import load_dotenv
from anthropic import Anthropic


load_dotenv()


class Casper:
    def __init__(self):
        self.name = "Casper"

        self.client = Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )

    def think(self, question):
        message = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": question
                }
            ]
        )

        return f"[{self.name}]\n{message.content[0].text}"
