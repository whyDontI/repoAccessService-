"""R4 harness: fires random checks at the real fixture database,
interleaved with random mutations, comparing checker.check() against
oracle.oracle_check() on every check and logging any disagreement.

Run: python backend/scripts/oracle_harness.py [--rounds N] [--mutation-rate P]
Needs DATABASE_URL pointing at a database with the fixture already loaded.
"""
import argparse
import asyncio
import json
import os
import random
import time
from pathlib import Path

from app import cache, checker, db, oracle
from app.models import Role

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://repo_access:repo_access@localhost:5432/repo_access"
)


async def _load_ids(pool):
    users = [r["id"] for r in await pool.fetch("SELECT id FROM subjects WHERE type = 'user'")]
    teams = [r["id"] for r in await pool.fetch("SELECT id FROM subjects WHERE type = 'team'")]
    repos = [r["id"] for r in await pool.fetch("SELECT id FROM resources WHERE type = 'repo'")]
    orgs = [r["id"] for r in await pool.fetch("SELECT id FROM resources WHERE type = 'org'")]
    return users, teams, repos, orgs


def _invalidate_for(subject_type: str, subject_id: int) -> None:
    """Same rule routes.py applies on a real mutation: a user's own
    membership change only invalidates their entry, a team's changing
    (it moved to a new parent) invalidates everything. Applying it here
    too matters -- skipping it would manufacture a "divergence" that's
    just the harness cutting a corner, not a real bug."""
    if subject_type == "user":
        cache.invalidate_user(subject_id)
    else:
        cache.invalidate_all()


async def _random_team_relationship(pool, id_range: tuple[int, int]):
    """Approximately-random existing row, via an index range scan on id
    rather than ORDER BY random() -- that would sort the whole table on
    every single call."""
    guess = random.randint(*id_range)
    return await pool.fetchrow(
        "SELECT id, subject_id FROM team_relationships WHERE id >= $1 ORDER BY id LIMIT 1", guess
    )


async def _random_grant(pool, id_range: tuple[int, int]):
    guess = random.randint(*id_range)
    return await pool.fetchrow("SELECT id FROM grants WHERE id >= $1 ORDER BY id LIMIT 1", guess)


async def random_mutation(pool, ranges, users, teams, repos, orgs) -> str:
    """Applies one random mutation. Weighted like README's mutation mix:
    membership adds dominate, removes are rarer, team re-parenting
    (the expensive, cache-wiping case) is rare on purpose."""
    roll = random.random()

    if roll < 0.70:
        user_id, team_id = random.choice(users), random.choice(teams)
        await pool.execute(
            "INSERT INTO team_relationships (subject_id, belongs_to_team_id) VALUES ($1, $2)",
            user_id, team_id,
        )
        cache.invalidate_user(user_id)
        return f"add membership: user {user_id} -> team {team_id}"

    if roll < 0.75:
        row = await _random_team_relationship(pool, ranges["team_relationships"])
        if row is None:
            return "remove membership: skipped, none found"
        subject_type = await pool.fetchval("SELECT type FROM subjects WHERE id = $1", row["subject_id"])
        await pool.execute("DELETE FROM team_relationships WHERE id = $1", row["id"])
        _invalidate_for(subject_type, row["subject_id"])
        return f"remove membership: id {row['id']} (subject {row['subject_id']}, {subject_type})"

    if roll < 0.90:
        subject_id = random.choice(users + teams)
        resource_id = random.choice(repos + orgs)
        role = random.choice(list(Role)).name.lower()
        await pool.execute(
            "INSERT INTO grants (subject_id, role, resource_id) VALUES ($1, $2, $3)",
            subject_id, role, resource_id,
        )
        return f"add grant: {subject_id} {role} on {resource_id}"

    if roll < 0.97:
        row = await _random_grant(pool, ranges["grants"])
        if row is None:
            return "remove grant: skipped, none found"
        await pool.execute("DELETE FROM grants WHERE id = $1", row["id"])
        return f"remove grant: id {row['id']}"

    # Re-parent: replace a team's current parent edge (if any) with a new
    # one, rather than just adding a second parent -- that's the
    # expensive case our whole cache design is betting stays rare.
    team_id, new_parent = random.choice(teams), random.choice(teams)
    if team_id == new_parent:
        return "reparent: skipped, self-parent"
    old_edge = await pool.fetchrow(
        "SELECT id FROM team_relationships WHERE subject_id = $1 AND belongs_to_team_id IS NOT NULL",
        team_id,
    )
    if old_edge:
        await pool.execute("DELETE FROM team_relationships WHERE id = $1", old_edge["id"])
    await pool.execute(
        "INSERT INTO team_relationships (subject_id, belongs_to_team_id) VALUES ($1, $2)",
        team_id, new_parent,
    )
    cache.invalidate_all()
    return f"reparent: team {team_id} -> new parent {new_parent}"


async def run_harness(rounds: int, mutation_rate: float, seed: int) -> list[dict]:
    random.seed(seed)
    pool = await db.get_pool()
    users, teams, repos, orgs = await _load_ids(pool)
    ranges = {
        "team_relationships": tuple((await pool.fetchrow(
            "SELECT min(id) AS lo, max(id) AS hi FROM team_relationships"
        )).values()),
        "grants": tuple((await pool.fetchrow("SELECT min(id) AS lo, max(id) AS hi FROM grants")).values()),
    }

    divergences = []
    for round_number in range(rounds):
        last_mutation = None
        if random.random() < mutation_rate:
            last_mutation = await random_mutation(pool, ranges, users, teams, repos, orgs)

        user_id = random.choice(users)
        action = random.choice(list(Role))
        repo_id = random.choice(repos)

        fast = await checker.check(user_id, action, repo_id)
        slow = await oracle.oracle_check(user_id, action, repo_id)
        if fast != slow:
            divergences.append({
                "round": round_number,
                "user_id": user_id,
                "action": action.name,
                "repo_id": repo_id,
                "fast_path": fast,
                "oracle": slow,
                "preceding_mutation": last_mutation,
            })

    return divergences


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2000)
    parser.add_argument("--mutation-rate", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    start = time.perf_counter()
    divergences = await run_harness(args.rounds, args.mutation_rate, args.seed)
    elapsed = time.perf_counter() - start

    print(f"{args.rounds} rounds ({elapsed:.1f}s): {len(divergences)} divergences found")
    out_path = Path(__file__).with_name("oracle_divergences.json")
    out_path.write_text(json.dumps(divergences, indent=2))
    print(f"Written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
