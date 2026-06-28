import os

from dotenv import load_dotenv
from google import genai

class Balthasar:
    def __init__(self):
        load_dotenv()

        self.name = "Balthasar"
        self.client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY")
        )

    def think(self, question):
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=question
        )
       
        return f"[{self.name}]\n{response.text}"