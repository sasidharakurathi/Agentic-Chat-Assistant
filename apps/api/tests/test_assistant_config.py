from __future__ import annotations

import pytest
from app.schemas.assistant_config import (
    AssistantConfig,
    RagRetrieval,
    config_json_schema,
    default_config,
)
from pydantic import ValidationError


def test_default_config_is_valid_and_conservative() -> None:
    c = default_config()
    assert c.schema_version == 1
    assert c.models.main.model == "sonnet"
    assert c.models.router.model == "haiku"
    assert c.rag.enabled is False
    assert c.tools.calculator.enabled is False
    assert c.approval_policy.file_write == "deny"


def test_unknown_model_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown model"):
        AssistantConfig.model_validate({"models": {"main": {"model": "gpt-4o"}}})


def test_rerank_top_n_cannot_exceed_candidate_pool() -> None:
    with pytest.raises(ValidationError, match="exceeds the candidate pool"):
        RagRetrieval(top_k_dense=5, top_k_sparse=5, rerank_top_n=10)


def test_extra_keys_forbidden() -> None:
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"nope": 1})


def test_duplicate_database_refs_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate database connection_id"):
        AssistantConfig.model_validate(
            {"databases": [{"connection_id": "x"}, {"connection_id": "x"}]}
        )


def test_reference_lists_are_canonicalized() -> None:
    c = AssistantConfig.model_validate(
        {
            "databases": [{"connection_id": "b"}, {"connection_id": "a"}],
            "mcp_servers": [
                "22222222-2222-2222-2222-222222222222",
                "11111111-1111-1111-1111-111111111111",
            ],
            "rag": {"enabled": True, "source_ids": ["s2", "s1", "s2"]},
        }
    )
    assert [d.connection_id for d in c.databases] == ["a", "b"]
    # Bare ids (configs saved before task 4.6) load as servers with no tools.
    assert [(m.id, m.tools) for m in c.mcp_servers] == [
        ("11111111-1111-1111-1111-111111111111", []),
        ("22222222-2222-2222-2222-222222222222", []),
    ]
    assert c.rag.source_ids == ["s1", "s2"]


def test_json_schema_exports() -> None:
    schema = config_json_schema()
    assert schema["type"] == "object"
    assert "models" in schema["properties"]
    assert "$defs" in schema
