from magi.melchior import Melchior
from magi.balthasar import Balthasar
from magi.casper import Casper
from magi.consensus import ConsensusEngine
from magi.debate import DebateEngine


def boot():
    print("Project MAGI started")
    print("Melchior : OpenAI")
    print("Balthasar: Gemini")
    print("Casper   : Claude")
    print()
    print("Consensus Engine Ready")
    print("-" * 40)


def main():
    boot()

    question = input("Question: ")

    melchior = Melchior()
    balthasar = Balthasar()
    casper = Casper()

    consensus = ConsensusEngine()
    debate = DebateEngine()

    responses = {
        "Melchior": melchior.think(question),
        "Balthasar": balthasar.think(question),
        "Casper": casper.think(question),
    }

    print("\n==============================")
    print("INITIAL RESPONSES")
    print("==============================\n")

    for name, response in responses.items():
        print(f"[{name}]")
        print(response)
        print("-" * 60)

    debate_rounds = debate.run(
        agents=[melchior, balthasar, casper],
        question=question,
        initial_responses=responses,
        rounds=3,
    )

    for debate_round in debate_rounds:
        print("\n==============================")
        print(f"DEBATE ROUND {debate_round['round']}")
        print("==============================\n")

        for name, response in debate_round["responses"].items():
            print(f"[{name}]")
            print(response)
            print("-" * 60)

    final_debate_responses = debate_rounds[-1]["responses"]

    print("\n==============================")
    print("FINAL CONSENSUS")
    print("==============================\n")

    final_answer = consensus.decide(final_debate_responses)
    print(final_answer)


if __name__ == "__main__":
    main()

