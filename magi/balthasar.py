import os
from pathlib import Path
from urllib import response

from dotenv import load_dotenv
from google import genai

class Balthasar:
    def __init__(self):
        load_dotenv()

        self.name = "Balthasar"
        self.client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY")
        )
        with open(
            Path(__file__).parent.parent / "prompts" / "balthasar.txt",
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

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        return f"[{self.name}]\n{response.text}"