class ConsensusEngine:
    def __init__(self):
        self.name = "Consensus Engine"

    def decide(self, responses):
        print()
        print("Consensus")
        print("-" * 40)

        for agent_name, response in responses.items():
            print(f"{agent_name}: reviewed")

        print()
        return "Final consensus: All three agents have reviewed the question."