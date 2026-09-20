"""The fast path: R1 (check) lives here. explain() (R3) comes later and
deliberately does not reuse get_team_set's cache -- see README.
"""
from app import cache, db
from app.models import Role

# Walks team_relationships upward from a user, collecting every team
# reachable (direct or nested). The visited array stops it looping on a
# cycle -- the fixture has some on purpose, see plan.md.
_ANCESTOR_TEAMS_SQL = """
WITH RECURSIVE ancestors(team_id, visited) AS (
    SELECT belongs_to_team_id, ARRAY[belongs_to_team_id]
    FROM team_relationships
    WHERE subject_id = $1 AND belongs_to_team_id IS NOT NULL
  UNION ALL
    SELECT tr.belongs_to_team_id, a.visited || tr.belongs_to_team_id
    FROM team_relationships tr
    JOIN ancestors a ON tr.subject_id = a.team_id
    WHERE tr.belongs_to_team_id IS NOT NULL
      AND NOT (tr.belongs_to_team_id = ANY(a.visited))
)
SELECT DISTINCT team_id FROM ancestors
"""

# A grant counts if it's on the repo itself or on the repo's org -- an org
# grant covers its repos, but a repo-level grant can still be higher (see
# README's "Org grants cover their repos, but don't cap them").
_ROLE_LOOKUP_SQL = """
SELECT g.role
FROM grants g
JOIN resources r ON r.id = $2
WHERE g.subject_id = ANY($1::bigint[])
  AND g.resource_id IN ($2, r.org_id)
"""


async def get_team_set(user_id: int) -> set[int]:
    cached = cache.get(user_id)
    if cached is not None:
        team_ids, _cached_at = cached
        return team_ids

    pool = await db.get_pool()
    rows = await pool.fetch(_ANCESTOR_TEAMS_SQL, user_id)
    team_ids = {row["team_id"] for row in rows}
    cache.set(user_id, team_ids)
    return team_ids


async def resolve_role(subject_ids: set[int], repo_id: int) -> Role | None:
    pool = await db.get_pool()
    rows = await pool.fetch(_ROLE_LOOKUP_SQL, list(subject_ids), repo_id)
    roles = [Role.from_str(row["role"]) for row in rows]
    return max(roles) if roles else None


async def check(user_id: int, action: Role, repo_id: int) -> bool:
    team_ids = await get_team_set(user_id)
    role = await resolve_role({user_id} | team_ids, repo_id)
    return role is not None and role >= action
