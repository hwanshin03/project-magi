import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


class Melchior:
    def __init__(self):
        self.name = "Melchior"

        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )
        with open(
            Path(__file__).parent.parent / "prompts" / "melchior.txt",
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

        result = self.client.responses.create(
            model="gpt-5.5",
            input=prompt
        )

        return f"[{self.name}]\n{result.output_text}"
