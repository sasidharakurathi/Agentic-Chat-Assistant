"""Internal destinations (Phase 9.3): company systems on the office network.

Outbound requests stay public-only, except to hosts the owner lists, each
with the address range it must resolve into, its ports and whether plain
http is allowed. This machine, link-local addresses and the platform's own
networks are refused whatever the list says. A fake resolver stands in for
DNS and a mock transport for the network, so every case is offline.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.security import ssrf
from app.security.ssrf import InternalDestination, SsrfBlocked, resolve_public, safe_request
from app.services import destinations
from httpx import AsyncClient
from sqlalchemy import select

from test_orgs import _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
GLPI = InternalDestination("glpi.corp.example.com", "10.20.0.0/16")
CORP = InternalDestination(".corp.example.com", "10.20.0.0/16", ports=(443, 8080), http=True)


def resolver_for(table: dict[str, list[str]]):
    async def resolve(host: str, _port: int) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return table[host]

    return resolve


DNS = resolver_for(
    {
        "glpi.corp.example.com": ["10.20.3.4"],
        "wiki.corp.example.com": ["10.20.9.9"],
        "moved.corp.example.com": ["10.99.0.1"],
        "loop.corp.example.com": ["127.0.0.1"],
        "meta.corp.example.com": ["169.254.169.254"],
        "studio.corp.example.com": ["172.30.0.5"],
        "elsewhere.example.net": ["10.20.3.4"],
    }
)


async def test_a_listed_host_reaches_its_range() -> None:
    ip = await resolve_public("glpi.corp.example.com", 443, DNS, internal=[GLPI])
    assert str(ip) == "10.20.3.4"


async def test_without_the_list_the_same_host_is_refused() -> None:
    with pytest.raises(SsrfBlocked):
        await resolve_public("glpi.corp.example.com", 443, DNS)


@pytest.mark.parametrize(
    ("host", "port", "scheme", "entries"),
    [
        # A name the entry doesn't cover, even resolving into the range.
        ("elsewhere.example.net", 443, "https", [GLPI]),
        ("wiki.corp.example.com", 443, "https", [GLPI]),
        # A listed name that now points outside its range.
        ("moved.corp.example.com", 443, "https", [CORP]),
        # A port the entry doesn't list.
        ("glpi.corp.example.com", 8443, "https", [GLPI]),
        # Plain http where the entry doesn't allow it.
        ("glpi.corp.example.com", 443, "http", [GLPI]),
        # This machine and link-local, whatever the range says.
        (
            "loop.corp.example.com",
            443,
            "https",
            [InternalDestination(".corp.example.com", "0.0.0.0/1")],
        ),
        (
            "meta.corp.example.com",
            443,
            "https",
            [InternalDestination(".corp.example.com", "169.0.0.0/8")],
        ),
    ],
)
async def test_what_the_entry_doesnt_say_is_refused(
    host: str, port: int, scheme: str, entries: list[InternalDestination]
) -> None:
    with pytest.raises(SsrfBlocked):
        await resolve_public(host, port, DNS, internal=entries, scheme=scheme)


async def test_a_suffix_covers_names_under_it_with_its_ports_and_http() -> None:
    ip = await resolve_public("wiki.corp.example.com", 8080, DNS, internal=[CORP], scheme="http")
    assert str(ip) == "10.20.9.9"
    assert not CORP.covers("corp.example.com"), "the suffix itself is not a name under it"
    assert not CORP.covers("evilcorp.example.com")


async def test_the_platforms_own_networks_are_never_reached(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "platform_networks", "172.30.0.0/16")
    ssrf.platform_denied.cache_clear()
    try:
        wide = InternalDestination(".corp.example.com", "172.16.0.0/12")
        with pytest.raises(SsrfBlocked):
            await resolve_public("studio.corp.example.com", 443, DNS, internal=[wide])
    finally:
        monkeypatch.undo()
        ssrf.platform_denied.cache_clear()


async def test_safe_request_sends_to_a_listed_host_over_http_only_if_allowed() -> None:
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text="ok")

    result = await safe_request(
        "GET",
        "http://wiki.corp.example.com:8080/page",
        allowed_domains=["wiki.corp.example.com"],
        resolver=DNS,
        transport=httpx.MockTransport(answer),
        internal=[CORP],
    )
    assert result.status == 200
    assert sent[0].url.host == "10.20.9.9"
    with pytest.raises(SsrfBlocked):
        await safe_request(
            "GET",
            "http://glpi.corp.example.com/",
            allowed_domains=["glpi.corp.example.com"],
            resolver=DNS,
            transport=httpx.MockTransport(answer),
            internal=[GLPI],
        )
    assert len(sent) == 1, "nothing sent for the refused one"


@pytest.mark.parametrize(
    ("entry", "said"),
    [
        ({"host": "glpi", "cidr": "10.0.0.0/8"}, None),
        ({"host": ".example", "cidr": "10.0.0.0/8"}, "a name like"),
        ({"host": "glpi.corp.example.com", "cidr": "127.0.0.0/8"}, "this machine"),
        ({"host": "glpi.corp.example.com", "cidr": "169.254.0.0/16"}, "this machine"),
        ({"host": "glpi.corp.example.com", "cidr": "0.0.0.0/0"}, "every address"),
        ({"host": "glpi.corp.example.com", "cidr": "nope"}, "address range"),
        ({"host": "glpi.corp.example.com", "cidr": "10.0.0.0/8", "ports": [0]}, "ports"),
        ({"host": "a b", "cidr": "10.0.0.0/8"}, "a name like"),
    ],
)
def test_an_entry_is_checked_when_saved(entry: dict[str, Any], said: str | None) -> None:
    if said is None:
        assert destinations.checked([entry])[0]["ports"] == [443]
        return
    with pytest.raises(ValueError, match=said):
        destinations.checked([entry])


def test_a_stored_entry_that_no_longer_validates_is_skipped() -> None:
    entries = destinations.entries_of(
        [{"host": "glpi.corp.example.com", "cidr": "10.20.0.0/16"}, {"host": "x", "cidr": "bad"}]
    )
    assert entries == (InternalDestination("glpi.corp.example.com", "10.20.0.0/16", (443,), False),)


async def test_the_list_is_checked_saved_and_audited(client: AsyncClient) -> None:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    h = {**owner.headers, "X-Org-Id": org["id"]}
    entry = {"host": "glpi.corp.example.com", "cidr": "10.20.0.0/16", "note": "IT tickets"}

    bad = await client.patch(
        f"{API}/orgs/{org['id']}/settings",
        json={"internal_destinations": [{**entry, "cidr": "127.0.0.0/8"}]},
        headers=h,
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_destination")

    r = await client.patch(
        f"{API}/orgs/{org['id']}/settings", json={"internal_destinations": [entry]}, headers=h
    )
    assert r.status_code == 200, r.text
    (saved,) = r.json()["internal_destinations"]
    assert (saved["host"], saved["cidr"], saved["ports"], saved["http"]) == (
        "glpi.corp.example.com",
        "10.20.0.0/16",
        [443],
        False,
    )
    assert await destinations.for_org(uuid.UUID(org["id"])) == (
        InternalDestination("glpi.corp.example.com", "10.20.0.0/16", (443,), False),
    )
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "org.settings.update"))
    assert row is not None and "internal_destinations" in row.meta["changed"]
