"""The fast path: check() is R1, explain() is R3. explain() deliberately
does not reuse get_team_set's cache -- see README's "Explain (R3)".
"""
from app import cache, db
from app.models import ExplainResponse, Role

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


# explain() never touches the cache: a flat team set has no parent-links,
# so it can't reconstruct a readable chain. It re-walks the graph fresh
# every time instead, one query per level -- fine, since explain isn't
# in the 5k/sec check path.
_TEAM_EDGES_FOR_SQL = """
SELECT subject_id, belongs_to_team_id
FROM team_relationships
WHERE subject_id = ANY($1::bigint[]) AND belongs_to_team_id IS NOT NULL
"""

_GRANTS_WITH_SUBJECT_SQL = """
SELECT g.subject_id, g.role, g.resource_id
FROM grants g
JOIN resources r ON r.id = $2
WHERE g.subject_id = ANY($1::bigint[])
  AND g.resource_id IN ($2, r.org_id)
"""


async def _walk_team_graph(user_id: int) -> tuple[dict[int, int], list[int]]:
    """BFS outward from the user. Returns (predecessor, order):
    predecessor maps each team reached to whoever led to it, so a
    shortest path back to the user can be rebuilt; order is discovery
    order, user first. A subject is only ever added once, which is what
    keeps this safe on the fixture's cyclic teams.
    """
    pool = await db.get_pool()
    predecessor: dict[int, int] = {}
    order = [user_id]
    frontier = [user_id]
    visited = {user_id}
    while frontier:
        rows = await pool.fetch(_TEAM_EDGES_FOR_SQL, frontier)
        frontier = []
        for row in rows:
            team_id = row["belongs_to_team_id"]
            if team_id in visited:
                continue
            visited.add(team_id)
            predecessor[team_id] = row["subject_id"]
            order.append(team_id)
            frontier.append(team_id)
    return predecessor, order


def _path_to(subject_id: int, predecessor: dict[int, int]) -> list[int]:
    path = [subject_id]
    while path[-1] in predecessor:
        path.append(predecessor[path[-1]])
    path.reverse()
    return path


async def _subject_names(subject_ids: list[int]) -> dict[int, str]:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT id, name FROM subjects WHERE id = ANY($1::bigint[])", subject_ids
    )
    return {row["id"]: row["name"] for row in rows}


async def _resource_names(resource_ids: list[int]) -> dict[int, str]:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT id, name FROM resources WHERE id = ANY($1::bigint[])", resource_ids
    )
    return {row["id"]: row["name"] for row in rows}


async def explain(user_id: int, action: Role, repo_id: int) -> ExplainResponse:
    predecessor, order = await _walk_team_graph(user_id)
    pool = await db.get_pool()
    grant_rows = await pool.fetch(_GRANTS_WITH_SUBJECT_SQL, order, repo_id)

    if not grant_rows:
        names = await _subject_names([user_id])
        return ExplainResponse(
            allowed=False,
            chain=[names.get(user_id, str(user_id))],
            granted_role=None,
            reason="No grant found on this repo or its org, for this user or any team they belong to.",
        )

    # Highest role wins; among ties, the one reached first in the BFS
    # (i.e. the shortest, simplest chain) is the one we explain with.
    max_role = max(Role.from_str(row["role"]) for row in grant_rows)
    candidates = [row for row in grant_rows if Role.from_str(row["role"]) == max_role]
    order_index = {subject_id: i for i, subject_id in enumerate(order)}
    best = min(candidates, key=lambda row: order_index[row["subject_id"]])

    path = _path_to(best["subject_id"], predecessor)
    subject_names = await _subject_names(path)
    resource_names = await _resource_names([best["resource_id"], repo_id])

    chain = [subject_names[subject_id] for subject_id in path]
    chain.append(f"{resource_names[best['resource_id']]} ({max_role.name.lower()})")
    if best["resource_id"] != repo_id:
        chain.append(f"repo: {resource_names[repo_id]}")

    allowed = max_role >= action
    reason = None
    if not allowed:
        reason = (
            f"Highest role found is {max_role.name.lower()}, "
            f"but {action.name.lower()} was requested."
        )
    return ExplainResponse(allowed=allowed, chain=chain, granted_role=max_role, reason=reason)
