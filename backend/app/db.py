"""One shared connection pool for the whole app. No ORM -- the schema is
small enough that raw SQL is easier to follow than an ORM layer we'd only
ever use once.
"""
import os

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://repo_access:repo_access@localhost:5432/repo_access"
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool
