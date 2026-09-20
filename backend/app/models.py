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
