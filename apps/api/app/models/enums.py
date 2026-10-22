from __future__ import annotations

import enum


class MemberRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    #: Builds assistants in the Studio, and chats.
    member = "member"
    #: Chats, and nothing else (Phase 8.1, plan D2): below the Studio floor,
    #: so every route refuses it except those that ask for Chat on purpose.
    employee = "employee"

    @property
    def rank(self) -> int:
        # A value missing here raises KeyError: a 500, never a way in.
        return {"employee": -1, "member": 0, "admin": 1, "owner": 2}[self.value]

    def satisfies(self, minimum: MemberRole) -> bool:
        return self.rank >= minimum.rank


__all__ = ["MemberRole"]
