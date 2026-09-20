from enum import IntEnum


class Role(IntEnum):
    """Ordered so `role >= action` just works: higher roles imply lower ones."""

    READ = 1
    WRITE = 2
    ADMIN = 3
    OWNER = 4

    @classmethod
    def from_str(cls, value: str) -> "Role":
        return cls[value.upper()]
