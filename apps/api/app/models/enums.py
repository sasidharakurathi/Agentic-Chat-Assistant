from __future__ import annotations

import enum


class MemberRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"

    @property
    def rank(self) -> int:
        return {"member": 0, "admin": 1, "owner": 2}[self.value]

    def satisfies(self, minimum: MemberRole) -> bool:
        return self.rank >= minimum.rank


__all__ = ["MemberRole"]
