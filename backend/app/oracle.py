"""The correctness oracle for R4: answers checks the slow, obviously
correct way. No cache, no cleverness, and no shared code with
checker.py -- this is what checker.py's answers get checked against.
"""
from app import db
from app.models import Role


async def oracle_check(user_id: int, action: Role, repo_id: int) -> bool:
    pool = await db.get_pool()

    # Every subject the user is (transitively) a member of: a plain
    # breadth-first walk, one query per hop. The visited set is what
    # stops a cycle in the team data from looping forever.
    reachable = {user_id}
    frontier = [user_id]
    while frontier:
        rows = await pool.fetch(
            "SELECT belongs_to_team_id FROM team_relationships "
            "WHERE subject_id = ANY($1::bigint[]) AND belongs_to_team_id IS NOT NULL",
            frontier,
        )
        frontier = []
        for row in rows:
            team_id = row["belongs_to_team_id"]
            if team_id not in reachable:
                reachable.add(team_id)
                frontier.append(team_id)

    repo = await pool.fetchrow("SELECT org_id FROM resources WHERE id = $1", repo_id)
    resource_ids = [repo_id] if repo["org_id"] is None else [repo_id, repo["org_id"]]

    rows = await pool.fetch(
        "SELECT role FROM grants WHERE subject_id = ANY($1::bigint[]) "
        "AND resource_id = ANY($2::bigint[])",
        list(reachable),
        resource_ids,
    )
    best_role = None
    for row in rows:
        role = Role.from_str(row["role"])
        if best_role is None or role > best_role:
            best_role = role

    return best_role is not None and best_role >= action
