"""Load generator: fires /check at a target rate while separately
applying membership mutations at a configurable rate, against a running
instance of the API. Reports p50/p95/p99 check latency at the end.

Run: python backend/scripts/load_test.py --check-rate 5000 --mutation-rate 50 --duration 10
Needs the API running (see README) and DATABASE_URL pointing at the same
database it's using -- this script reads real ids from the DB to build
realistic requests, but every timed request goes over HTTP, not to the DB.
"""
import argparse
import asyncio
import json
import os
import random
import time
from pathlib import Path

import asyncpg
import httpx2 as httpx

from app.models import Role

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://repo_access:repo_access@localhost:5432/repo_access"
)


class Targets:
    """Real ids to build requests from. deep_user_ids come from the
    fixture manifest -- the assignment specifically asks for the
    deepest-nesting case to be included in the measurement, not just
    the easy ones, so a fixed share of checks always hits them.
    membership_id_range is an (min, max) id bound used to find an
    existing membership to delete via an index range scan, rather than
    sorting or counting the whole table on every mutation."""

    def __init__(self, users, teams, repos, deep_user_ids, membership_id_range):
        self.users = users
        self.teams = teams
        self.repos = repos
        self.deep_user_ids = deep_user_ids or users[:20]
        self.membership_id_range = membership_id_range

    def pick_check(self):
        user_id = random.choice(self.deep_user_ids) if random.random() < 0.1 else random.choice(self.users)
        return user_id, random.choice(list(Role)).name.lower(), random.choice(self.repos)


async def _load_targets(pool) -> Targets:
    users = [r["id"] for r in await pool.fetch("SELECT id FROM subjects WHERE type = 'user'")]
    teams = [r["id"] for r in await pool.fetch("SELECT id FROM subjects WHERE type = 'team'")]
    repos = [r["id"] for r in await pool.fetch("SELECT id FROM resources WHERE type = 'repo'")]
    manifest_path = Path(__file__).with_name("fixture_manifest.json")
    deep_user_ids = json.loads(manifest_path.read_text())["deep_user_ids"] if manifest_path.exists() else None
    id_range_row = await pool.fetchrow("SELECT min(id) AS lo, max(id) AS hi FROM team_relationships")
    return Targets(users, teams, repos, deep_user_ids, (id_range_row["lo"], id_range_row["hi"]))


async def _timed_check(client, base_url, targets, results):
    user_id, action, repo_id = targets.pick_check()
    start = time.perf_counter()
    try:
        resp = await client.get(
            f"{base_url}/check", params={"user_id": user_id, "action": action, "repo_id": repo_id}
        )
        ok = resp.status_code == 200
    except httpx.HTTPError:
        ok = False
    results.append(((time.perf_counter() - start) * 1000, ok))


async def _apply_mutation(client, base_url, pool, targets):
    """Same adds-heavy mix as the fixture and the R4 harness: adding a
    user to a team dominates, removing one is rarer. Both go through the
    real API -- the DB pool here is only used to find an existing
    membership id to delete, never to perform the mutation itself.
    Errors are swallowed the same way _timed_check's are: one failed
    request during a high-throughput run shouldn't crash the whole
    measurement."""
    try:
        if random.random() < 0.9:
            user_id, team_id = random.choice(targets.users), random.choice(targets.teams)
            await client.post(
                f"{base_url}/membership", json={"subject_id": user_id, "belongs_to_team_id": team_id}
            )
        else:
            guess = random.randint(*targets.membership_id_range)
            row = await pool.fetchrow(
                "SELECT id FROM team_relationships WHERE id >= $1 ORDER BY id LIMIT 1", guess
            )
            if row:
                await client.delete(f"{base_url}/membership/{row['id']}")
    except httpx.HTTPError:
        pass


async def run_checks(client, base_url, targets, rate, duration, results):
    interval = 1.0 / rate
    end_time = time.perf_counter() + duration
    next_send = time.perf_counter()
    tasks = []
    while time.perf_counter() < end_time:
        now = time.perf_counter()
        if now < next_send:
            await asyncio.sleep(next_send - now)
        tasks.append(asyncio.create_task(_timed_check(client, base_url, targets, results)))
        next_send += interval
    await asyncio.gather(*tasks)


async def run_mutations(client, base_url, pool, targets, rate, duration):
    if rate <= 0:
        return
    interval = 1.0 / rate
    end_time = time.perf_counter() + duration
    next_send = time.perf_counter()
    tasks = []
    while time.perf_counter() < end_time:
        now = time.perf_counter()
        if now < next_send:
            await asyncio.sleep(next_send - now)
        tasks.append(asyncio.create_task(_apply_mutation(client, base_url, pool, targets)))
        next_send += interval
    await asyncio.gather(*tasks)


def _percentile(sorted_values, p):
    index = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[index]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-rate", type=int, default=5000, help="checks per second")
    parser.add_argument("--mutation-rate", type=int, default=0, help="mutations per second")
    parser.add_argument("--duration", type=float, default=10, help="seconds")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    pool = await asyncpg.create_pool(DATABASE_URL)
    targets = await _load_targets(pool)

    # httpx's default connection pool (max_connections=100) is fine at low
    # rates but starts throwing PoolTimeout well before 5,000/sec -- size
    # it to the target rate instead of the library default.
    limits = httpx.Limits(max_connections=args.check_rate, max_keepalive_connections=args.check_rate)
    results: list[tuple[float, bool]] = []
    async with httpx.AsyncClient(timeout=5.0, limits=limits) as client:
        await asyncio.gather(
            run_checks(client, args.url, targets, args.check_rate, args.duration, results),
            run_mutations(client, args.url, pool, targets, args.mutation_rate, args.duration),
        )
    await pool.close()

    latencies = sorted(latency for latency, ok in results if ok)
    failures = sum(1 for _, ok in results if not ok)

    print(f"{len(results)} checks attempted over {args.duration}s ({failures} failed)")
    if latencies:
        print(f"p50: {_percentile(latencies, 0.50):.2f}ms")
        print(f"p95: {_percentile(latencies, 0.95):.2f}ms")
        print(f"p99: {_percentile(latencies, 0.99):.2f}ms")


if __name__ == "__main__":
    asyncio.run(main())
