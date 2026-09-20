"""HTTP endpoints. Thin wrappers over checker.py / cache.py / db.py -- no
business logic lives here.
"""
from fastapi import APIRouter, HTTPException

from app import cache, checker, db
from app.models import (
    CheckResponse,
    ExplainResponse,
    GrantIn,
    GrantOut,
    MembershipIn,
    OrgOut,
    RepoOut,
    ResourceOut,
    Role,
    SubjectRef,
    TeamOut,
)

router = APIRouter()


def _parse_role(action: str) -> Role:
    try:
        return Role.from_str(action)
    except KeyError:
        raise HTTPException(
            400, f"Unknown action '{action}'. Use one of: read, write, admin, owner."
        )


@router.get("/check", response_model=CheckResponse)
async def check(user_id: int, action: str, repo_id: int):
    role = _parse_role(action)
    allowed = await checker.check(user_id, role, repo_id)
    cached = cache.get(user_id)
    as_of = cached[1] if cached else None
    return CheckResponse(allowed=allowed, as_of=as_of)


@router.get("/explain", response_model=ExplainResponse)
async def explain(user_id: int, action: str, repo_id: int):
    role = _parse_role(action)
    return await checker.explain(user_id, role, repo_id)


async def _subject_type(conn, subject_id: int) -> str:
    row = await conn.fetchrow("SELECT type FROM subjects WHERE id = $1", subject_id)
    if row is None:
        raise HTTPException(404, f"No subject with id {subject_id}")
    return row["type"]


def _invalidate_for(subject_id: int, subject_type: str) -> None:
    """A user's own membership changed -> just their entry. A team's did
    (it moved to a new parent) -> the whole cache, since we don't track
    which users sit under which team. See README's "Fast checks vs fast
    revocation"."""
    if subject_type == "user":
        cache.invalidate_user(subject_id)
    else:
        cache.invalidate_all()


@router.post("/membership", status_code=201)
async def add_membership(body: MembershipIn):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        subject_type = await _subject_type(conn, body.subject_id)
        await conn.execute(
            "INSERT INTO team_relationships (subject_id, belongs_to_team_id) VALUES ($1, $2)",
            body.subject_id,
            body.belongs_to_team_id,
        )
    _invalidate_for(body.subject_id, subject_type)
    return {"status": "created"}


@router.delete("/membership/{membership_id}", status_code=204)
async def remove_membership(membership_id: int):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT subject_id FROM team_relationships WHERE id = $1", membership_id
        )
        if row is None:
            raise HTTPException(404, f"No membership with id {membership_id}")
        subject_type = await _subject_type(conn, row["subject_id"])
        await conn.execute("DELETE FROM team_relationships WHERE id = $1", membership_id)
    _invalidate_for(row["subject_id"], subject_type)


@router.get("/orgs", response_model=list[OrgOut])
async def list_orgs():
    pool = await db.get_pool()
    rows = await pool.fetch("SELECT id, name FROM resources WHERE type = 'org' ORDER BY id")
    return [OrgOut(**dict(row)) for row in rows]


@router.get("/orgs/{org_id}/repos", response_model=list[RepoOut])
async def list_org_repos(org_id: int):
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT id, name, org_id FROM resources WHERE type = 'repo' AND org_id = $1 ORDER BY id",
        org_id,
    )
    return [RepoOut(**dict(row)) for row in rows]


@router.get("/resource/{resource_id}", response_model=ResourceOut)
async def get_resource(resource_id: int):
    pool = await db.get_pool()
    row = await pool.fetchrow(
        "SELECT id, name, type, org_id FROM resources WHERE id = $1", resource_id
    )
    if row is None:
        raise HTTPException(404, f"No resource with id {resource_id}")
    return ResourceOut(**dict(row))


@router.get("/resource/{resource_id}/grants", response_model=list[GrantOut])
async def get_resource_grants(resource_id: int):
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT id, subject_id, role, resource_id FROM grants WHERE resource_id = $1 ORDER BY id",
        resource_id,
    )
    return [GrantOut(**{**dict(row), "role": Role.from_str(row["role"])}) for row in rows]


@router.get("/teams/{team_id}", response_model=TeamOut)
async def get_team(team_id: int):
    pool = await db.get_pool()
    team_row = await pool.fetchrow(
        "SELECT id, name FROM subjects WHERE id = $1 AND type = 'team'", team_id
    )
    if team_row is None:
        raise HTTPException(404, f"No team with id {team_id}")

    parent_row = await pool.fetchrow(
        "SELECT s.id, s.name FROM team_relationships tr "
        "JOIN subjects s ON s.id = tr.belongs_to_team_id "
        "WHERE tr.subject_id = $1 LIMIT 1",
        team_id,
    )
    child_rows = await pool.fetch(
        "SELECT s.id, s.name FROM team_relationships tr "
        "JOIN subjects s ON s.id = tr.subject_id "
        "WHERE tr.belongs_to_team_id = $1 ORDER BY s.id",
        team_id,
    )
    return TeamOut(
        id=team_row["id"],
        name=team_row["name"],
        parent=SubjectRef(**dict(parent_row)) if parent_row else None,
        children=[SubjectRef(**dict(row)) for row in child_rows],
    )


# A subject can have any number of grants, and the table can be huge (the
# fixture alone has ~400k rows), so this always requires a subject_id --
# no unfiltered dump of the whole table. /resource/{id}/grants above is
# the mirror image of this (resource -> its grants, this is subject ->
# its grants).
@router.get("/grant", response_model=list[GrantOut])
async def list_grants(subject_id: int):
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT id, subject_id, role, resource_id FROM grants WHERE subject_id = $1 ORDER BY id",
        subject_id,
    )
    return [GrantOut(**{**dict(row), "role": Role.from_str(row["role"])}) for row in rows]


@router.post("/grant", status_code=201, response_model=GrantOut)
async def create_grant(body: GrantIn):
    pool = await db.get_pool()
    row = await pool.fetchrow(
        "INSERT INTO grants (subject_id, role, resource_id) VALUES ($1, $2, $3) "
        "RETURNING id, subject_id, role, resource_id",
        body.subject_id,
        body.role.name.lower(),
        body.resource_id,
    )
    return GrantOut(**{**dict(row), "role": Role.from_str(row["role"])})
