import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.loop.terminator import LoopTerminator, LoopTerminatorConfig


def test_stops_at_max_turns():
    terminator = LoopTerminator(LoopTerminatorConfig(max_turns=3))

    decision = terminator.check(turn=3)

    assert decision.should_stop
    assert decision.reason == "max_turns"


def test_stops_after_repeated_tool_observation():
    terminator = LoopTerminator(
        LoopTerminatorConfig(repeated_observation_limit=3, max_turns=20)
    )

    for turn in range(2):
        decision = terminator.check(
            turn=turn,
            tool_name="search",
            tool_args={"query": "missing package"},
            observation="No results found.",
        )
        assert not decision.should_stop

    decision = terminator.check(
        turn=2,
        tool_name="search",
        tool_args={"query": "missing package"},
        observation="No results found.",
    )

    assert decision.should_stop
    assert decision.reason == "repeated_observation"


def test_stops_after_repeated_error():
    terminator = LoopTerminator(LoopTerminatorConfig(repeated_error_limit=2))

    decision = terminator.check(turn=0, error="permission denied")
    assert not decision.should_stop

    decision = terminator.check(turn=1, error="permission denied")

    assert decision.should_stop
    assert decision.reason == "repeated_error"


def test_stops_after_no_progress():
    terminator = LoopTerminator(LoopTerminatorConfig(no_progress_limit=2))

    decision = terminator.check(turn=0, progress_score=0.5)
    assert not decision.should_stop

    decision = terminator.check(turn=1, progress_score=0.5)
    assert not decision.should_stop

    decision = terminator.check(turn=2, progress_score=0.49)

    assert decision.should_stop
    assert decision.reason == "no_progress"


if __name__ == "__main__":
    for test in (
        test_stops_at_max_turns,
        test_stops_after_repeated_tool_observation,
        test_stops_after_repeated_error,
        test_stops_after_no_progress,
    ):
        test()
    print("terminator self-check passed")
