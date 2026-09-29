"""The SSRF guard, as a transport for the MCP SDK's HTTP client (task 4.5).

`app.security.ssrf.safe_request` protects single requests we make ourselves.
The MCP SDK makes its own: `streamable_http_client` and `sse_client` hold a
client (an `httpx2.AsyncClient`) for the whole session. This transport
plugs the same rules into that client, so *every* request it sends is
checked and pinned:

- the scheme is https (http only with `allow_insecure`);
- the host resolves only to public addresses (private ones only with
  `allow_private`, for a server on this machine while testing);
- the connection goes to the address that was checked, with the original
  name in the Host header and TLS SNI, so DNS rebinding cannot swap in an
  internal address between check and connect.

The client using it must not follow redirects (the SDK's default client
does), or a redirect would be the way around all of this.
"""

from __future__ import annotations

import httpx2

from app.security.ssrf import Resolver, SsrfBlocked, resolve_public, system_resolver


class PinnedTransport(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        *,
        allow_insecure: bool = False,
        allow_private: bool = False,
        resolver: Resolver = system_resolver,
        inner: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._allow_insecure = allow_insecure
        self._allow_private = allow_private
        self._resolver = resolver
        self._inner = inner or httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if url.scheme not in ("https", "http") or not url.host:
            raise SsrfBlocked("only http(s) URLs can be reached")
        if url.scheme == "http" and not self._allow_insecure:
            raise SsrfBlocked("the server must be reached over https")
        if self._allow_private:
            return await self._inner.handle_async_request(request)

        port = url.port or (443 if url.scheme == "https" else 80)
        ip = await resolve_public(url.host, port, self._resolver)
        request.url = url.copy_with(host=str(ip))
        request.headers["host"] = url.host if url.port is None else f"{url.host}:{url.port}"
        if url.scheme == "https":
            request.extensions["sni_hostname"] = url.host
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def pinned_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | float | None = None,
    auth: httpx2.Auth | None = None,
    *,
    allow_insecure: bool = False,
    allow_private: bool = False,
    resolver: Resolver = system_resolver,
    inner: httpx2.AsyncBaseTransport | None = None,
) -> httpx2.AsyncClient:
    """An MCP-ready client that never follows redirects and ignores proxy
    settings from the environment (a proxy would resolve the name itself)."""
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout if timeout is not None else httpx2.Timeout(30.0, read=300.0),
        auth=auth,
        follow_redirects=False,
        trust_env=False,
        transport=PinnedTransport(
            allow_insecure=allow_insecure,
            allow_private=allow_private,
            resolver=resolver,
            inner=inner,
        ),
    )


__all__ = ["PinnedTransport", "pinned_client"]
