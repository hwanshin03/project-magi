from magi.melchior import Melchior
from magi.balthasar import Balthasar
from magi.casper import Casper


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

    question = input("Question > ")

    print(melchior.think(question))
    print(balthasar.think(question))
    print(casper.think(question))


if __name__ == "__main__":
    main()