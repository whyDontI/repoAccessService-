"""HTTP endpoints. Thin wrappers over checker.py / cache.py / db.py -- no
business logic lives here.
"""
from fastapi import APIRouter, HTTPException

from app import cache, checker, db
from app.models import CheckResponse, ExplainResponse, MembershipIn, Role

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
