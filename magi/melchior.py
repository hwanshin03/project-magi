import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class Melchior:
    def __init__(self):
        self.name = "Melchior"

        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )

    def think(self, question):
        response = self.client.responses.create(
            model="gpt-5.5",
            input=question
        )

        return f"[{self.name}]\n{response.output_text}"