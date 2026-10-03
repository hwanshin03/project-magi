from magi.decision import reconcile_result


class DebateEngine:
    def __init__(self):
        self.name = "Debate Engine"

    def challenge(self, agent, question, responses, round_number, *, research_selection=None):
        your_answer = responses[agent.name]

        other_answers = ""

        for agent_name, response in responses.items():
            if agent_name != agent.name:
                other_answers += f"\n\n[{agent_name}]\n{response}"

        debate_prompt = f"""
You are {agent.name}, one of three AI agents in Project MAGI.

This is debate round {round_number}.

Original question:
{question}

Your previous position:
{your_answer}

Other agents' current positions:
{other_answers}

Your task:
1. Review the other agents' arguments.
2. Identify stronger points from other agents.
3. Identify remaining disagreements.
4. You may change your position after reviewing the others, or keep it.
5. Produce your revised position, confidence, full reasoning, risks, and evidence gaps.
Include stronger points and remaining disagreements in your reasoning.
STALE responses are earlier answers, not fresh endorsements. UNAVAILABLE responses
contain no position. Do not interpret missing answers as agreement or disagreement.
The application computes changed_position by comparing validated positions.

"""

        if research_selection is not None:
            return agent.think(debate_prompt, research_selection=research_selection)
        return agent.think(debate_prompt)

    def run_round(self, agents, question, current_responses, round_number, *, research_selection=None):
        next_responses = {}

        for agent in agents:
            result = self.challenge(
                agent=agent,
                question=question,
                responses=current_responses,
                round_number=round_number,
                research_selection=research_selection
            )
            next_responses[agent.name] = reconcile_result(result, current_responses[agent.name])

        return next_responses

    def run(self, agents, question, initial_responses, rounds=3, *, research_selection=None):
        all_rounds = []
        current_responses = initial_responses

        for round_number in range(1, rounds + 1):
            current_responses = self.run_round(
                agents=agents,
                question=question,
                current_responses=current_responses,
                round_number=round_number,
                research_selection=research_selection
            )

            all_rounds.append({
                "round": round_number,
                "responses": current_responses
            })

        return all_rounds
