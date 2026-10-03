from magi.melchior import Melchior
from magi.balthasar import Balthasar
from magi.casper import Casper
from magi.consensus import ConsensusEngine
from magi.debate import DebateEngine
from magi.voting import VotingEngine
from magi.memory import AnalysisMemory
from magi.history import historical_context
from magi.storage import StorageError


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

    memory = None
    history = ''
    try:
        memory = AnalysisMemory()
        history = historical_context(memory.relevant(question))
    except (StorageError, ValueError):
        print("Memory retrieval failed; continuing without historical context.")

    melchior = Melchior()
    balthasar = Balthasar()
    casper = Casper()

    for agent in (melchior, balthasar, casper):
        agent.historical_context = history

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

    vote = VotingEngine().vote(final_debate_responses)
    print("\n=== MAGI VOTE ===\n")
    exclusions = {item.agent: item.reason for item in vote.excluded_agents}
    for result in vote.agent_results:
        position = result.position.value if result.position is not None else "NO POSITION"
        detail = f"{result.availability.value}, confidence={result.confidence:.2f}"
        if result.agent in exclusions:
            detail += f", excluded: {exclusions[result.agent]}"
        print(f"{result.agent}: {position} ({detail})")
    print(f"\nEligible votes:\nBUY: {vote.buy_votes}\nHOLD: {vote.hold_votes}\nSELL: {vote.sell_votes}")
    print(f"Required votes: {vote.required_votes}")
    print(f"\nFINAL ACTION: {vote.final_action.value}", flush=True)

    print("\n=== CONSENSUS EXPLANATION ===")
    print("LLM commentary only; the deterministic action above is authoritative.")
    explanation = consensus.explain(question, vote)
    for line in str(explanation).splitlines():
        print(f"| {line}")
    try:
        if memory is None:
            memory = AnalysisMemory()
        record = memory.save_analysis(question, vote, explanation if isinstance(explanation, str) else None)
        print(f"Analysis saved: {record.run_id}")
    except (StorageError, ValueError):
        print("Memory persistence failed; the MAGI decision remains valid.")
    # Always end with the application-owned action, even if the model contradicts
    # its instructions or the explanation provider is unavailable.
    print(f"\nFINAL ACTION: {vote.final_action.value} (deterministic)")
    return vote


def cli(argv=None):
    import sys
    args = sys.argv[1:] if argv is None else argv
    if not args:
        main()
        return 0
    if args[0] == "analyze":
        from magi.analysis.cli import main as analysis_cli
        return analysis_cli(args[1:])
    if args[0] == "research":
        from magi.research.cli import main as research_cli
        return research_cli(args[1:])
    if args[0] == "broker":
        from magi.broker.cli import main as broker_cli
        return broker_cli(args[1:])
    if args[0] == "market":
        from magi.market.cli import main as market_cli
        return market_cli(args[1:])
    from magi.portfolio_cli import main as portfolio_cli
    return portfolio_cli(args)


if __name__ == "__main__":
    raise SystemExit(cli())
