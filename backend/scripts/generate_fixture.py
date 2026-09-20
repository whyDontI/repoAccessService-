"""Generates the fixture data described in plan.md: 50k users, 4k teams
(nested up to 8 levels, with a few deliberate cycles and a multi-parent
case), 1k orgs, 20k repos, and ~400k grants.

Run: python backend/scripts/generate_fixture.py
Needs DATABASE_URL pointing at an empty database with schema.sql already applied.
"""
import asyncio
import json
import os
import random
from pathlib import Path

import asyncpg

from fixture_graph import (
    add_second_parents,
    attach_remaining_teams,
    build_team_spine,
    plant_cycles,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://repo_access:repo_access@localhost:5432/repo_access"
)

SEED = 42
NUM_USERS = 50_000
NUM_TEAMS = 4_000
MAX_TEAM_DEPTH = 8
NUM_ORGS = 1_000
NUM_REPOS = 20_000
NUM_GRANTS = 400_000

ROLES = ["read", "write", "admin", "owner"]
ROLE_WEIGHTS = [0.55, 0.25, 0.15, 0.05]  # most access is read, fewest are owner

FIRST_USER_ID = 1
FIRST_TEAM_ID = FIRST_USER_ID + NUM_USERS
FIRST_ORG_ID = 1
FIRST_REPO_ID = FIRST_ORG_ID + NUM_ORGS


def build_teams(rng):
    team_ids = list(range(FIRST_TEAM_ID, FIRST_TEAM_ID + NUM_TEAMS))
    edges, depth_by_team = build_team_spine(team_ids, MAX_TEAM_DEPTH)
    spine_leaf_id = team_ids[MAX_TEAM_DEPTH - 1]
    spine_ids = set(team_ids[:MAX_TEAM_DEPTH])
    edges, depth_by_team = attach_remaining_teams(
        rng, team_ids, edges, depth_by_team, MAX_TEAM_DEPTH
    )
    # Keep the spine itself untouched by extra parents, so it stays exactly
    # 8 levels deep -- one clean, known-depth case to benchmark against.
    # Non-spine teams can still end up deeper than 8 through this (which is
    # the point: the assignment warns not to assume 8 is a hard max).
    non_spine_ids = [t for t in team_ids if t not in spine_ids]
    edges = add_second_parents(rng, non_spine_ids, edges, fraction=0.03)
    edges, cyclic_pairs = plant_cycles(rng, edges, count=3, exclude=spine_ids)
    empty_team_id = team_ids[-1]  # deliberately gets no direct members
    return team_ids, edges, spine_leaf_id, cyclic_pairs, empty_team_id


def assign_users_to_teams(rng, user_ids, team_ids, spine_leaf_id):
    """Most users get one team, some get two, a few get none. A handful
    are pinned under the deepest team, and *only* that team, so there's
    one clean, unambiguous depth-8 case to point the load test /
    benchmark at -- giving them a second, random team too would muddy
    that with an unrelated branch."""
    edges = []
    deep_user_ids = set(rng.sample(user_ids, 20))
    for user_id in deep_user_ids:
        edges.append((user_id, spine_leaf_id))

    for user_id in user_ids:
        if user_id in deep_user_ids:
            continue
        roll = rng.random()
        if roll < 0.02:
            continue  # no team at all
        edges.append((user_id, rng.choice(team_ids)))
        if roll > 0.95:
            edges.append((user_id, rng.choice(team_ids)))  # second team
    return edges, sorted(deep_user_ids)


def build_resources(rng):
    org_ids = list(range(FIRST_ORG_ID, FIRST_ORG_ID + NUM_ORGS))
    repo_ids = list(range(FIRST_REPO_ID, FIRST_REPO_ID + NUM_REPOS))
    repo_org = {repo_id: rng.choice(org_ids) for repo_id in repo_ids}
    return org_ids, repo_ids, repo_org


def build_grants(rng, user_ids, team_ids, org_ids, repo_ids, repo_org):
    subjects = user_ids + team_ids
    resources = org_ids + repo_ids
    grants = []
    seen = set()
    while len(grants) < NUM_GRANTS:
        subject_id = rng.choice(subjects)
        resource_id = rng.choice(resources)
        key = (subject_id, resource_id)
        if key in seen:
            continue  # one grant per subject/resource pair, like a real system
        seen.add(key)
        role = rng.choices(ROLES, weights=ROLE_WEIGHTS)[0]
        grants.append((subject_id, role, resource_id))

    # Deliberate repo-beats-org case: give one subject a low role on an
    # org and a higher one directly on one repo under it. Random grants
    # are independent per pair, so this isn't guaranteed to happen on its
    # own -- and it's the exact case that proves "take the max, repo can
    # beat org" (see README) actually works.
    override_subject = rng.choice(subjects)
    override_org = rng.choice(org_ids)
    override_repo = rng.choice([r for r, o in repo_org.items() if o == override_org])
    grants.append((override_subject, "read", override_org))
    grants.append((override_subject, "owner", override_repo))
    override_case = {
        "subject_id": override_subject,
        "org_id": override_org,
        "repo_id": override_repo,
        "org_role": "read",
        "repo_role": "owner",
    }
    return grants, override_case


async def main():
    rng = random.Random(SEED)
    conn = await asyncpg.connect(DATABASE_URL)

    user_ids = list(range(FIRST_USER_ID, FIRST_USER_ID + NUM_USERS))
    team_ids, team_edges, spine_leaf_id, cyclic_pairs, empty_team_id = build_teams(rng)
    membership_pool = [t for t in team_ids if t != empty_team_id]
    membership_edges, deep_user_ids = assign_users_to_teams(
        rng, user_ids, membership_pool, spine_leaf_id
    )
    org_ids, repo_ids, repo_org = build_resources(rng)
    grants, override_case = build_grants(rng, user_ids, team_ids, org_ids, repo_ids, repo_org)

    async with conn.transaction():
        await conn.copy_records_to_table(
            "subjects",
            records=[(uid, "user", f"user-{uid}") for uid in user_ids]
            + [(tid, "team", f"team-{tid}") for tid in team_ids],
            columns=["id", "type", "name"],
        )
        await conn.copy_records_to_table(
            "resources",
            records=[(oid, "org", None, f"org-{oid}") for oid in org_ids]
            + [(rid, "repo", repo_org[rid], f"repo-{rid}") for rid in repo_ids],
            columns=["id", "type", "org_id", "name"],
        )
        await conn.copy_records_to_table(
            "team_relationships",
            records=team_edges + membership_edges,
            columns=["subject_id", "belongs_to_team_id"],
        )
        await conn.copy_records_to_table(
            "grants",
            records=grants,
            columns=["subject_id", "role", "resource_id"],
        )
        # We inserted explicit ids above; bump the sequences so the app's
        # own inserts (grants, memberships) don't collide with them later.
        await conn.execute("SELECT setval('subjects_id_seq', (SELECT MAX(id) FROM subjects))")
        await conn.execute("SELECT setval('resources_id_seq', (SELECT MAX(id) FROM resources))")

    manifest = {
        "deep_user_ids": deep_user_ids,
        "spine_leaf_team_id": spine_leaf_id,
        "cyclic_team_pairs": cyclic_pairs,
        "empty_team_id": empty_team_id,
        "repo_beats_org_case": override_case,
    }
    manifest_path = Path(__file__).with_name("fixture_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2))

    await conn.close()
    print(
        f"Inserted {NUM_USERS} users, {NUM_TEAMS} teams, {NUM_ORGS} orgs, "
        f"{NUM_REPOS} repos, {len(grants)} grants."
    )
    print(f"Manifest written to {manifest_path}")


if __name__ == "__main__":
    asyncio.run(main())
