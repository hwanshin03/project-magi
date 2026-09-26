import os

from dotenv import load_dotenv
from openai import OpenAI
from magi.provider import ProviderUnavailable, TIMEOUT_SECONDS, call_provider
from magi.decision import Availability
from magi.voting import VotingResult

load_dotenv()


class ConsensusEngine:
    """Explain a completed vote. This class cannot select or update its action."""

    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=TIMEOUT_SECONDS,
            max_retries=0,
        )

    def explain(self, question: str, vote: VotingResult):
        if not any(result.has_answer for result in vote.agent_results):
            return ProviderUnavailable("Consensus", 0, "no_agent_answers_available")

        unavailable = [result.agent for result in vote.agent_results
                       if result.availability != Availability.AVAILABLE]
        limitation = ""
        if unavailable:
            limitation = (
                "Provider availability limitation: " + ", ".join(unavailable)
                + " unavailable on the latest request. STALE responses are historical context "
                "and do not vote. UNAVAILABLE results have no usable answer; "
                "do not invent missing positions.\n"
            )

        prompt = f"""
You are the MAGI Consensus Explanation Engine.

Original user question:
{question}

The application has already computed the authoritative, immutable voting result:
{vote}

Your task is explanation ONLY. You cannot override, replace, or change final_action.
Explain why these counts produced {vote.final_action.value}.
Only AVAILABLE, non-ABSTAIN results vote. STRONG_BUY/BUY count as BUY;
STRONG_SELL/SELL count as SELL. HOLD is its own category and means no trade.
Two agreeing votes are required, with one agent equal to one vote.
Fewer than two eligible voters means INSUFFICIENT_PARTICIPATION; otherwise,
without two agreeing votes, the result is NO_CONSENSUS.
Confidence is preserved for context but never changes vote weight.
STALE opinions may inform discussion, but cannot support a counted vote.
ABSTAIN means the agent withheld a recommendation; it is not a HOLD vote.

Explain:
- How the eligible votes and exclusions produced the recorded result.
- Agreements and disagreements, including minority opinions.
- Risks and evidence gaps from the structured agent results.

Return explanatory prose only. Do not output a replacement final action or a
Final Decision section, and do not propose or execute a trade.
"""

        result = call_provider("OpenAI", lambda: self.client.responses.create(
            model="gpt-5.5",
            input=limitation + prompt
        ))
        if isinstance(result, ProviderUnavailable):
            return result
        return limitation + result.output_text
