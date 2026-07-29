import asyncio
import dataclasses
import json
import unittest

from mewcode.events import Event, EventChannel, EventType


class EventTests(unittest.TestCase):
    def test_all_event_types_are_declared(self):
        declared = {
            value
            for name, value in vars(EventType).items()
            if not name.startswith("_") and isinstance(value, str)
        }
        self.assertEqual(
            declared,
            {
                "user_message",
                "thinking",
                "text",
                "tool_call_start",
                "tool_call_end",
                "tool_result",
                "turn_end",
                "final_reply",
                "error",
                "cancelled",
                "loop_end",
            },
        )

    def test_event_is_frozen_with_a_millisecond_timestamp(self):
        event = Event(type=EventType.TEXT)
        self.assertIsInstance(event.timestamp, int)
        self.assertGreater(event.timestamp, 1_700_000_000_000)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.turn = 1

    def test_to_dict_serialises_without_ascii_escaping(self):
        event = Event(type=EventType.TEXT, turn=2, data={"content": "你好"})
        dumped = json.dumps(event.to_dict(), ensure_ascii=False)
        self.assertIn('"content": "你好"', dumped)
        self.assertEqual(event.to_dict()["turn"], 2)


class EventChannelTests(unittest.IsolatedAsyncioTestCase):
    def test_default_queue_size(self):
        self.assertEqual(EventChannel()._queue.maxsize, 1000)

    async def test_events_arrive_in_order_then_iteration_ends(self):
        channel = EventChannel()
        for index in range(3):
            await channel.emit(Event(type=EventType.TEXT, data={"i": index}))
        channel.close()

        received = [event async for event in channel]
        self.assertEqual([event.data["i"] for event in received], [0, 1, 2])

    async def test_iterating_a_closed_channel_returns_immediately(self):
        channel = EventChannel()
        channel.close()

        async def drain():
            return [event async for event in channel]

        self.assertEqual(await asyncio.wait_for(drain(), timeout=1), [])

    async def test_emitting_after_close_is_silently_dropped(self):
        channel = EventChannel()
        channel.close()
        await channel.emit(Event(type=EventType.TEXT))

        async def drain():
            return [event async for event in channel]

        self.assertEqual(await asyncio.wait_for(drain(), timeout=1), [])

    async def test_close_is_idempotent(self):
        channel = EventChannel()
        channel.close()
        channel.close()
        self.assertTrue(channel.closed)


if __name__ == "__main__":
    unittest.main()
