"""Regression test for a real bug: db.get_pool()'s lazy initialization
had no lock, so a burst of concurrent callers could each see `_pool is
None` and independently call asyncpg.create_pool(), opening far more
connections than intended all at once. Found under load, fixed with an
asyncio.Lock -- this proves the fix actually closes the race, using a
fake create_pool with no real Postgres involved.
"""
import asyncio
import unittest

from app import db


class TestGetPoolIsRaceSafe(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db._pool = None
        self._real_create_pool = db.asyncpg.create_pool
        self.create_pool_calls = 0

        async def fake_create_pool(*args, **kwargs):
            self.create_pool_calls += 1
            # Widens the race window: without the lock, many callers would
            # all pass the `_pool is None` check before any of them
            # finishes "creating" the pool.
            await asyncio.sleep(0.05)
            return object()

        db.asyncpg.create_pool = fake_create_pool

    async def asyncTearDown(self):
        db.asyncpg.create_pool = self._real_create_pool
        db._pool = None

    async def test_concurrent_callers_only_create_the_pool_once(self):
        pools = await asyncio.gather(*[db.get_pool() for _ in range(50)])

        self.assertEqual(self.create_pool_calls, 1)
        self.assertTrue(all(p is pools[0] for p in pools))


if __name__ == "__main__":
    unittest.main()
