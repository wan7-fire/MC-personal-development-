import unittest

from mewcode.cancel import CancelToken
from mewcode.config import ALL_REASONS, AgentConfig, TerminationReason
from mewcode.state import IllegalStateTransition, LoopState, LoopStateMachine
from mewcode.terminator import LoopTerminatorConfig


LEGAL = [
    (LoopState.IDLE, "start", LoopState.RUNNING),
    (LoopState.RUNNING, "tool_call", LoopState.TOOL_EXECUTING),
    (LoopState.TOOL_EXECUTING, "tool_done", LoopState.RUNNING),
    (LoopState.RUNNING, "terminate", LoopState.TERMINATED),
    (LoopState.RUNNING, "cancel", LoopState.CANCELLED),
    (LoopState.TOOL_EXECUTING, "cancel", LoopState.CANCELLED),
    (LoopState.CANCELLED, "cleanup_done", LoopState.TERMINATED),
    (LoopState.RUNNING, "enter_plan_only", LoopState.PLAN_ONLY),
    (LoopState.PLAN_ONLY, "exit_plan_only", LoopState.RUNNING),
]


def machine_in(state: LoopState) -> LoopStateMachine:
    machine = LoopStateMachine()
    machine._state = state
    return machine


class StateMachineTests(unittest.TestCase):
    def test_enum_members(self):
        self.assertEqual(
            [member.name for member in LoopState],
            [
                "IDLE",
                "RUNNING",
                "TOOL_EXECUTING",
                "PLAN_ONLY",
                "CANCELLED",
                "TERMINATED",
            ],
        )

    def test_all_legal_transitions(self):
        self.assertEqual(len(LEGAL), 9)
        for source, action, target in LEGAL:
            with self.subTest(source=source, action=action):
                self.assertEqual(machine_in(source).transition(action), target)

    def test_illegal_transition_names_state_and_action(self):
        with self.assertRaises(IllegalStateTransition) as caught:
            LoopStateMachine().transition("tool_call")
        message = str(caught.exception)
        self.assertIn("IDLE", message)
        self.assertIn("tool_call", message)

    def test_terminated_is_final(self):
        with self.assertRaises(IllegalStateTransition):
            machine_in(LoopState.TERMINATED).transition("start")

    def test_illegal_transition_is_a_runtime_error(self):
        self.assertTrue(issubclass(IllegalStateTransition, RuntimeError))

    def test_termination_reason_is_recorded(self):
        machine = machine_in(LoopState.RUNNING)
        machine.transition("terminate", TerminationReason.MAX_TURNS)
        self.assertEqual(machine.termination_reason, "max_turns")

    def test_can_reports_legality_without_transitioning(self):
        machine = LoopStateMachine()
        self.assertTrue(machine.can("start"))
        self.assertFalse(machine.can("cancel"))
        self.assertIs(machine.state, LoopState.IDLE)


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = AgentConfig()
        self.assertEqual(config.max_turns, 20)
        self.assertIs(config.plan_only, False)
        self.assertEqual(config.llm_timeout_seconds, 120.0)
        self.assertIsInstance(config.cancel_token, CancelToken)

    def test_terminator_defaults_are_unchanged(self):
        guard = LoopTerminatorConfig()
        self.assertEqual(guard.repeated_observation_limit, 3)
        self.assertEqual(guard.repeated_error_limit, 2)
        self.assertEqual(guard.no_progress_limit, 4)

    def test_with_plan_only_returns_a_new_instance(self):
        config = AgentConfig()
        planning = config.with_plan_only(True)
        self.assertIs(config.plan_only, False)
        self.assertIs(planning.plan_only, True)
        self.assertIsNot(config, planning)

    def test_guard_reasons_are_part_of_the_reason_set(self):
        for reason in ("repeated_observation", "repeated_error", "no_progress"):
            self.assertIn(reason, ALL_REASONS)
        self.assertEqual(len(ALL_REASONS), 8)


if __name__ == "__main__":
    unittest.main()
