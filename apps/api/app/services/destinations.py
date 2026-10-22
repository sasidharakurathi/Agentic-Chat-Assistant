"""An organisation's internal destinations (Phase 9.3).

Systems on the office network or VPN its assistants may reach: an
intranet wiki for URL sources, a ticketing API for HTTP connections, an
MCP server inside. Everything else outbound stays public-only
(`security/ssrf.py`). The owner edits the list in the organisation's
settings, and each change is in the audit log with its before and after
(`org_settings.update`).
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.security.ssrf import ALWAYS_DENIED, InternalDestination

#: An exact name, a suffix with a leading dot, or an address.
_HOST = re.compile(r"^\.?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
#: The most entries an organisation keeps.
MAX_ENTRIES = 50
#: Each field as the settings page names it.
_LABELS = {"host": "host", "cidr": "address range", "ports": "ports", "note": "what it is"}


class DestinationIn(BaseModel):
    """One entry as the owner writes it."""

    host: str = Field(min_length=1, max_length=253)
    cidr: str = Field(min_length=3, max_length=49)
    ports: list[int] = Field(default_factory=lambda: [443], min_length=1, max_length=20)
    http: bool = False
    #: Who this is, for the people reading the list ("GLPI, IT's tickets").
    note: str = Field(default="", max_length=200)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        h = v.strip().lower().rstrip(".")
        try:
            ipaddress.ip_address(h.strip("[]"))
            return h.strip("[]")
        except ValueError:
            pass
        if not _HOST.match(h) or (h.startswith(".") and h.count(".") < 2):  # noqa: PLR2004
            raise ValueError("a name like glpi.corp.example.com, .corp.example.com, or an address")
        return h

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, v: str) -> str:
        try:
            net = ipaddress.ip_network(v.strip(), strict=False)
        except ValueError as exc:
            raise ValueError("an address range like 10.20.0.0/16") from exc
        if net.prefixlen == 0:
            raise ValueError("that range is every address: name the network the system is on")
        if any(net.overlaps(d) for d in ALWAYS_DENIED if d.version == net.version):
            raise ValueError("that range includes this machine or link-local addresses")
        return str(net)

    @field_validator("ports")
    @classmethod
    def _ports(cls, v: list[int]) -> list[int]:
        if any(p < 1 or p > 65535 for p in v):  # noqa: PLR2004
            raise ValueError("ports are between 1 and 65535")
        return sorted(set(v))


def checked(entries: list[Any]) -> list[dict[str, Any]]:
    """The list as stored: each entry validated. Raises ValueError."""
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"at most {MAX_ENTRIES} internal destinations")
    out = []
    for i, raw in enumerate(entries, 1):
        try:
            out.append(DestinationIn.model_validate(raw).model_dump())
        except ValidationError as exc:
            # One readable sentence, not pydantic's dump.
            first = exc.errors()[0]
            loc = str((first.get("loc") or ("",))[0])
            field = _LABELS.get(loc, loc)
            why = str(first.get("msg", "isn't valid")).removeprefix("Value error, ")
            raise ValueError(f"Internal destination {i}, {field}: {why}") from exc
    return out


def entries_of(stored: list[dict[str, Any]]) -> tuple[InternalDestination, ...]:
    """What the guard reads; an entry that no longer validates is skipped."""
    out = []
    for raw in stored or []:
        try:
            e = DestinationIn.model_validate(raw)
        except ValueError:
            continue
        out.append(InternalDestination(e.host, e.cidr, tuple(e.ports), e.http))
    return tuple(out)


async def for_org(org_id: uuid.UUID | None) -> tuple[InternalDestination, ...]:
    """An organisation's entries, read in a short session of its own."""
    if org_id is None:
        return ()
    from app.db.session import get_sessionmaker
    from app.services import org_settings

    async with get_sessionmaker()() as session:
        switches = await org_settings.switches(session, org_id)
    return entries_of(switches.internal_destinations)


__all__ = ["MAX_ENTRIES", "DestinationIn", "checked", "entries_of", "for_org"]
