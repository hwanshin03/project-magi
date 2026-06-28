from magi.melchior import Melchior
from magi.balthasar import Balthasar
from magi.casper import Casper
from magi.consensus import ConsensusEngine


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

    melchior = Melchior()
    balthasar = Balthasar()
    casper = Casper()
    consensus = ConsensusEngine()
    
    question = input("Question > ")

    responses = {
        "Melchior": melchior.think(question),
        "Balthasar": balthasar.think(question),
        "Casper": casper.think(question)
    }

    for response in responses.values():
        print(response)
    
    final_answer = consensus.decide(responses)
    print(final_answer)


if __name__ == "__main__":
    main()