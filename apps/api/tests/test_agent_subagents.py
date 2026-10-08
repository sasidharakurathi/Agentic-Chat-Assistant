"""The retrieval subagent (task 2.10).

Opt-in, and gated on there actually being a knowledge base — an enabled
retrieval subagent with RAG off would be handed no tools and would burn a
turn discovering that.
"""

from __future__ import annotations

from app.agent.models import resolve_model
from app.agent.options import build_runtime_spec, compose_system_prompt
from app.agent.subagents import RETRIEVAL_TOOLS, build_subagent_specs
from app.schemas.assistant_config import default_config


def _config(*, retrieval: bool, rag: bool):
    cfg = default_config()
    return cfg.model_copy(
        update={
            "subagents": cfg.subagents.model_copy(update={"retrieval": retrieval}),
            "rag": cfg.rag.model_copy(update={"enabled": rag}),
        }
    )


def test_off_by_default() -> None:
    assert build_subagent_specs(default_config()) == []


def test_enabled_with_a_knowledge_base_produces_the_retrieval_spec() -> None:
    (spec,) = build_subagent_specs(_config(retrieval=True, rag=True))
    assert spec.name == "retrieval"
    assert spec.tools == RETRIEVAL_TOOLS
    assert spec.max_turns == 6
    # Cheap model per the plan — the whole point is keeping the expensive one
    # out of the search loop.
    assert spec.model == resolve_model(default_config().models.subagent.model)


def test_enabled_without_a_knowledge_base_produces_nothing() -> None:
    """Otherwise the subagent exists with kb tools that have nothing behind
    them — the same shape the graph validator flags as
    `subagent_without_knowledge_base`."""
    assert build_subagent_specs(_config(retrieval=True, rag=False)) == []


def test_a_knowledge_base_alone_does_not_opt_you_in() -> None:
    assert build_subagent_specs(_config(retrieval=False, rag=True)) == []


def test_the_spec_reaches_the_runtime_spec() -> None:
    import uuid

    spec = build_runtime_spec(_config(retrieval=True, rag=True), assistant_id=uuid.uuid4())
    assert [s.name for s in spec.subagents] == ["retrieval"]


def test_the_prompt_tells_the_main_agent_it_can_delegate() -> None:
    import uuid

    aid = uuid.uuid4()
    with_sub = compose_system_prompt(_config(retrieval=True, rag=True), aid)
    without = compose_system_prompt(_config(retrieval=False, rag=True), aid)
    assert "retrieval` subagent" in with_sub
    assert "subagent" not in without


def test_the_prompt_tells_it_not_to_renumber_citations() -> None:
    """The subagent hands back markers from the same per-conversation
    registry; renumbering them would point the sources panel at other
    chunks."""
    import uuid

    prompt = compose_system_prompt(_config(retrieval=True, rag=True), uuid.uuid4())
    assert "reuse the citation markers exactly" in prompt


def test_the_subagent_is_told_not_to_answer() -> None:
    from app.agent.subagents import RETRIEVAL_PROMPT

    assert "Do NOT answer the question yourself" in RETRIEVAL_PROMPT


def test_agent_definitions_build_for_the_real_sdk() -> None:
    """Shape check against the installed SDK — a wrong kwarg here would only
    surface on a real paid run otherwise."""
    from app.agent.subagents import build_agent_definitions

    specs = build_subagent_specs(_config(retrieval=True, rag=True))
    defs = build_agent_definitions(specs)
    assert set(defs) == {"retrieval"}
    assert defs["retrieval"].tools == RETRIEVAL_TOOLS
    assert defs["retrieval"].maxTurns == 6
