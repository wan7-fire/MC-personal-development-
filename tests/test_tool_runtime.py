import asyncio
import unittest

from openai.lib._parsing._completions import validate_input_tools

from mewcode.tools import Tool, ToolCall, ToolExecutor, ToolRegistry, ToolResult


class EchoTool(Tool):
    name = "Echo"
    description = "Return the supplied text."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(success=True, output=arguments["text"])


class SpyTool(EchoTool):
    name = "Spy"
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1},
            "count": {"type": ["integer", "null"], "minimum": 1},
            "items": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["text", "count", "items"],
        "additionalProperties": False,
    }

    def __init__(self):
        self.entered = False

    async def execute(self, arguments, context):
        self.entered = True
        return await super().execute(arguments, context)


class TrackingTool(Tool):
    description = "Track execution order."
    input_schema = {"type": "object"}

    def __init__(self, name, read_only, events, reads_started=None):
        self.name = name
        self.read_only = read_only
        self.events = events
        self.reads_started = reads_started

    async def execute(self, arguments, context):
        self.events.append(f"start:{self.name}")
        if self.read_only:
            self.reads_started.add(self.name)
            if len(self.reads_started) == 2:
                self.reads_started.ready.set()
            await self.reads_started.ready.wait()
            await asyncio.sleep(0)
        self.events.append(f"end:{self.name}")
        return ToolResult(success=True, output=self.name)


class ReadsStarted(set):
    def __init__(self):
        super().__init__()
        self.ready = asyncio.Event()


class ToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_exports_and_executor_runs_tool(self):
        registry = ToolRegistry([EchoTool()])
        executor = ToolExecutor(registry)

        result = await executor.execute("call-1", "Echo", {"text": "hello"})

        self.assertTrue(result.success)
        self.assertEqual(result.output, "hello")
        self.assertEqual(
            registry.definitions(),
            [
                {
                    "type": "function",
                    "function": {
                        "name": "Echo",
                        "description": "Return the supplied text.",
                        "parameters": EchoTool.input_schema,
                        "strict": True,
                    },
                }
            ],
        )
        validate_input_tools(registry.definitions())

    async def test_batch_runs_reads_together_before_serial_writes(self):
        events = []
        reads_started = ReadsStarted()
        tools = [
            TrackingTool("read-a", True, events, reads_started),
            TrackingTool("read-b", True, events, reads_started),
            TrackingTool("write-a", False, events),
            TrackingTool("write-b", False, events),
        ]
        executor = ToolExecutor(ToolRegistry(tools))
        calls = [
            ToolCall("1", "read-a", {}),
            ToolCall("2", "write-a", {}),
            ToolCall("3", "read-b", {}),
            ToolCall("4", "write-b", {}),
        ]

        results = await executor.execute_batch(calls)

        self.assertEqual([result.output for result in results], [
            "read-a", "write-a", "read-b", "write-b"
        ])
        self.assertLess(events.index("end:read-a"), events.index("start:write-a"))
        self.assertLess(events.index("end:read-b"), events.index("start:write-a"))
        self.assertEqual(events[-4:], [
            "start:write-a", "end:write-a", "start:write-b", "end:write-b"
        ])

    async def test_invalid_schema_arguments_do_not_enter_tool(self):
        valid = {"text": "hello", "count": None, "items": [{"value": "x"}]}
        invalid = [
            {**valid, "unexpected": True},
            {"text": "hello"},
            {**valid, "text": ""},
            {**valid, "count": True},
            {**valid, "count": 0},
            {**valid, "items": []},
            {**valid, "items": [{"value": "x", "unexpected": True}]},
        ]
        for index, arguments in enumerate(invalid):
            with self.subTest(arguments=arguments):
                tool = SpyTool()
                result = await ToolExecutor(ToolRegistry([tool])).execute(
                    f"call-bad-{index}", "Spy", arguments
                )
                self.assertEqual(result.error["code"], "invalid_arguments")
                self.assertFalse(tool.entered)


if __name__ == "__main__":
    unittest.main()
