import asyncio
import threading
import unittest

from mewcode.cancel import CancelToken


class CancelTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_lifecycle(self):
        token = CancelToken()
        self.assertFalse(token.is_cancelled())
        token.cancel()
        self.assertTrue(token.is_cancelled())
        token.reset()
        self.assertFalse(token.is_cancelled())

    async def test_wait_returns_once_cancelled(self):
        token = CancelToken()
        token.cancel()
        await asyncio.wait_for(token.wait(), timeout=1)

    async def test_wait_blocks_after_reset(self):
        token = CancelToken()
        token.cancel()
        await asyncio.wait_for(token.wait(), timeout=1)
        token.reset()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(token.wait(), timeout=0.2)

    async def test_concurrent_access_is_safe(self):
        token = CancelToken()
        errors = []

        def hammer():
            try:
                for _ in range(100):
                    token.is_cancelled()
                    token.cancel()
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [threading.Thread(target=hammer) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertTrue(token.is_cancelled())


if __name__ == "__main__":
    unittest.main()
