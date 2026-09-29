"""The `http_request` capability, its approval rules, and web search limits
(task 4.1)."""

from __future__ import annotations

import functools
from typing import Any

import httpx
import pytest
from app.agent import caps_http
from app.agent.approvals import build_can_use_tool, classify
from app.agent.caps_http import build_http_tool
from app.agent.driver import FakeDriver
from app.agent.events import ToolResultEvent
from app.agent.hooks import build_tool_gate, constrain_web_search
from app.agent.options import build_runtime_spec, builtin_tools, permitted_tool_names
from app.config import settings
from app.rag.fetch import UrlFetchError, fetch_url_text
from app.schemas.assistant_config import (
    ApprovalPolicy,
    AssistantConfig,
    HttpRequestTool,
    WebSearchTool,
)
from app.security import ssrf

pytestmark = pytest.mark.anyio

HTTP = "mcp__caps__http_request"
PUBLIC = "93.184.216.34"


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch):
    """Route the tool's requests to a scripted transport and a fake DNS."""
    responses: list[httpx.Response] = []
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return responses.pop(0) if responses else httpx.Response(200, text="ok")

    async def resolve(host: str, _port: int) -> list[str]:
        return {"api.example.com": [PUBLIC], "internal.test": ["10.0.0.7"]}.get(host, [PUBLIC])

    patched = functools.partial(
        ssrf.safe_request, resolver=resolve, transport=httpx.MockTransport(handle)
    )
    monkeypatch.setattr(caps_http, "safe_request", patched)
    return responses, sent


def _text(result: dict[str, Any]) -> str:
    return result["content"][0]["text"]


# ── the tool ─────────────────────────────────────────────────


async def test_a_get_returns_status_and_pretty_json(web) -> None:
    responses, sent = web
    responses.append(httpx.Response(200, json={"items": [1, 2]}))
    result = await build_http_tool(HttpRequestTool(enabled=True)).handler(
        {"url": "https://api.example.com/items"}
    )
    assert not result.get("is_error")
    text = _text(result)
    assert text.startswith("HTTP 200 OK\napplication/json")
    assert '"items": [\n    1,' in text
    assert sent[0].method == "GET"


async def test_an_error_status_is_marked_as_an_error(web) -> None:
    responses, _ = web
    responses.append(httpx.Response(404, text="missing"))
    result = await build_http_tool(HttpRequestTool(enabled=True)).handler(
        {"url": "https://api.example.com/nope"}
    )
    assert result["is_error"] is True
    assert "HTTP 404" in _text(result)


async def test_a_binary_body_is_described_not_dumped(web) -> None:
    responses, _ = web
    responses.append(
        httpx.Response(200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"})
    )
    result = await build_http_tool(HttpRequestTool(enabled=True)).handler(
        {"url": "https://api.example.com/logo.png"}
    )
    assert "(binary body not shown)" in _text(result)
    assert "PNG" not in _text(result)


async def test_an_internal_address_is_blocked_with_a_reason(web) -> None:
    _, sent = web
    result = await build_http_tool(HttpRequestTool(enabled=True)).handler(
        {"url": "http://internal.test/admin"}
    )
    assert result["is_error"] is True
    assert _text(result).startswith("blocked:")
    assert sent == []


async def test_the_allowlist_belongs_to_the_assistant_not_the_model(web) -> None:
    tool = build_http_tool(HttpRequestTool(enabled=True, allowed_domains=["example.com"]))
    assert "example.com" in tool.description
    result = await tool.handler({"url": "https://other.net/"})
    assert "allowed domains" in _text(result)


# ── approvals ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "tool_mode", "policy_mode", "expected"),
    [
        ("GET", "require", "require", ("auto", "low")),
        ("HEAD", "deny", "deny", ("auto", "low")),
        (None, "require", "require", ("auto", "low")),  # no method means GET
        ("POST", "require", "require", ("require", "medium")),
        ("POST", "auto", "require", ("require", "medium")),  # policy insists
        ("DELETE", "require", "auto", ("require", "medium")),  # tool insists
        ("PUT", "auto", "auto", ("auto", "medium")),
        ("PATCH", "deny", "auto", ("deny", "medium")),
    ],
)
def test_non_get_takes_the_stricter_setting(
    method: str | None, tool_mode: str, policy_mode: str, expected: tuple[str, str]
) -> None:
    args = {"url": "https://x.test/"} | ({"method": method} if method else {})
    got = classify(
        HTTP,
        args,
        ApprovalPolicy(http_non_get=policy_mode),  # type: ignore[arg-type]
        http=HttpRequestTool(approval=tool_mode),  # type: ignore[arg-type]
    )
    assert got == expected


async def test_a_refused_write_says_how_to_allow_it() -> None:
    can_use = build_can_use_tool(
        ApprovalPolicy(), http=HttpRequestTool(enabled=True, approval="deny")
    )
    decision = await can_use(HTTP, {"method": "POST", "url": "https://x.test/"}, None)  # type: ignore[arg-type]
    assert type(decision).__name__ == "PermissionResultDeny"
    assert "GET and HEAD" in decision.message  # type: ignore[union-attr]


def test_the_tool_is_offered_only_when_enabled() -> None:
    off = build_runtime_spec(AssistantConfig())
    assert HTTP not in off.enabled_tools
    cfg = AssistantConfig.model_validate({"tools": {"http_request": {"enabled": True}}})
    on = build_runtime_spec(cfg)
    assert HTTP in on.enabled_tools
    assert any(t.name == "http_request" and t.open_world for t in on.caps_tools)


async def test_the_fake_driver_asks_before_a_write() -> None:
    """`http: POST …` goes through the real permission callback; with no
    conversation to ask in, a write that needs a human is refused."""
    cfg = AssistantConfig.model_validate({"tools": {"http_request": {"enabled": True}}})
    spec = build_runtime_spec(cfg)
    can_use = build_can_use_tool(cfg.approval_policy, None, http=cfg.tools.http_request)
    events = [
        ev
        async for ev in FakeDriver().stream(
            prompt='http: POST https://api.example.com/items {"a": 1}',
            spec=spec,
            policy=cfg.approval_policy,
            can_use_tool=can_use,
        )
    ]
    result = next(ev for ev in events if isinstance(ev, ToolResultEvent))
    assert result.status == "denied"
    assert "needs human approval" in result.output


# ── web search ───────────────────────────────────────────────


def test_web_search_domains_can_narrow_but_not_widen() -> None:
    allowed = ["example.com", "docs.python.org"]
    assert constrain_web_search({"query": "x"}, []) is None
    widened = constrain_web_search({"query": "x", "allowed_domains": ["evil.net"]}, allowed)
    assert widened == {"query": "x", "allowed_domains": ["example.com", "docs.python.org"]}
    asked = {
        "query": "x",
        "allowed_domains": ["api.example.com", "evil.net"],
        "blocked_domains": ["a"],
    }
    narrowed = constrain_web_search(asked, allowed)
    assert narrowed == {"query": "x", "allowed_domains": ["api.example.com"]}


async def test_the_gate_counts_searches_per_turn() -> None:
    gate = build_tool_gate(frozenset({"WebSearch"}), WebSearchTool(enabled=True, max_uses=2))
    call = {"tool_name": "WebSearch", "tool_input": {"query": "q"}}
    assert await gate(call, None, None) == {}
    assert await gate(call, None, None) == {}
    third = await gate(call, None, None)
    assert third["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "2 web searches per turn" in third["hookSpecificOutput"]["permissionDecisionReason"]


async def test_the_gate_rewrites_search_domains() -> None:
    gate = build_tool_gate(
        frozenset({"WebSearch"}),
        WebSearchTool(enabled=True, allowed_domains=["example.com"]),
    )
    out = await gate({"tool_name": "WebSearch", "tool_input": {"query": "q"}}, None, None)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    assert hso["updatedInput"] == {"query": "q", "allowed_domains": ["example.com"]}


def test_web_search_limits_reach_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_offline", False)
    cfg = AssistantConfig.model_validate(
        {"tools": {"web_search": {"enabled": True, "max_uses": 3, "allowed_domains": ["a.com"]}}}
    )
    spec = build_runtime_spec(cfg)
    assert spec.web_search is not None and spec.web_search.max_uses == 3
    assert build_runtime_spec(AssistantConfig()).web_search is None


async def test_offline_mode_switches_web_search_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan §10, Privacy: offline mode disables web search. A search sends
    the model's query to a third party, which is what offline promises not
    to do. The assistant's config keeps it; this instance just won't offer
    it, and the gate refuses a call that arrives anyway."""
    monkeypatch.setattr(settings, "rag_offline", True)
    cfg = AssistantConfig.model_validate({"tools": {"web_search": {"enabled": True}}})
    spec = build_runtime_spec(cfg)
    assert spec.web_search is None
    assert "WebSearch" not in spec.enabled_tools
    assert builtin_tools(spec) == []
    assert cfg.tools.web_search.enabled is True  # the config is untouched
    gate = build_tool_gate(permitted_tool_names(spec), spec.web_search)
    refused = await gate({"tool_name": "WebSearch", "tool_input": {"query": "q"}}, None, None)
    assert refused["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── URL data sources use the same guard ──────────────────────


async def test_a_url_source_cannot_point_inside_the_network() -> None:
    with pytest.raises(UrlFetchError, match="not a public address"):
        await fetch_url_text("http://127.0.0.1:8000/api/v1/")
    with pytest.raises(UrlFetchError, match="only http and https"):
        await fetch_url_text("file:///etc/passwd")


def test_the_approval_card_reads_like_a_request() -> None:
    from app.agent.approvals import describe

    text = describe(
        HTTP,
        {
            "method": "post",
            "url": "https://api.example.com/items",
            "headers": {"Authorization": "Bearer abc", "X-Trace": "7"},
            "body": '{"name": "x"}',
        },
    )
    assert text == (
        'POST https://api.example.com/items\nAuthorization: <redacted>\nX-Trace: 7\n\n{"name": "x"}'
    )
