import asyncio
import unittest

from zxcode.mcp.errors import McpError
from zxcode.mcp.matcher import ResponseMatcher
from zxcode.mcp.protocol import Response


class ResponseMatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_matching_id(self):
        matcher = ResponseMatcher()
        future = matcher.register(1, "tools/call")
        resolved = matcher.resolve(Response(1, result={"ok": True}))
        self.assertTrue(resolved)
        response = await future
        self.assertEqual(response.result, {"ok": True})
        self.assertEqual(matcher.pending_count, 0)

    async def test_unknown_id_is_ignored(self):
        matcher = ResponseMatcher()
        matcher.register(1, "tools/call")
        self.assertFalse(matcher.resolve(Response(999, result=None)))
        self.assertEqual(matcher.pending_count, 1)

    async def test_late_response_after_abandon_is_ignored(self):
        matcher = ResponseMatcher()
        matcher.register(1, "tools/call")
        matcher.abandon(1)
        self.assertFalse(matcher.resolve(Response(1, result="late")))
        self.assertEqual(matcher.pending_count, 0)

    async def test_duplicate_register_raises(self):
        matcher = ResponseMatcher()
        matcher.register(1, "ping")
        with self.assertRaises(ValueError):
            matcher.register(1, "ping")

    async def test_fail_raises_mcp_error_in_waiter(self):
        matcher = ResponseMatcher()
        future = matcher.register(2, "tools/list")
        self.assertTrue(matcher.fail(2, "remote_error", "boom"))
        with self.assertRaises(McpError) as ctx:
            await future
        self.assertEqual(ctx.exception.code, "remote_error")

    async def test_fail_all_fails_every_pending(self):
        matcher = ResponseMatcher()
        futures = [matcher.register(i, "ping") for i in range(3)]
        matcher.fail_all("connection_error", "closed")
        self.assertEqual(matcher.pending_count, 0)
        for future in futures:
            with self.assertRaises(McpError) as ctx:
                await future
            self.assertEqual(ctx.exception.code, "connection_error")

    async def test_resolve_does_not_override_done_future(self):
        matcher = ResponseMatcher()
        future = matcher.register(1, "ping")
        matcher.abandon(1)
        future.cancel()
        self.assertFalse(matcher.resolve(Response(1, result="x")))


if __name__ == "__main__":
    unittest.main()
