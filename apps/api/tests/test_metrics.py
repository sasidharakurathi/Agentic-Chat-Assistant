"""`/metrics`, the alert rules and the dashboard (task 6.4).

The last two are configuration, which nothing runs in CI. So they are
checked against the one thing that can be: every metric an alert or a
panel names must be one `/metrics` really exports. A renamed metric then
fails here, instead of leaving an alert that can never fire.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.budget import BudgetPeriod
from app.models.integration import McpServer, McpServerStatus, McpTransport
from app.models.usage import UsageEvent, UsageKind
from app.observability import collect, metrics
from app.observability.metrics import Counter, Histogram, Sample
from app.services import budgets
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

API = "/api/v1"
ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "observability"


@pytest.fixture(autouse=True)
def _clean() -> Any:
    metrics.reset()
    collect.clear_cache()
    yield
    metrics.reset()
    collect.clear_cache()


def _values(text: str) -> dict[str, float]:
    """`name{labels}` -> value, for every sample line."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.rpartition(" ")
            name, brace, labels = key.partition("{")
            if brace:
                # Labels in a fixed order, so a test needn't know the order
                # a metric declares them in.
                pairs = sorted(re.findall(r'\w+="(?:[^"\\]|\\.)*"', labels))
                key = name + "{" + ",".join(pairs) + "}"
            out[key] = float(value)
    return out


def _names(text: str) -> set[str]:
    """Every metric family the page declares."""
    return set(re.findall(r"^# TYPE (\S+) ", text, re.M))


# ── the text format ──────────────────────────────────────────


def test_a_counter_and_a_histogram_render_in_the_text_format() -> None:
    hits = Counter("hits_total", "Hits.", ("route", "status"))
    hits.inc("/a", "2xx")
    hits.inc("/a", "2xx", by=2)
    hits.inc('/b "quoted"\n', "5xx")
    assert hits.render() == [
        "# HELP hits_total Hits.",
        "# TYPE hits_total counter",
        'hits_total{route="/a",status="2xx"} 3',
        'hits_total{route="/b \\"quoted\\"\\n",status="5xx"} 1',
    ]

    took = Histogram("took_seconds", "Took.", (), buckets=(0.1, 1.0))
    for seconds in (0.05, 0.5, 5.0):
        took.observe(seconds)
    assert took.render() == [
        "# HELP took_seconds Took.",
        "# TYPE took_seconds histogram",
        'took_seconds_bucket{le="0.1"} 1',
        'took_seconds_bucket{le="1"} 2',
        'took_seconds_bucket{le="+Inf"} 3',
        "took_seconds_sum 5.55",
        "took_seconds_count 3",
    ]
    with pytest.raises(ValueError, match="takes labels"):
        hits.inc("/a")


def test_labels_stay_bounded() -> None:
    assert metrics.status_class(200) == "2xx" and metrics.status_class(503) == "5xx"
    assert metrics.short_tool("mcp__caps__sql_query") == "sql_query"
    assert metrics.short_tool("WebSearch") == "WebSearch"
    sample = Sample("x", "X.", "gauge", ("a",), [(("1",), 2.5)])
    assert sample.render()[-1] == 'x{a="1"} 2.5'


# ── who may read it ──────────────────────────────────────────


async def test_metrics_need_the_token_when_one_is_set(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page names orgs and assistants and says what each spends."""
    monkeypatch.setattr(settings, "metrics_token", "scrape-token-0123456789")
    assert (await client.get("/metrics")).status_code == 401
    wrong = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    right = await client.get(
        "/metrics", headers={"Authorization": "Bearer scrape-token-0123456789"}
    )
    assert right.status_code == 200
    assert right.headers["content-type"].startswith("text/plain; version=0.0.4")


async def test_without_a_token_it_is_open_in_development_and_off_in_production(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "metrics_token", "")
    assert (await client.get("/metrics")).status_code == 200
    monkeypatch.setattr(settings, "app_env", "production")
    assert (await client.get("/metrics")).status_code == 404


# ── what it reports ──────────────────────────────────────────


async def _assistant(client: AsyncClient, headers: dict[str, str], **config: Any) -> str:
    a = (await client.post(f"{API}/assistants", json={"name": "Shop"}, headers=headers)).json()
    if config:
        cfg = {**a["draft_config"], **config}
        await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
    return str(a["id"])


async def _say(client: AsyncClient, headers: dict[str, str], aid: str, text: str) -> None:
    conv = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=headers)
    async with client.stream(
        "POST",
        f"{API}/conversations/{conv.json()['id']}/messages",
        json={"text": text},
        headers=headers,
    ) as stream:
        async for _ in stream.aiter_lines():
            pass


async def test_requests_are_counted_by_route_template_not_by_address(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    for _ in range(3):
        await client.get(f"{API}/assistants/{aid}", headers=org_headers)
    await client.get(f"{API}/assistants/{uuid.uuid4()}", headers=org_headers)
    await client.get("/no/such/page")
    await client.get("/healthz")

    text = (await client.get("/metrics")).text
    got = _values(text)
    route = "/api/v1/assistants/{assistant_id}"
    assert (
        got[f'assistant_studio_http_requests_total{{method="GET",route="{route}",status="2xx"}}']
        == 3
    )
    assert (
        got[f'assistant_studio_http_requests_total{{method="GET",route="{route}",status="4xx"}}']
        == 1
    )
    # One series for everything that matched no route: an address scanner
    # can't create a series per URL it tries.
    assert (
        got['assistant_studio_http_requests_total{method="GET",route="unmatched",status="4xx"}']
        == 1
    )
    assert aid not in text, "no id ever becomes a label"
    assert "healthz" not in text and 'route="/metrics"' not in text, "probes are not traffic"
    assert (
        got[f'assistant_studio_http_request_duration_seconds_count{{method="GET",route="{route}"}}']
        == 4
    )


async def test_a_turn_reports_its_duration_its_tools_and_its_spend(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers, tools={"calculator": {"enabled": True}})
    await _say(client, org_headers, aid, "calculate 2 + 3")
    await _say(client, org_headers, aid, "hello")

    got = _values((await client.get("/metrics")).text)
    assert got['assistant_studio_turn_duration_seconds_count{status="ok"}'] == 2
    assert (
        got['assistant_studio_tool_duration_seconds_count{status="success",tool="calculator"}'] == 1
    )
    assert got["assistant_studio_sse_stream_duration_seconds_count"] == 2
    # Read from the ledger and the run rows, not counted in memory.
    assert got['assistant_studio_runs_total{status="ok"}'] == 2
    assert got['assistant_studio_tool_calls_total{status="success",tool="calculator"}'] == 1
    model = "claude-sonnet-5"
    assert got[f'assistant_studio_tokens_total{{direction="out",kind="llm",model="{model}"}}'] > 0
    assert got[f'assistant_studio_cost_usd_total{{kind="llm",model="{model}"}}'] > 0
    assert (
        got[f'assistant_studio_assistant_cost_usd_total{{assistant="Shop",assistant_id="{aid}"}}']
        > 0
    )


async def test_state_is_read_from_the_database(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    org = org_headers["X-Org-Id"]
    put = await client.put(f"{API}/orgs/{org}/budgets", json={"daily_usd": 4}, headers=org_headers)
    assert put.status_code == 200, put.text
    async with get_sessionmaker()() as s:
        s.add(UsageEvent(org_id=uuid.UUID(org), kind=UsageKind.llm, model="m", cost_usd=3))
        for status, enabled in (
            (McpServerStatus.error, True),
            (McpServerStatus.ok, True),
            (McpServerStatus.error, False),
        ):
            s.add(
                McpServer(
                    assistant_id=uuid.UUID(aid),
                    org_id=uuid.UUID(org),
                    name=f"s{uuid.uuid4().hex[:6]}",
                    transport=McpTransport.http,
                    url="https://mcp.example.com/mcp",
                    status=status,
                    enabled=enabled,
                )
            )
        await s.commit()
    await client.post(
        f"{API}/assistants/{aid}/data-sources",
        json={"type": "text", "name": "Notes", "text": "hello"},
        headers=org_headers,
    )

    got = _values((await client.get("/metrics")).text)
    assert (
        got[f'assistant_studio_budget_used_ratio{{org_id="{org}",period="day",scope="org"}}']
        == 0.75
    )
    assert got['assistant_studio_mcp_servers{status="error"}'] == 1, (
        "a switched-off server isn't down"
    )
    assert got['assistant_studio_mcp_servers{status="ok"}'] == 1
    assert sum(v for k, v in got.items() if k.startswith("assistant_studio_data_sources")) == 1
    assert got["assistant_studio_approvals_pending"] == 0
    assert got["assistant_studio_chunks"] == 0
    assert BudgetPeriod.day.value == "day" and budgets.WARN_AT == 0.8


async def test_a_source_that_cannot_be_read_is_reported_not_zeroed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis isn't running in the unit tier, so the queue can't be read."""

    async def broken(_session: Any) -> list[Sample]:
        raise ConnectionError("redis is down")

    monkeypatch.setitem(collect.COLLECTORS, "queue", broken)
    text = (await client.get("/metrics")).text
    got = _values(text)
    assert got['assistant_studio_scrape_errors{source="queue"}'] == 1
    assert got['assistant_studio_scrape_errors{source="usage"}'] == 0
    assert "assistant_studio_queue_depth" not in text, "absent, not zero"
    assert "redis is down" not in text


async def test_the_queue_depth_is_read_from_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRedis:
        async def zcard(self, key: str) -> int:
            assert key == "arq:queue"
            return 7

    monkeypatch.setattr(collect, "get_redis", FakeRedis)
    samples = {s.name: s for s in await collect.collect(fresh=True)}
    assert samples["assistant_studio_queue_depth"].values == [((), 7.0)]


async def test_a_scrape_is_cached_for_a_few_seconds(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    real = collect.COLLECTORS["usage"]

    async def counting(session: Any) -> list[Sample]:
        calls.append(1)
        return await real(session)

    monkeypatch.setitem(collect.COLLECTORS, "usage", counting)
    for _ in range(3):
        assert (await client.get("/metrics")).status_code == 200
    assert len(calls) == 1
    collect.clear_cache()
    await client.get("/metrics")
    assert len(calls) == 2


# ── the alert rules and the dashboard ────────────────────────

METRIC = re.compile(r"\bassistant_studio_[a-z_]+")
#: What Prometheus adds to a histogram's name.
SUFFIXES = ("_bucket", "_sum", "_count")


async def _exported(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Every metric family `/metrics` can export, observed or read."""

    class FakeRedis:
        async def zcard(self, _key: str) -> int:
            return 0

    monkeypatch.setattr(collect, "get_redis", FakeRedis)
    collect.clear_cache()
    names = _names((await client.get("/metrics")).text)
    assert len(names) > 15
    return names


def _base(name: str, exported: set[str]) -> str:
    for suffix in SUFFIXES:
        if name.endswith(suffix) and name.removesuffix(suffix) in exported:
            return name.removesuffix(suffix)
    return name


async def test_every_alert_names_metrics_that_exist(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = await _exported(client, monkeypatch)
    rules = yaml.safe_load((DEPLOY / "alerts.yml").read_text(encoding="utf-8"))
    alerts = [r for g in rules["groups"] for r in g["rules"]]
    assert len(alerts) >= 10
    # The plan's list (§12): budgets at 80 and 100%, ingestion failures, an
    # MCP server down, a queue backlog, the API's 5xx rate.
    assert {
        "BudgetNearlySpent",
        "BudgetSpent",
        "IngestionFailing",
        "McpServerDown",
        "QueueBacklog",
        "ApiErrorRate",
    } <= {a["alert"] for a in alerts}
    for alert in alerts:
        used = {_base(m, exported) for m in METRIC.findall(alert["expr"])}
        assert used or "up{" in alert["expr"], f"{alert['alert']} names no metric"
        assert used <= exported, f"{alert['alert']} uses {sorted(used - exported)}"
        assert alert["labels"]["severity"] in {"info", "warning", "critical"}
        assert alert["annotations"]["summary"] and alert["annotations"]["description"]
        assert "for" in alert, f"{alert['alert']} would fire on a single bad scrape"


def _dashboard_module() -> Any:
    path = DEPLOY / "grafana" / "build_dashboard.py"
    spec = importlib.util.spec_from_file_location("build_dashboard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_every_dashboard_panel_queries_metrics_that_exist(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = await _exported(client, monkeypatch)
    builder = _dashboard_module()
    committed = (DEPLOY / "grafana" / "dashboards" / "assistant-studio.json").read_text(
        encoding="utf-8"
    )
    assert committed == builder.text(), "run deploy/observability/grafana/build_dashboard.py"

    panels = [p for p in builder.build()["panels"] if p["type"] != "row"]
    assert len(panels) >= 15
    titles = " ".join(p["title"] for p in panels).lower()
    # The plan's four (§12): spend by assistant and model, retrieval
    # latency, the turn's duration breakdown, the error budget.
    for wanted in ("by model", "by assistant", "knowledge-base search", "turn duration", "5xx"):
        assert wanted in titles, wanted
    for panel in panels:
        for target in panel["targets"]:
            used = {_base(m, exported) for m in METRIC.findall(target["expr"])}
            assert used, f"{panel['title']} queries no metric"
            assert used <= exported, f"{panel['title']} uses {sorted(used - exported)}"


def test_prometheus_scrapes_the_api_with_a_token_from_a_file() -> None:
    config = yaml.safe_load((DEPLOY / "prometheus.yml").read_text(encoding="utf-8"))
    (job,) = config["scrape_configs"]
    assert job["metrics_path"] == "/metrics"
    assert job["authorization"] == {"type": "Bearer", "credentials_file": "/tmp/metrics_token"}
    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]
    # The job name the ApiDown alert looks for.
    assert f'job="{job["job_name"]}"' in (DEPLOY / "alerts.yml").read_text(encoding="utf-8")


# ── the timers around waiting ────────────────────────────────


async def test_an_approval_reports_how_long_it_waited(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    from app.services import approvals

    aid = await _assistant(client, org_headers)
    conv = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    async with get_sessionmaker()() as s:
        row = await approvals.create(
            s,
            conversation_id=uuid.UUID(conv.json()["id"]),
            org_id=uuid.UUID(org_headers["X-Org-Id"]),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "DELETE FROM orders"},
            risk="high",
            rationale="writes",
        )
    done = await client.post(
        f"{API}/approvals/{row.id}:resolve", json={"decision": "denied"}, headers=org_headers
    )
    assert done.status_code == 200, done.text
    assert metrics.APPROVAL_WAIT.count("denied") == 1
    assert metrics.APPROVAL_WAIT.count("approved") == 0


async def test_a_search_is_timed_whether_it_answers_or_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import retrieve
    from app.schemas.assistant_config import RagRetrieval

    async def found(*_a: Any) -> list[Any]:
        return []

    async def broke(*_a: Any) -> list[Any]:
        raise RuntimeError("index unavailable")

    async def search() -> Any:
        return await retrieve.retrieve(
            None,  # type: ignore[arg-type]
            assistant_id=uuid.uuid4(),
            query="q",
            config=RagRetrieval(),
        )

    monkeypatch.setattr(retrieve, "_retrieve", found)
    assert await search() == []
    monkeypatch.setattr(retrieve, "_retrieve", broke)
    with pytest.raises(RuntimeError):
        await search()
    assert metrics.RETRIEVAL_DURATION.count() == 2


async def test_a_request_that_matches_no_route_shares_one_series(client: AsyncClient) -> None:
    for path in ("/nothing/here", "/api/v1/also-nothing/123"):
        assert (await client.get(path)).status_code == 404
    assert metrics.HTTP_REQUESTS.value("GET", "unmatched", "4xx") == 2
    # And the probes and the scrape are not traffic at all.
    await client.get("/healthz")
    await client.get("/metrics")
    assert "healthz" not in metrics.render()
