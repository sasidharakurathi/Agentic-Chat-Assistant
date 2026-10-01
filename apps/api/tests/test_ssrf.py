"""The SSRF guard (task 4.1): what may be fetched, and how.

A mock transport stands in for the network and a fake resolver for DNS, so
every case is offline and exact: the test sees the precise request that
would have been sent, or proves nothing was sent at all.
"""

from __future__ import annotations

import asyncio
import ipaddress

import httpx
import pytest
from app.security.ssrf import (
    FetchLimits,
    SsrfBlocked,
    host_allowed,
    is_public_ip,
    normalize_domain,
    resolve_public,
    safe_request,
)

pytestmark = pytest.mark.anyio

PUBLIC = "93.184.216.34"


def resolver_for(table: dict[str, list[str]]):
    async def resolve(host: str, _port: int) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return table[host]

    return resolve


class Recorder:
    """A transport that records requests and answers from a script."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.pop(0) if self._responses else httpx.Response(200, text="ok")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


# ── addresses ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "fd00:ec2::254",  # AWS metadata over IPv6
    ],
)
def test_internal_addresses_are_not_public(addr: str) -> None:
    assert not is_public_ip(ipaddress.ip_address(addr))


@pytest.mark.parametrize("addr", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_public(addr: str) -> None:
    assert is_public_ip(ipaddress.ip_address(addr))


async def test_every_resolved_address_must_be_public() -> None:
    resolve = resolver_for({"mixed.test": [PUBLIC, "10.0.0.5"]})
    with pytest.raises(SsrfBlocked, match="no public address"):
        await resolve_public("mixed.test", 443, resolve)


# ── domains ──────────────────────────────────────────────────


def test_domain_allowlist_matches_subdomains_only() -> None:
    allowed = ["example.com"]
    assert host_allowed("example.com", allowed)
    assert host_allowed("api.example.com", allowed)
    assert not host_allowed("evil-example.com", allowed)
    assert not host_allowed("example.com.evil.net", allowed)
    assert host_allowed("anything.net", [])


def test_domains_are_normalized() -> None:
    assert normalize_domain("https://Example.com/path") == "example.com"
    assert normalize_domain("*.example.com") == "example.com"
    assert normalize_domain("example.com:8443") == "example.com"


# ── requests ─────────────────────────────────────────────────


async def test_the_request_goes_to_the_checked_address_as_the_original_host() -> None:
    rec = Recorder(httpx.Response(200, json={"ok": True}))
    result = await safe_request(
        "GET",
        "https://api.example.com/v1/items?q=1",
        resolver=resolver_for({"api.example.com": [PUBLIC]}),
        transport=rec.transport,
    )
    assert result.status == 200
    (sent,) = rec.requests
    assert sent.url.host == PUBLIC
    assert sent.url.path == "/v1/items" and sent.url.query == b"q=1"
    assert sent.headers["host"] == "api.example.com"
    # TLS is still negotiated, and the certificate checked, for the name.
    assert sent.extensions["sni_hostname"] == "api.example.com"


async def test_ipv6_addresses_are_pinned_too() -> None:
    rec = Recorder()
    await safe_request(
        "GET",
        "http://v6.example.com:8080/",
        resolver=resolver_for({"v6.example.com": ["2606:4700:4700::1111"]}),
        transport=rec.transport,
    )
    (sent,) = rec.requests
    assert sent.url.host == "2606:4700:4700::1111"
    assert sent.headers["host"] == "v6.example.com:8080"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", "only http and https"),
        ("gopher://example.com/", "only http and https"),
        ("http://user:pw@example.com/", "username or password"),
        ("http://127.0.0.1:6379/", "not a public address"),
        ("http://169.254.169.254/latest/meta-data/", "not a public address"),
        ("http://[::1]:8000/", "not a public address"),
        ("http://internal.test/", "no public address"),
        ("http://nowhere.test/", "no public address"),
    ],
)
async def test_refused_before_anything_is_sent(url: str, reason: str) -> None:
    rec = Recorder()
    with pytest.raises(SsrfBlocked, match=reason):
        await safe_request(
            "GET",
            url,
            resolver=resolver_for({"internal.test": ["10.0.0.5"], "example.com": [PUBLIC]}),
            transport=rec.transport,
        )
    assert rec.requests == []


async def test_a_redirect_into_the_network_is_refused() -> None:
    rec = Recorder(httpx.Response(302, headers={"location": "http://127.0.0.1:8000/admin"}))
    with pytest.raises(SsrfBlocked, match="not a public address"):
        await safe_request(
            "GET",
            "http://example.com/",
            resolver=resolver_for({"example.com": [PUBLIC]}),
            transport=rec.transport,
        )
    assert len(rec.requests) == 1  # the redirect target was never contacted


async def test_a_redirect_is_checked_against_the_allowlist() -> None:
    rec = Recorder(httpx.Response(301, headers={"location": "https://elsewhere.net/"}))
    with pytest.raises(SsrfBlocked, match="allowed domains"):
        await safe_request(
            "GET",
            "https://example.com/",
            allowed_domains=["example.com"],
            resolver=resolver_for({"example.com": [PUBLIC], "elsewhere.net": [PUBLIC]}),
            transport=rec.transport,
        )


async def test_redirects_drop_credentials_across_hosts_and_writes_on_303() -> None:
    rec = Recorder(
        httpx.Response(303, headers={"location": "https://cdn.example.net/result"}),
        httpx.Response(200, text="done"),
    )
    result = await safe_request(
        "POST",
        "https://example.com/submit",
        headers={"Authorization": "Bearer abc", "X-Trace": "1"},
        body='{"a": 1}',
        resolver=resolver_for({"example.com": [PUBLIC], "cdn.example.net": ["8.8.8.8"]}),
        transport=rec.transport,
    )
    first, second = rec.requests
    assert first.method == "POST" and first.headers["authorization"] == "Bearer abc"
    assert second.method == "GET" and second.content == b""
    assert "authorization" not in second.headers
    # Another host gets none of the caller's headers: a key can hide in any.
    assert "x-trace" not in second.headers
    assert result.redirects == ["https://cdn.example.net/result"]
    assert result.url == "https://cdn.example.net/result"


async def test_redirect_loops_stop() -> None:
    rec = Recorder(*[httpx.Response(302, headers={"location": "/again"}) for _ in range(10)])
    with pytest.raises(SsrfBlocked, match="too many redirects"):
        await safe_request(
            "GET",
            "https://example.com/",
            limits=FetchLimits(max_redirects=2),
            resolver=resolver_for({"example.com": [PUBLIC]}),
            transport=rec.transport,
        )
    assert len(rec.requests) == 3


async def test_the_body_is_read_only_up_to_the_cap() -> None:
    rec = Recorder(httpx.Response(200, content=b"x" * 5000))
    result = await safe_request(
        "GET",
        "https://example.com/big",
        limits=FetchLimits(max_bytes=1000),
        resolver=resolver_for({"example.com": [PUBLIC]}),
        transport=rec.transport,
    )
    assert len(result.body) == 1000 and result.truncated


async def test_a_slow_server_times_out() -> None:
    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)  # pragma: no cover

    with pytest.raises(SsrfBlocked, match="longer than"):
        await safe_request(
            "GET",
            "https://example.com/",
            limits=FetchLimits(timeout_s=0.2),
            resolver=resolver_for({"example.com": [PUBLIC]}),
            transport=httpx.MockTransport(slow),
        )


async def test_callers_cannot_set_routing_headers() -> None:
    rec = Recorder()
    await safe_request(
        "GET",
        "https://example.com/",
        headers={"Host": "169.254.169.254", "Transfer-Encoding": "chunked"},
        resolver=resolver_for({"example.com": [PUBLIC]}),
        transport=rec.transport,
    )
    (sent,) = rec.requests
    assert sent.headers["host"] == "example.com"
    assert "transfer-encoding" not in sent.headers


# ── the security pass (task 6.3) ─────────────────────────────


@pytest.mark.parametrize(
    "addr",
    [
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254, the metadata address
        "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
        "64:ff9b:1::7f00:1",  # local-use NAT64 of 127.0.0.1
        "::7f00:1",  # IPv4-compatible 127.0.0.1
        "::a9fe:a9fe",
        "::ffff:0:7f00:1",  # SIIT
        "fec0::1",  # deprecated site-local
    ],
)
def test_ipv6_forms_that_carry_an_internal_ipv4_are_not_public(addr: str) -> None:
    assert not is_public_ip(ipaddress.ip_address(addr))


def test_nat64_of_a_public_address_is_still_public() -> None:
    """An IPv6-only host reaches the IPv4 internet through these: blocking
    the whole range would cut it off from every IPv4-only site."""
    assert is_public_ip(ipaddress.ip_address("64:ff9b::808:808"))  # 8.8.8.8


async def test_a_nat64_literal_for_the_metadata_address_is_refused() -> None:
    rec = Recorder(httpx.Response(200, text="secrets"))
    with pytest.raises(SsrfBlocked, match="not a public address"):
        await safe_request(
            "GET", "http://[64:ff9b::a9fe:a9fe]/latest/meta-data/", transport=rec.transport
        )
    assert rec.requests == []


def test_an_allowlist_that_says_nothing_usable_allows_nothing() -> None:
    for broken in (["*."], ["."], [" "], ["https://"]):
        assert not host_allowed("anything.org", broken), broken
    assert host_allowed("anything.org", []), "no list at all still means unrestricted"
    assert host_allowed("api.example.com", ["*.", "example.com"])


async def test_missing_and_internal_names_get_the_same_answer() -> None:
    """Two different messages would say which internal names exist."""
    resolve = resolver_for({"internal.test": ["10.0.0.5"]})
    messages = []
    for host in ("internal.test", "nowhere.test"):
        with pytest.raises(SsrfBlocked) as blocked:
            await resolve_public(host, 443, resolve)
        messages.append(str(blocked.value).replace(host, "HOST"))
    assert messages[0] == messages[1] == "HOST can't be reached: it has no public address"


async def test_a_compressed_body_is_never_inflated() -> None:
    import gzip

    bomb = gzip.compress(b"A" * 5_000_000)
    assert len(bomb) < 10_000
    rec = Recorder(
        httpx.Response(
            200, content=bomb, headers={"content-encoding": "gzip", "content-type": "text/plain"}
        )
    )
    result = await safe_request(
        "GET",
        "https://example.com/",
        headers={"Accept-Encoding": "gzip, br"},
        resolver=resolver_for({"example.com": [PUBLIC]}),
        transport=rec.transport,
        limits=FetchLimits(max_bytes=1000),
    )
    # We ask for an uncompressed body, whatever the caller wanted...
    assert rec.requests[0].headers["accept-encoding"] == "identity"
    # ...and one that is compressed anyway is not read at all.
    assert result.body == b"" and result.headers["content-encoding"] == "gzip"


async def test_https_is_never_redirected_down_to_http() -> None:
    rec = Recorder(httpx.Response(302, headers={"location": "http://example.com/plain"}))
    with pytest.raises(SsrfBlocked, match="redirected from https to http"):
        await safe_request(
            "GET",
            "https://example.com/",
            headers={"Authorization": "Bearer abc"},
            resolver=resolver_for({"example.com": [PUBLIC]}),
            transport=rec.transport,
        )
    assert len(rec.requests) == 1


async def test_another_origin_gets_no_caller_headers_and_no_replayed_body() -> None:
    rec = Recorder(
        httpx.Response(307, headers={"location": "https://example.com:8443/elsewhere"}),
        httpx.Response(200, text="ok"),
    )
    await safe_request(
        "POST",
        "https://example.com/submit",
        headers={"Authorization": "Bearer abc", "X-Api-Key": "k", "Cookie": "s=1", "Accept": "*/*"},
        body='{"password": "p"}',
        resolver=resolver_for({"example.com": [PUBLIC]}),
        transport=rec.transport,
    )
    first, second = rec.requests
    assert first.headers["x-api-key"] == "k"
    # The same host on another port is another origin.
    for name in ("authorization", "x-api-key", "cookie"):
        assert name not in second.headers, name
    assert second.headers["accept"] == "*/*"
    assert second.method == "GET" and second.content == b"", "a 307 must not replay the body"


async def test_the_same_origin_keeps_its_headers_across_a_redirect() -> None:
    rec = Recorder(
        httpx.Response(302, headers={"location": "/moved"}),
        httpx.Response(200, text="ok"),
    )
    await safe_request(
        "GET",
        "https://example.com/",
        headers={"Authorization": "Bearer abc"},
        resolver=resolver_for({"example.com": [PUBLIC]}),
        transport=rec.transport,
    )
    assert rec.requests[1].headers["authorization"] == "Bearer abc"


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("text/html; charset=windows-1252", "cp1252"),
        ('text/html; charset="UTF-8"', "utf-8"),
        ("text/html; charset=ISO-8859-1", "iso8859-1"),
        ("text/html", "utf-8"),
        # Codecs that are not text encodings: minutes of CPU, or an exception.
        ("text/html; charset=punycode", "utf-8"),
        ("text/html; charset=idna", "utf-8"),
        ("text/html; charset=undefined", "utf-8"),
        ("text/html; charset=zlib", "utf-8"),
        ("text/html; charset=no-such-thing", "utf-8"),
    ],
)
def test_a_page_is_only_read_with_a_real_text_encoding(content_type: str, expected: str) -> None:
    from app.rag.fetch import page_charset

    assert page_charset(content_type) == expected


def test_a_url_source_must_be_a_web_address() -> None:
    from app.schemas.data_source import CreateUrlSource
    from pydantic import ValidationError

    assert (
        CreateUrlSource(name="Docs", url=" https://example.com/a ").url == "https://example.com/a"
    )
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "example.com"):
        with pytest.raises(ValidationError):
            CreateUrlSource(name="x", url=bad)
