"""The sample assistants (task 6.7).

Each shipped file is held to what a sample promises: it loads, its graph
compiles and validates with nothing to fix, it uses only what every install
has, and an assistant made from it is an ordinary assistant whose own eval
suite passes on the free driver.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app import queue, samples
from app.db.session import get_sessionmaker
from app.evals import runner
from app.graph.compile import compile_graph
from app.graph.project import project_config
from app.graph.validate import validate_graph
from app.models.audit_log import AuditLog
from app.models.rag import Chunk, DataSource, DataSourceStatus, Document
from httpx import AsyncClient
from sqlalchemy import select
from tests import lexical_rag

pytestmark = pytest.mark.anyio

API = "/api/v1"
SAMPLES = samples.all_samples()
IDS = [s.id for s in SAMPLES]


# ── the files ────────────────────────────────────────────────


def test_there_are_samples_and_their_ids_are_their_file_names() -> None:
    assert len(SAMPLES) >= 3
    assert len(set(IDS)) == len(IDS)
    on_disk = sorted(p.stem for p in samples.FOLDER.glob("*.json"))
    assert on_disk == sorted(IDS)
    # The gallery's order is chosen, not the alphabet's: the one that shows
    # the most, first.
    assert IDS[0] == "store-support"
    assert [s.order for s in SAMPLES] == sorted(s.order for s in SAMPLES)


@pytest.mark.parametrize("sample", SAMPLES, ids=IDS)
def test_a_samples_graph_has_nothing_to_fix(sample: samples.Sample) -> None:
    result = validate_graph(sample.graph)
    assert result.errors == [] and result.warnings == []
    # The graph is the source: the config comes out of the compiler, and
    # drawing that config again gives the same graph (nothing hand-edited
    # into a shape the canvas would not produce).
    config = compile_graph(sample.graph)
    again = project_config(config, existing_graph=sample.graph)
    assert compile_graph(again).model_dump() == config.model_dump()
    assert {n.id for n in again.nodes} == {n.id for n in sample.graph.nodes}


@pytest.mark.parametrize("sample", SAMPLES, ids=IDS)
def test_a_sample_uses_only_what_every_install_has(sample: samples.Sample) -> None:
    config = sample.config()
    assert config.databases == [] and config.mcp_servers == []
    assert config.rag.source_ids == []  # its own documents, whatever their ids
    assert not config.tools.http_request.enabled  # would need an allowlist
    # A knowledge base needs something in it; nothing else may bring documents.
    assert bool(sample.documents) == config.rag.enabled
    assert config.system_prompt != "You are a helpful assistant."
    assert sample.try_asking


@pytest.mark.parametrize("sample", SAMPLES, ids=IDS)
def test_a_samples_eval_cases_ask_only_for_what_it_can_do(sample: samples.Sample) -> None:
    assert sample.eval_suite is not None
    config = sample.config()
    available = {"kb_search"} if config.rag.enabled else set()
    available |= {"calculator"} if config.tools.calculator.enabled else set()
    available |= {"datetime"} if config.tools.datetime.enabled else set()
    available |= {"memory"} if config.memory.memory_tool else set()
    titles = {d.name for d in sample.documents}
    for case in sample.eval_suite.cases:
        assert set(case.expected.tools) <= available, case.input
        assert set(case.labels.relevant_sources) <= titles, case.input
        if case.expected.cites:
            assert config.rag.enabled and config.rag.citations


def test_the_files_are_plain_json_with_unix_line_endings() -> None:
    for path in samples.FOLDER.glob("*.json"):
        raw = path.read_bytes()
        assert b"\r" not in raw
        assert json.loads(raw)["id"] == path.stem


# ── the gallery and starting from one ────────────────────────


async def test_the_gallery_lists_every_sample(client: AsyncClient) -> None:
    listed = (await client.get(f"{API}/meta/samples")).json()
    assert [s["id"] for s in listed] == IDS
    store = next(s for s in listed if s["id"] == "store-support")
    assert store["documents"] == 4 and store["eval_cases"] == 5
    assert "knowledge_base" in store["node_types"]
    # The gallery is a list of summaries: not the graph, not the documents.
    assert "graph" not in store and "system_prompt" not in json.dumps(store)


@pytest.fixture
def no_queue(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Ingestion and eval runs queued into a list, not Redis."""
    seen: list[uuid.UUID] = []

    async def enqueue(item: uuid.UUID) -> bool:
        seen.append(item)
        return True

    monkeypatch.setattr("app.services.data_sources.enqueue_ingest", enqueue)
    monkeypatch.setattr(queue, "enqueue_eval", enqueue)
    return seen


async def _start(client: AsyncClient, headers: dict[str, str], sample_id: str, **body: Any) -> Any:
    return await client.post(
        f"{API}/assistants:from-sample", json={"sample_id": sample_id, **body}, headers=headers
    )


async def test_starting_from_a_sample_makes_an_ordinary_assistant(
    client: AsyncClient, org_headers: dict[str, str], no_queue: list[uuid.UUID]
) -> None:
    made = await _start(client, org_headers, "store-support")
    assert made.status_code == 201, made.text
    a = made.json()
    sample = samples.get("store-support")
    assert sample is not None
    assert a["name"] == "Store support desk" and a["status"] == "draft"
    assert a["draft_config"] == sample.config().model_dump(mode="json")
    assert a["draft_validation"] == {"errors": [], "warnings": []}

    sources = (
        await client.get(f"{API}/assistants/{a['id']}/data-sources", headers=org_headers)
    ).json()
    assert sorted(s["name"] for s in sources["items"]) == sorted(d.name for d in sample.documents)
    assert len(no_queue) == 4  # each one queued for indexing

    suites = (
        await client.get(f"{API}/assistants/{a['id']}/eval-suites", headers=org_headers)
    ).json()
    assert [(s["name"], s["case_count"]) for s in suites["suites"]] == [("Policy answers", 5)]

    # Through the same services as a person's clicks, so the audit log has it.
    async with get_sessionmaker()() as s:
        actions = set((await s.scalars(select(AuditLog.action))).all())
    assert {"assistant.create", "data_source.create", "eval_suite.create"} <= actions


async def test_a_sample_can_be_given_a_name_and_an_unknown_one_is_refused(
    client: AsyncClient, org_headers: dict[str, str], no_queue: list[uuid.UUID]
) -> None:
    named = await _start(client, org_headers, "team-notebook", name="  Platform team notes ")
    assert named.json()["name"] == "Platform team notes"
    twice = await _start(client, org_headers, "team-notebook")
    assert twice.status_code == 201  # as many as you like: each is its own assistant
    assert twice.json()["slug"] != named.json()["slug"]

    missing = await _start(client, org_headers, "no-such-sample")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "sample_not_found"
    assert (
        await client.post(f"{API}/assistants:from-sample", json={"sample_id": "team-notebook"})
    ).status_code == 401


async def _index_like_the_worker(assistant_id: str) -> None:
    """What the ingestion job leaves behind, without the job: each pasted
    text as one document and one chunk per paragraph, and the source ready."""
    async with get_sessionmaker()() as s:
        sources = (
            await s.scalars(
                select(DataSource).where(DataSource.assistant_id == uuid.UUID(assistant_id))
            )
        ).all()
        n = 0
        for source in sources:
            doc = Document(
                data_source_id=source.id,
                assistant_id=source.assistant_id,
                org_id=source.org_id,
                title=source.name,
            )
            s.add(doc)
            await s.flush()
            for paragraph in str(source.config["text"]).split("\n\n"):
                if paragraph.strip():
                    s.add(
                        Chunk(
                            document_id=doc.id,
                            assistant_id=source.assistant_id,
                            org_id=source.org_id,
                            ordinal=n,
                            content=paragraph.strip(),
                            chunk_metadata={"start": 0, "end": len(paragraph)},
                        )
                    )
                    n += 1
            source.status = DataSourceStatus.ready
        await s.commit()


@pytest.mark.parametrize("sample_id", IDS)
async def test_a_samples_own_eval_suite_passes_on_the_free_driver(
    sample_id: str,
    client: AsyncClient,
    org_headers: dict[str, str],
    no_queue: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plan's demo: build the sample assistant, run its eval suite.
    Every case is a real turn through the platform, on the offline driver."""
    lexical_rag.install(monkeypatch)
    a = (await _start(client, org_headers, sample_id)).json()
    await _index_like_the_worker(a["id"])
    suites = (
        await client.get(f"{API}/assistants/{a['id']}/eval-suites", headers=org_headers)
    ).json()
    sid = suites["suites"][0]["id"]
    started = await client.post(
        f"{API}/eval-suites/{sid}/runs", json={"assistant_version_id": None}, headers=org_headers
    )
    assert started.status_code == 202, started.text
    rid = started.json()["id"]
    await runner.run(uuid.UUID(rid))
    run = (await client.get(f"{API}/eval-runs/{rid}", headers=org_headers)).json()
    failed = [
        (r["input"], r["scores"], r["error"], r["output"][:200])
        for r in run["results"]
        if not r["passed"]
    ]
    assert run["status"] == "done" and failed == [], failed
    sample = samples.get(sample_id)
    assert sample is not None and sample.eval_suite is not None
    assert len(run["results"]) == len(sample.eval_suite.cases)
