"""In-memory cache of each user's team set. See README's "Fast checks vs
fast revocation" section for why grants are never cached here, only team
membership.

One process, one dict, no locking: everything runs on a single asyncio
event loop and nothing here awaits mid-mutation, so two coroutines can
never interleave inside get/set/invalidate.
"""
from datetime import datetime, timezone

_cache: dict[int, tuple[set[int], datetime]] = {}


def get(user_id: int) -> tuple[set[int], datetime] | None:
    """Returns (team_ids, cached_at), or None on a miss. The timestamp is
    what the frontend shows as "as of" on a cache-hit answer."""
    return _cache.get(user_id)


def set(user_id: int, team_ids: set[int]) -> None:
    _cache[user_id] = (team_ids, datetime.now(timezone.utc))


def invalidate_user(user_id: int) -> None:
    _cache.pop(user_id, None)


def invalidate_all() -> None:
    _cache.clear()
