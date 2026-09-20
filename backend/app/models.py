from datetime import datetime
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel

SubjectType = Literal["user", "team"]
ResourceType = Literal["org", "repo"]


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


class SubjectRef(BaseModel):
    """A minimal reference to a user or team, used for a team's parent
    and children -- just enough to link to it, show a name, and tell a
    user apart from a nested team when rendering as a tree."""

    id: int
    name: str
    type: SubjectType


class OrgOut(BaseModel):
    id: int
    name: str


class RepoOut(BaseModel):
    id: int
    name: str
    org_id: int


class ResourceOut(BaseModel):
    id: int
    name: str
    type: ResourceType
    org_id: int | None


class TeamOut(BaseModel):
    id: int
    name: str
    parent: SubjectRef | None
    children: list[SubjectRef]


class GrantIn(BaseModel):
    subject_id: int
    role: Role
    resource_id: int


class GrantOut(BaseModel):
    id: int
    subject_id: int
    subject_name: str
    subject_type: SubjectType
    role: Role
    resource_id: int
    resource_name: str
    resource_type: ResourceType
