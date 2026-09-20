"""One shared connection pool for the whole app. No ORM -- the schema is
small enough that raw SQL is easier to follow than an ORM layer we'd only
ever use once.
"""
import asyncio
import os

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://repo_access:repo_access@localhost:5432/repo_access"
)
# asyncpg's own default (max_size=10) becomes the bottleneck well before
# 5,000 checks/sec: found by load-testing, not guessed -- at high
# concurrency, requests queue up waiting for one of only 10 connections
# and latency collapses into the seconds. Not raising this arbitrarily
# high either: Postgres's own max_connections defaults to 100 on a
# container capped at 512MB, and every connection (each its own backend
# process) costs real memory in a budget that's already tight -- other
# clients (the harness, load_test.py's own setup connection, a stray
# psql) need headroom too. 20 is a deliberate middle point, not a guess.
DB_POOL_MAX_SIZE = int(os.environ.get("DB_POOL_MAX_SIZE", "20"))

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """Without the lock, a cold-start burst of concurrent requests would
    all see `_pool is None` at once and each independently call
    `create_pool`, opening far more connections than intended all at
    once -- found this the hard way under load (see BENCH.md/README):
    it looked like a resource-sizing problem until the traceback showed
    pool creation itself was what was racing."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(DATABASE_URL, max_size=DB_POOL_MAX_SIZE)
    return _pool
