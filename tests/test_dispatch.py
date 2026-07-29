import asyncio
import unittest
from time import monotonic

from mewcode.config import AgentConfig
from mewcode.dispatch import PLAN_ONLY_MESSAGE, ToolDispatcher
from mewcode.events import EventChannel, EventType
from mewcode.tools import Tool, ToolCall, ToolContext, ToolExecutor, ToolRegistry, ToolResult


SCHEMA = {"type": "object", "properties": {}, "required": []}


class Timed(Tool):
    """Records its own execution window so overlap can be asserted."""

    description = "timed"
    input_schema = SCHEMA

    def __init__(self, name, read_only=True, delay=0.3, log=None):
        self.name = name
        self.read_only = read_only
        self.delay = delay
        self.log = log if log is not None else []

    async def execute(self, arguments, context):
        started = monotonic()
        await asyncio.sleep(self.delay)
        self.log.append((self.name, started, monotonic()))
        return ToolResult(True, self.name)


class Boom(Tool):
    name = "Boom"
    description = "raises"
    input_schema = SCHEMA

    async def execute(self, arguments, context):
        raise RuntimeError("boom")


class Slow(Tool):
    name = "Slow"
    description = "times out"
    input_schema = SCHEMA
    timeout_seconds = 0.05

    async def execute(self, arguments, context):
        await asyncio.sleep(1)
        return ToolResult(True, "never")


class Counting(Tool):
    description = "counts executions"
    input_schema = SCHEMA

    def __init__(self, name, read_only):
        self.name = name
        self.read_only = read_only
        self.calls = 0

    async def execute(self, arguments, context):
        self.calls += 1
        return ToolResult(True, "ok")


def call(name, index=1):
    return ToolCall(f"call-{index}", name, {})


async def run(tools, calls, config=None):
    registry = ToolRegistry(tools)
    channel = EventChannel()
    dispatcher = ToolDispatcher(registry, ToolExecutor(registry), channel)
    outcome = await dispatcher.dispatch(
        calls, ToolContext(), config or AgentConfig(), 0
    )
    channel.close()
    events = [event async for event in channel]
    return outcome, events


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_run_concurrently(self):
        log = []
        tools = [Timed(f"R{i}", True, 0.3, log) for i in range(3)]
        started = monotonic()
        await run(tools, [call(f"R{i}", i) for i in range(3)])
        self.assertLess(monotonic() - started, 0.6)

    async def test_writes_run_serially(self):
        log = []
        tools = [Timed(f"W{i}", False, 0.05, log) for i in range(3)]
        await run(tools, [call(f"W{i}", i) for i in range(3)])

        windows = sorted(log, key=lambda item: item[1])
        for earlier, later in zip(windows, windows[1:]):
            self.assertLessEqual(earlier[2], later[1] + 1e-6)

    async def test_reads_start_before_writes(self):
        log = []
        tools = [
            Timed("WriteA", False, 0.05, log),
            Timed("ReadB", True, 0.05, log),
            Timed("WriteC", False, 0.05, log),
        ]
        await run(
            tools,
            [call("WriteA", 1), call("ReadB", 2), call("WriteC", 3)],
        )
        starts = {name: start for name, start, _ in log}
        self.assertLess(starts["ReadB"], starts["WriteA"])

    async def test_results_keep_the_input_order(self):
        tools = [
            Timed("WriteA", False, 0.01),
            Timed("ReadB", True, 0.01),
            Timed("WriteC", False, 0.01),
        ]
        calls = [call("WriteA", 1), call("ReadB", 2), call("WriteC", 3)]
        outcome, _ = await run(tools, calls)

        self.assertEqual(
            [result.output for result in outcome.results],
            ["WriteA", "ReadB", "WriteC"],
        )
        self.assertEqual(
            [result.metadata["call_id"] for result in outcome.results],
            ["call-1", "call-2", "call-3"],
        )

    async def test_one_failure_does_not_lose_the_batch(self):
        tools = [Timed("R1", True, 0.01), Boom(), Timed("R2", True, 0.01)]
        outcome, _ = await run(
            tools, [call("R1", 1), call("Boom", 2), call("R2", 3)]
        )
        self.assertEqual(len(outcome.results), 3)
        self.assertFalse(outcome.results[1].success)


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_end_events_are_paired(self):
        tools = [Timed("R1", True, 0.01), Timed("R2", True, 0.01)]
        _, events = await run(tools, [call("R1", 1), call("R2", 2)])

        starts = [e for e in events if e.type == EventType.TOOL_CALL_START]
        ends = [e for e in events if e.type == EventType.TOOL_CALL_END]
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(ends), 2)
        for start in starts:
            end = next(
                e
                for e in ends
                if e.data["tool_call_id"] == start.data["tool_call_id"]
            )
            self.assertLess(events.index(start), events.index(end))

    async def test_start_event_payload(self):
        _, events = await run([Timed("R1", True, 0.01)], [call("R1", 1)])
        start = next(e for e in events if e.type == EventType.TOOL_CALL_START)
        self.assertEqual(
            set(start.data),
            {"tool_call_id", "tool_name", "arguments", "tool_type"},
        )
        self.assertIn(start.data["tool_type"], ("read", "write"))

    async def test_write_tools_are_tagged_as_write(self):
        _, events = await run([Timed("W1", False, 0.01)], [call("W1", 1)])
        start = next(e for e in events if e.type == EventType.TOOL_CALL_START)
        self.assertEqual(start.data["tool_type"], "write")

    async def test_duration_reflects_real_time(self):
        _, events = await run([Timed("R1", True, 0.2)], [call("R1", 1)])
        end = next(e for e in events if e.type == EventType.TOOL_CALL_END)
        self.assertGreaterEqual(end.data["duration_ms"], 180)

    async def test_status_is_success_error_or_timeout(self):
        cases = {
            "R1": ("success", Timed("R1", True, 0.01)),
            "Boom": ("error", Boom()),
            "Slow": ("timeout", Slow()),
        }
        for name, (expected, tool) in cases.items():
            with self.subTest(tool=name):
                _, events = await run([tool], [call(name, 1)])
                end = next(e for e in events if e.type == EventType.TOOL_CALL_END)
                self.assertEqual(end.data["status"], expected)


class PlanOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_tools_never_execute(self):
        writer = Counting("W1", read_only=False)
        outcome, _ = await run(
            [writer], [call("W1", 1)], AgentConfig(plan_only=True)
        )

        self.assertEqual(writer.calls, 0)
        self.assertFalse(outcome.results[0].success)
        self.assertEqual(outcome.results[0].error["code"], "plan_only_blocked")
        self.assertEqual(outcome.results[0].error["message"], PLAN_ONLY_MESSAGE)

    async def test_blocked_message_text(self):
        self.assertEqual(
            PLAN_ONLY_MESSAGE,
            "当前为 plan-only 模式，写类工具已被拦截。"
            "请使用 /plan 关闭该模式后再执行写操作。",
        )

    async def test_read_tools_still_execute(self):
        reader = Counting("R1", read_only=True)
        await run([reader], [call("R1", 1)], AgentConfig(plan_only=True))
        self.assertEqual(reader.calls, 1)

    async def test_blocked_entry_shape(self):
        outcome, _ = await run(
            [Counting("W1", read_only=False)],
            [ToolCall("call-1", "W1", {"path": "x"})],
            AgentConfig(plan_only=True),
        )
        self.assertEqual(len(outcome.blocked_calls), 1)
        self.assertEqual(
            set(outcome.blocked_calls[0]), {"tool_name", "arguments", "reason"}
        )
        self.assertEqual(outcome.blocked_calls[0]["arguments"], {"path": "x"})

    async def test_nothing_blocked_when_mode_is_off(self):
        outcome, _ = await run(
            [Counting("W1", read_only=False)], [call("W1", 1)], AgentConfig()
        )
        self.assertEqual(outcome.blocked_calls, [])


if __name__ == "__main__":
    unittest.main()
