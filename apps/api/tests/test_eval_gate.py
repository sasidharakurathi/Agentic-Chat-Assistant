"""The eval regression gate (task 6.2, plan §11.2).

A 15-case suite runs against a fixture assistant: a stub corpus, a SQLite
shop database, the calculator, and no web search. The model is canned
(`tests/canned_model.py`); the driver, the SDK client, the hooks, the
permission router, the tools, retrieval's pipeline, citations, the eval
runner and its scoring are the real ones. The run's metrics are compared
with a committed baseline (`app/evals/gate.py`), and a drop beyond the
tolerance fails the build.

The second half proves the gate has teeth: break one thing the platform
does, and the same suite must fail.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from app import queue
from app.agent import claude_api
from app.agent import driver as driver_mod
from app.config import settings
from app.db.session import get_sessionmaker
from app.evals import runner
from app.evals.gate import Baseline, load_baseline, regressions
from app.evals.suite import FIXTURES_DIR
from app.models.approval import Approval
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from claude_agent_sdk import ClaudeAgentOptions
from httpx import AsyncClient
from sqlalchemy import func, select
from tests import lexical_rag
from tests.canned_model import COST_USD, CannedModel, Play
from tests.scripted_cli import ScriptedCLI

pytestmark = pytest.mark.anyio

API = "/api/v1"
SUITE = json.loads((FIXTURES_DIR / "regression_v1.json").read_text(encoding="utf-8"))
BASELINE = load_baseline("regression_baseline.json")


@dataclass
class GateRun:
    run: dict[str, Any]
    model: CannedModel
    shop: Path
    assistant_id: str

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(self.run["metrics"])

    def result(self, case_id: str) -> dict[str, Any]:
        question = next(c["input"] for c in SUITE["cases"] if c["id"] == case_id)
        return next(r for r in self.run["results"] if r["input"] == question)

    def calls(self, case_id: str) -> list[Any]:
        question = next(c["input"] for c in SUITE["cases"] if c["id"] == case_id)
        return self.model.calls[question]


def _shop_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    for statement in SUITE["database"]:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return path


async def _index_corpus(assistant_id: str, org_id: str) -> None:
    """The stub corpus as the knowledge base holds it: a source, a document
    and one chunk per passage. No embeddings: the stand-in store ranks by
    the text."""
    aid, org = uuid.UUID(assistant_id), uuid.UUID(org_id)
    async with get_sessionmaker()() as s:
        for n, passage in enumerate(SUITE["corpus"]):
            source = DataSource(
                assistant_id=aid,
                org_id=org,
                type=DataSourceType.text,
                name=passage["title"],
                status=DataSourceStatus.ready,
            )
            s.add(source)
            await s.flush()
            doc = Document(
                data_source_id=source.id, assistant_id=aid, org_id=org, title=passage["title"]
            )
            s.add(doc)
            await s.flush()
            s.add(
                Chunk(
                    document_id=doc.id,
                    assistant_id=aid,
                    org_id=org,
                    ordinal=n,
                    content=passage["text"],
                    chunk_metadata={"start": 0, "end": len(passage["text"])},
                )
            )
        await s.commit()


Tweak = Callable[[dict[str, Any]], None]


async def _fixture_assistant(
    client: AsyncClient,
    headers: dict[str, str],
    shop: Path,
    permissions: dict[str, bool],
    tweak: Tweak | None,
) -> tuple[str, str]:
    """The assistant under test, and its database connection's id."""
    a = (await client.post(f"{API}/assistants", json={"name": "Gate"}, headers=headers)).json()
    conn = await client.post(
        f"{API}/assistants/{a['id']}/db-connections",
        json={
            "name": "Shop",
            "engine": "sqlite",
            "database": str(shop),
            "permissions": permissions,
        },
        headers=headers,
    )
    assert conn.status_code == 201, conn.text
    cfg = a["draft_config"]
    cfg["rag"] = {**cfg["rag"], "enabled": True, "citations": True}
    cfg["tools"]["calculator"]["enabled"] = True
    cfg["databases"] = [{"connection_id": conn.json()["id"], "nl2sql": True, "expose_write": True}]
    if tweak is not None:
        tweak(cfg)
    saved = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
    assert saved.status_code == 200, saved.text
    await _index_corpus(a["id"], headers["X-Org-Id"])
    return str(a["id"]), str(conn.json()["id"])


#: Writes are allowed (so a change asks a person); schema changes are not.
PERMISSIONS = {"read": True, "write": True, "ddl": False}


async def run_gate(
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    tweak: Tweak | None = None,
    permissions: dict[str, bool] | None = None,
) -> GateRun:
    """Build the fixture assistant, run the suite through the canned model,
    and return the finished run. `tweak` edits the assistant's config, and
    `permissions` its database connection: for the tests that break
    something on purpose."""
    # The real driver, with no way to reach a real model: the transport is
    # scripted, and with no key nothing else (judge, titles) would call one.
    monkeypatch.setattr(settings, "agent_driver", "claude")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "voyage_api_key", "")
    assert not claude_api.real_model_allowed()
    lexical_rag.install(monkeypatch)

    shop = _shop_db(tmp_path / "shop.db")
    assistant_id, connection_id = await _fixture_assistant(
        client, headers, shop, permissions or PERMISSIONS, tweak
    )
    model = CannedModel(
        {c["input"]: Play(**c["play"]) for c in SUITE["cases"]}, {"connection": connection_id}
    )

    def transport(options: ClaudeAgentOptions) -> ScriptedCLI:
        return ScriptedCLI(options, model.script())

    monkeypatch.setattr(driver_mod, "transport_factory", transport)

    async def enqueue(_run_id: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue, "enqueue_eval", enqueue)
    suite = await client.post(
        f"{API}/assistants/{assistant_id}/eval-suites",
        json={"name": SUITE["name"], "config": {"judge": False}},
        headers=headers,
    )
    assert suite.status_code == 201, suite.text
    sid = suite.json()["id"]
    cases = [
        {"input": c["input"], "expected": c.get("expected", {}), "labels": c.get("labels", {})}
        for c in SUITE["cases"]
    ]
    added = await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk", json={"cases": cases}, headers=headers
    )
    assert added.status_code == 200, added.text
    started = await client.post(f"{API}/eval-suites/{sid}/runs", json={}, headers=headers)
    assert started.status_code == 202, started.text
    await runner.run(uuid.UUID(started.json()["id"]))
    run = (await client.get(f"{API}/eval-runs/{started.json()['id']}", headers=headers)).json()
    return GateRun(run=run, model=model, shop=shop, assistant_id=assistant_id)


def _explain(gate: GateRun) -> str:
    """Which cases failed and why: what a red build needs to say."""
    lines = []
    for r in gate.run["results"]:
        if r["passed"]:
            continue
        failed = [c for c in r["scores"]["checks"] if not c["passed"]]
        lines.append(f"- {r['input']!r}: {r['error'] or failed}\n    answer: {r['output'][:300]!r}")
    return "\n".join(lines)


# ── the gate ─────────────────────────────────────────────────


async def test_the_fixture_suite_meets_its_baseline(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gate = await run_gate(client, org_headers, monkeypatch, tmp_path)

    assert gate.run["status"] == "done", gate.run["error"]
    failed = regressions(gate.metrics, BASELINE)
    assert failed == [], "\n".join(failed) + "\n" + _explain(gate)

    # What the numbers rest on. Each of these is the platform's doing, not
    # the canned model's.
    # Two passages, two markers, both resolved to their sources.
    two = gate.result("kb_two_sources")
    assert two["scores"]["citations"] == 2 and "[1]" in two["output"] and "[2]" in two["output"]
    assert gate.result("kb_nothing_found")["scores"]["citations"] == 0
    # A change to data asks a person; here there is nobody, so it is declined
    # by the permission router, with no pending approval left behind...
    (write,) = gate.calls("sql_write_needs_a_person")
    assert write.outcome == "permission"
    # ...a schema change is refused by the connection's own permissions...
    (ddl,) = gate.calls("sql_ddl_refused")
    assert ddl.outcome == "permission"
    # ...and a tool that isn't switched on never gets past the gate.
    (web,) = gate.calls("web_search_is_off")
    assert web.outcome == "gate"
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(Approval)) == 0
    # The shop is exactly as it was.
    shop = sqlite3.connect(gate.shop)
    assert shop.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall() == [
        ("open", 2),
        ("shipped", 2),
    ]
    shop.close()
    # Read queries had a row limit added by the guard.
    (total,) = gate.calls("sql_total")
    assert "LIMIT" in total.output and "LIMIT" not in total.input["sql"]
    # Every turn ran on the real driver, at the canned model's fixed price.
    assert gate.run["cost_usd"] == pytest.approx(len(SUITE["cases"]) * COST_USD)
    listed = await client.get(
        f"{API}/assistants/{gate.assistant_id}/conversations", headers=org_headers
    )
    assert listed.json()["items"] == []


def test_the_fixture_and_its_baseline_belong_together() -> None:
    cases = SUITE["cases"]
    assert len(cases) == 15 == BASELINE.cases
    assert BASELINE.suite == SUITE["name"]
    assert len({c["id"] for c in cases}) == len(cases) == len({c["input"] for c in cases})
    assert all("play" in c and c["play"]["answer"] for c in cases)
    titles = {p["title"] for p in SUITE["corpus"]}
    assert len(titles) == len(SUITE["corpus"])
    for case in cases:
        for label in case.get("labels", {}).get("relevant_sources", []):
            assert label in titles, f"{case['id']} labels a source the corpus doesn't have"


# ── the gate has teeth ───────────────────────────────────────
#
# Each test breaks one thing the platform does and runs the same suite. If
# the gate still passed, it would be measuring the canned model, not the
# platform.


def _failures(gate: GateRun) -> tuple[list[str], set[str]]:
    failed_ids = {c["id"] for c in SUITE["cases"] if not gate.result(c["id"])["passed"]}
    return regressions(gate.metrics, BASELINE), failed_ids


async def test_the_gate_fails_when_retrieval_stops_finding_the_passage(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Backwards(lexical_rag.LexicalReranker):
        async def rerank(self, query: str, candidates: list[str]) -> list[Any]:
            ranked = await super().rerank(query, candidates)
            return [type(r)(index=r.index, score=1.0) for r in reversed(ranked)]

    real_install = lexical_rag.install

    def install(mp: pytest.MonkeyPatch) -> dict[str, Any]:
        parts = real_install(mp)
        parts["reranker"] = Backwards()
        return parts

    monkeypatch.setattr(lexical_rag, "install", install)
    # Only the top three reach the agent, so a bad ranking loses the passage.
    gate = await run_gate(
        client,
        org_headers,
        monkeypatch,
        tmp_path,
        tweak=lambda cfg: cfg["rag"]["retrieval"].update(rerank_top_n=3),
    )
    reasons, failed = _failures(gate)
    assert any("pass rate fell" in r for r in reasons), reasons
    assert any("retrieval recall fell" in r for r in reasons), reasons
    assert {"kb_refund_window", "kb_store_credit", "kb_warranty"} <= failed
    assert "calculator" not in failed and "sql_total" not in failed


async def test_the_gate_fails_when_citations_stop_resolving(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("app.agent.runtime.Turn.resolve_citations", lambda self, text: [])
    gate = await run_gate(client, org_headers, monkeypatch, tmp_path)
    reasons, failed = _failures(gate)
    assert any("pass rate fell" in r for r in reasons), reasons
    # The answers still say the right thing; they just cite nothing.
    assert failed == {c["id"] for c in SUITE["cases"] if c["expected"].get("cites")}
    assert not any("retrieval" in r for r in reasons), "retrieval itself is fine"


async def test_the_gate_fails_when_a_tool_is_no_longer_offered(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def calculator_off(cfg: dict[str, Any]) -> None:
        cfg["tools"]["calculator"]["enabled"] = False

    gate = await run_gate(client, org_headers, monkeypatch, tmp_path, tweak=calculator_off)
    reasons, failed = _failures(gate)
    assert failed == {"calculator"}
    assert len(reasons) == 1 and "pass rate fell to 0.9333" in reasons[0]


async def test_the_gate_fails_when_a_guard_stops_refusing(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A connection that allows schema changes: DROP is no longer refused by
    # the connection (it would go to a person instead).
    gate = await run_gate(
        client,
        org_headers,
        monkeypatch,
        tmp_path,
        permissions={"read": True, "write": True, "ddl": True},
    )
    reasons, failed = _failures(gate)
    assert failed == {"sql_ddl_refused"}
    assert reasons and "pass rate fell" in reasons[0]


async def test_the_gate_fails_when_a_turn_errors(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real = CannedModel._play_for

    def lost(self: CannedModel, prompt: str) -> tuple[str, Play]:
        if "17 * 23" in prompt:
            raise RuntimeError("the CLI died")
        return real(self, prompt)

    monkeypatch.setattr(CannedModel, "_play_for", lost)
    gate = await run_gate(client, org_headers, monkeypatch, tmp_path)
    reasons, failed = _failures(gate)
    assert failed == {"calculator"}
    assert "1 case(s) ended in an error" in reasons


# ── the rule itself ──────────────────────────────────────────


def _baseline() -> Baseline:
    metrics = {"pass_rate": 0.9, "retrieval": {"recall": 0.8, "mrr": 0.7, "ndcg": 0.75}}
    return Baseline(suite="s", metrics=metrics, tolerance=0.02, cases=10)


def _metrics(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cases": 10,
        "errors": 0,
        "pass_rate": 0.9,
        "retrieval": {"recall": 0.8, "mrr": 0.7, "ndcg": 0.75},
    }
    base.update(over)
    return base


def test_a_run_at_or_above_its_baseline_passes() -> None:
    assert regressions(_metrics(), _baseline()) == []
    better = _metrics(pass_rate=1.0, retrieval={"recall": 1.0, "mrr": 1.0, "ndcg": 1.0})
    assert regressions(better, _baseline()) == []
    assert regressions(_metrics(cases=12), _baseline()) == [], "a bigger suite is fine"


def test_a_drop_within_the_tolerance_passes_and_beyond_it_fails() -> None:
    assert regressions(_metrics(pass_rate=0.88), _baseline()) == []
    assert regressions(_metrics(pass_rate=0.879), _baseline()) == [
        "pass rate fell to 0.8790 from a baseline of 0.9000 (allowed drop 0.02)"
    ]
    worse = _metrics(retrieval={"recall": 0.5, "mrr": 0.7, "ndcg": 0.6})
    assert regressions(worse, _baseline()) == [
        "retrieval recall fell to 0.5000 from a baseline of 0.8000 (allowed drop 0.02)",
        "retrieval nDCG fell to 0.6000 from a baseline of 0.7500 (allowed drop 0.02)",
    ]


def test_a_measure_that_stopped_being_taken_fails() -> None:
    assert regressions(_metrics(retrieval=None), _baseline()) == [
        "retrieval recall was not measured (baseline 0.8000)",
        "retrieval MRR was not measured (baseline 0.7000)",
        "retrieval nDCG was not measured (baseline 0.7500)",
    ]
    # A baseline without retrieval doesn't ask for it.
    unlabelled = Baseline(suite="s", metrics={"pass_rate": 0.9}, cases=10)
    assert regressions(_metrics(retrieval=None), unlabelled) == []


def test_errors_and_a_shrunken_suite_fail_whatever_the_pass_rate() -> None:
    assert regressions(_metrics(errors=2), _baseline()) == ["2 case(s) ended in an error"]
    assert regressions(_metrics(cases=9, pass_rate=1.0), _baseline()) == [
        "only 9 of the baseline's 10 cases ran"
    ]


def test_the_committed_baseline_loads_with_its_tolerance() -> None:
    assert BASELINE.tolerance == 0.02
    assert BASELINE.metrics["pass_rate"] == 1.0
    assert set(BASELINE.metrics["retrieval"]) == {"recall", "mrr", "ndcg"}
