import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class ConsensusEngine:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )

    def decide(self, responses):

        prompt = f"""
You are the MAGI Consensus Engine.

Three AI agents answered the same question.

Melchior:
{responses["Melchior"]}

Balthasar:
{responses["Balthasar"]}

Casper:
{responses["Casper"]}

Your job is to:

1. Compare all three answers.
2. Point out where they agree.
3. Point out where they disagree.
4. Produce ONE final conclusion.

Format:

Consensus Summary

Agreements
- ...

Disagreements
- ...

Final Decision
...
"""

        result = self.client.responses.create(
            model="gpt-5.5",
            input=prompt
        )

        return result.output_text