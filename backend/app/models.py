from datetime import datetime
from enum import IntEnum

from pydantic import BaseModel


class Role(IntEnum):
    """Ordered so `role >= action` just works: higher roles imply lower ones."""

    READ = 1
    WRITE = 2
    ADMIN = 3
    OWNER = 4

    @classmethod
    def from_str(cls, value: str) -> "Role":
        return cls[value.upper()]


class ExplainResponse(BaseModel):
    """chain is a readable breadcrumb, e.g.
    ["alice", "platform-eng", "infrastructure", "acme-corp (write)", "repo: api-gateway"].
    reason is only set on a deny -- see checker.explain."""

    allowed: bool
    chain: list[str]
    granted_role: Role | None
    reason: str | None = None


class CheckResponse(BaseModel):
    """as_of is when the user's cached team set was computed, if this was
    a cache hit -- None on a cold check. Grants are always read live, so
    this is the only source of staleness in a check() answer at all."""

    allowed: bool
    as_of: datetime | None


class MembershipIn(BaseModel):
    subject_id: int
    belongs_to_team_id: int
