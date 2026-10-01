"""Outbound HTTP to a URL someone else chose, without letting it reach *us*.

Two places fetch URLs picked by a tenant or a model: the `http_request` tool
(the model picks) and URL data sources (a builder picks, the worker fetches).
Both run inside our network. Left unguarded, "fetch this URL" is also "fetch
http://localhost:8000/…", "fetch the Redis port", or "fetch
http://169.254.169.254/" (the cloud metadata service, which hands out the
machine's credentials). That is server-side request forgery (SSRF).

What this module enforces, on every hop of every request:

- **Scheme**: http and https only. No file:, gopher:, ftp:, data:.
- **No credentials in the URL** (`user:pass@host`).
- **Domain allowlist** when one is configured: the host must be an allowed
  domain or a subdomain of one.
- **Public addresses only.** The host is resolved and *every* address it
  resolves to must be globally routable: no loopback, private ranges,
  link-local (which is where metadata services live), CGNAT, multicast,
  reserved or unspecified addresses, and no IPv6 wrapper around one of those.
- **The connection goes to the address that was checked.** Resolving, checking
  and then letting the HTTP client resolve again would allow DNS rebinding:
  a hostname that answers with a public address for the check and a private
  one a moment later for the connection. So the request is sent to the
  checked IP, with the original name in the Host header and TLS SNI, and the
  certificate is still verified against that name.
- **Redirects are followed by hand** and each new location is checked again,
  up to a small limit. A public URL that redirects to localhost is the
  classic bypass.
- **Caps**: a wall-clock timeout, and the body is read only up to a byte
  limit, so a huge or endless response cannot tie up the worker.
- Environment proxies are ignored (`trust_env=False`); a proxy would do its
  own resolution and undo the pinning.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

import httpx

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
#: async (host, port) -> the addresses the host resolves to.
Resolver = Callable[[str, int], Awaitable[list[str]]]

ALLOWED_SCHEMES = frozenset({"http", "https"})
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
#: Request headers the caller may not set: the guard owns routing and framing.
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "proxy-authorization",
        "accept-encoding",
    }
)
#: What survives a redirect to a different origin. Anything else could be a
#: credential under a name nobody thought to list.
_SAFE_ACROSS_ORIGINS = frozenset(
    {"user-agent", "accept", "accept-language", "accept-encoding", "content-type"}
)
#: IPv6 ranges that carry an IPv4 address in their low 32 bits, which a
#: translator or an old stack delivers to that IPv4 host: NAT64 (well-known
#: and local-use), IPv4-compatible, and SIIT.
_EMBEDS_IPV4 = tuple(
    ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "::/96", "::ffff:0:0:0/96")
)
#: Deprecated site-local space: private by intent, "global" to `ipaddress`.
_SITE_LOCAL = ipaddress.ip_network("fec0::/10")
USER_AGENT = "AssistantStudio/1.0 (+outbound fetch)"


class SsrfBlocked(Exception):
    """The request was refused before anything was sent (or mid-redirect).

    The message is written for the person or model that asked, and never
    includes an internal address beyond the one they supplied themselves.
    """


@dataclass(frozen=True)
class FetchLimits:
    timeout_s: float = 15.0
    max_bytes: int = 1_000_000
    max_redirects: int = 3


@dataclass
class FetchResult:
    status: int
    reason: str
    url: str
    headers: dict[str, str]
    body: bytes
    truncated: bool
    redirects: list[str] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


def normalize_domain(domain: str) -> str:
    """`https://Example.com/path` or `*.example.com` -> `example.com`."""
    d = domain.strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split(":", 1)[0]
    return d.removeprefix("*.").strip(".")


def host_allowed(host: str, allowed_domains: Iterable[str]) -> bool:
    """True when there is no allowlist, or `host` is an allowed domain or a
    subdomain of one. `evil-example.com` does not match `example.com`."""
    raw = list(allowed_domains)
    if not raw:
        return True
    # A list whose entries all come to nothing ("*.", " ") is a mistake, not
    # "no restriction": it used to let every host through.
    allowed = [d for d in (normalize_domain(d) for d in raw) if d]
    h = host.lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in allowed)


def is_public_ip(ip: IpAddress) -> bool:
    """Globally routable, including once any IPv4 hiding inside an IPv6
    address has been unwrapped: mapped, 6to4, Teredo, and the ranges a
    NAT64 gateway or an old stack translates (`64:ff9b::a9fe:a9fe` is the
    cloud metadata address on a host with NAT64)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in _SITE_LOCAL:
            return False
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and any(ip in net for net in _EMBEDS_IPV4):
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            # The address is only as public as the IPv4 it stands for.
            return is_public_ip(inner)
        if inner is not None and not is_public_ip(inner):
            return False
    return ip.is_global and not ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


#: The same words whether the name doesn't exist or points inside: two
#: different answers would tell the asker which internal names resolve.
_NOT_REACHABLE = "{host} can't be reached: it has no public address"


async def resolve_public(host: str, port: int, resolver: Resolver = system_resolver) -> IpAddress:
    """The address to connect to, or `SsrfBlocked`.

    Every resolved address must be public, not just the first: a name that
    returns one public and one private address is refused rather than
    gambled on.
    """
    try:
        literal: IpAddress | None = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(literal):
            raise SsrfBlocked(f"{host} is not a public address")
        return literal

    try:
        addrs = await resolver(host, port)
    except (OSError, UnicodeError) as exc:
        raise SsrfBlocked(_NOT_REACHABLE.format(host=host)) from exc
    ips: list[IpAddress] = []
    for a in addrs:
        try:
            ips.append(ipaddress.ip_address(a.split("%", 1)[0]))
        except ValueError:
            continue
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise SsrfBlocked(_NOT_REACHABLE.format(host=host))
    return ips[0]


def _check_url(url: httpx.URL, allowed_domains: Iterable[str]) -> None:
    if url.scheme not in ALLOWED_SCHEMES:
        raise SsrfBlocked(f"only http and https URLs are allowed, not {url.scheme or 'none'}:")
    if not url.host:
        raise SsrfBlocked("the URL has no host")
    if url.userinfo:
        raise SsrfBlocked("URLs with a username or password are not allowed")
    if not host_allowed(url.host, allowed_domains):
        raise SsrfBlocked(f"{url.host} is not in this assistant's allowed domains")


def _clean_headers(headers: dict[str, str] | None) -> dict[str, str]:
    # `identity`: the size cap counts bytes as they arrive, so a body must
    # not be able to inflate after it. A compressed 200 KB answer used to
    # become 200 MB in memory under a 1 MB cap.
    out = {"user-agent": USER_AGENT, "accept-encoding": "identity"}
    for k, v in (headers or {}).items():
        key = str(k).strip().lower()
        if not key or key in _RESERVED_HEADERS:
            continue
        out[key] = str(v)
    return out


DEFAULT_LIMITS = FetchLimits()
_SEE_OTHER = 303
_MOVED = frozenset({301, 302})


@dataclass
class _Hop:
    """What to send next: the method, body and headers can all change on a
    redirect."""

    target: httpx.URL
    method: str
    content: bytes | None
    headers: dict[str, str]


def _port(url: httpx.URL) -> int:
    return url.port or (443 if url.scheme == "https" else 80)


def _follow(hop: _Hop, status: int, location: str) -> _Hop:
    nxt = hop.target.join(location)
    method, content = hop.method, hop.content
    # 303, and 301/302 after a write, turn into a GET without a body: what
    # browsers do, and it keeps a redirect from replaying a write elsewhere.
    if status == _SEE_OTHER or (status in _MOVED and method not in ("GET", "HEAD")):
        method, content = "GET", None
    headers = hop.headers
    was, now = hop.target, nxt
    if was.scheme == "https" and now.scheme != "https":
        # What was sent encrypted is not repeated in the clear.
        raise SsrfBlocked("the server redirected from https to http")
    if (was.scheme, was.host, _port(was)) != (now.scheme, now.host, _port(now)):
        # A different origin gets none of the caller's headers beyond the
        # harmless ones, and no replayed body: an API key can be in any
        # header, and a 307 would otherwise repeat a POST somewhere else.
        headers = {k: v for k, v in headers.items() if k in _SAFE_ACROSS_ORIGINS}
        if method not in ("GET", "HEAD"):
            method, content = "GET", None
    return _Hop(nxt, method, content, headers)


async def _send(client: httpx.AsyncClient, hop: _Hop, ip: IpAddress) -> httpx.Response:
    """One request, to the checked address, as the original host."""
    target = hop.target
    host_header = target.host if target.port is None else f"{target.host}:{target.port}"
    tls = {"sni_hostname": target.host} if target.scheme == "https" else {}
    request = client.build_request(
        hop.method,
        target.copy_with(host=str(ip)),
        headers={**hop.headers, "host": host_header},
        content=hop.content,
        extensions=tls,
    )
    return await client.send(request, stream=True)


async def safe_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: str | bytes | None = None,
    allowed_domains: Iterable[str] = (),
    limits: FetchLimits = DEFAULT_LIMITS,
    resolver: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """Send one request under the rules in the module docstring.

    `resolver` and `transport` exist for tests; production uses the system
    resolver and a real connection.
    """
    allowed = list(allowed_domains)
    try:
        target = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as exc:
        raise SsrfBlocked("that is not a valid URL") from exc
    hop = _Hop(
        target,
        method.upper(),
        body.encode() if isinstance(body, str) else body,
        _clean_headers(headers),
    )
    redirects: list[str] = []

    async with httpx.AsyncClient(
        transport=transport, follow_redirects=False, trust_env=False, timeout=limits.timeout_s
    ) as client:
        try:
            async with asyncio.timeout(limits.timeout_s):
                for _ in range(limits.max_redirects + 1):
                    _check_url(hop.target, allowed)
                    ip = await resolve_public(hop.target.host, _port(hop.target), resolver)
                    response = await _send(client, hop, ip)
                    try:
                        location = response.headers.get("location")
                        if response.status_code in _REDIRECT_CODES and location:
                            hop = _follow(hop, response.status_code, location)
                            redirects.append(str(hop.target))
                            continue
                        data, truncated = await _read_capped(response, limits.max_bytes)
                        return FetchResult(
                            status=response.status_code,
                            reason=response.reason_phrase,
                            url=str(hop.target),
                            headers={k.lower(): v for k, v in response.headers.items()},
                            body=data,
                            truncated=truncated,
                            redirects=redirects,
                        )
                    finally:
                        await response.aclose()
                raise SsrfBlocked(f"too many redirects (more than {limits.max_redirects})")
        except TimeoutError as exc:
            raise SsrfBlocked(f"the request took longer than {limits.timeout_s:g}s") from exc
        except httpx.HTTPError as exc:
            raise SsrfBlocked(f"the request failed: {type(exc).__name__}") from exc


def is_compressed(headers: httpx.Headers | dict[str, str]) -> bool:
    return str(headers.get("content-encoding", "")).strip().lower() not in ("", "identity")


async def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    # A server that compresses anyway (we asked for `identity`) is not read
    # at all: the client would inflate it, and the cap only sees the result.
    # The caller is told through the `content-encoding` header it kept.
    if is_compressed(response.headers):
        return b"", False
    buf = bytearray()
    async for chunk in response.aiter_bytes():
        buf.extend(chunk)
        if len(buf) > max_bytes:
            return bytes(buf[:max_bytes]), True
    return bytes(buf), False


__all__ = [
    "DEFAULT_LIMITS",
    "FetchLimits",
    "FetchResult",
    "SsrfBlocked",
    "host_allowed",
    "is_compressed",
    "is_public_ip",
    "normalize_domain",
    "resolve_public",
    "safe_request",
]
