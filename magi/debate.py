class DebateEngine:
    def __init__(self):
        self.name = "Debate Engine"

    def challenge(self, agent, question, responses, round_number):
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
4. Decide whether your position changed.
5. Produce your revised final position.

Format your answer exactly like this:

[{agent.name} Debate Review - Round {round_number}]

Stronger Points From Other Agents:
- ...

Remaining Disagreements:
- ...

Changed Position:
YES or NO

Revised Final Position:
...
"""

        return agent.think(debate_prompt)

    def run_round(self, agents, question, current_responses, round_number):
        next_responses = {}

        for agent in agents:
            next_responses[agent.name] = self.challenge(
                agent=agent,
                question=question,
                responses=current_responses,
                round_number=round_number
            )

        return next_responses

    def run(self, agents, question, initial_responses, rounds=3):
        all_rounds = []
        current_responses = initial_responses

        for round_number in range(1, rounds + 1):
            current_responses = self.run_round(
                agents=agents,
                question=question,
                current_responses=current_responses,
                round_number=round_number
            )

            all_rounds.append({
                "round": round_number,
                "responses": current_responses
            })

        return all_rounds

