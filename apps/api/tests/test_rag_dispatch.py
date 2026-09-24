"""`get_embedder`/`get_reranker` auto-selection — same shape as
`app.agent.driver.get_driver`: an explicit opt-out or a missing key both
fall back to the free local implementation."""

from __future__ import annotations

import pytest
from app.rag.embedders import get_embedder
from app.rag.embedders.local_bge import LocalBgeEmbedder
from app.rag.embedders.voyage import VoyageEmbedder
from app.rag.rerankers import get_reranker
from app.rag.rerankers.local import LocalCrossEncoderReranker
from app.rag.rerankers.voyage import VoyageReranker


def test_offline_flag_forces_local_even_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.embedders.settings.rag_offline", True)
    monkeypatch.setattr("app.rag.embedders.settings.voyage_api_key", "has-a-key")
    monkeypatch.setattr("app.rag.rerankers.settings.rag_offline", True)
    monkeypatch.setattr("app.rag.rerankers.settings.voyage_api_key", "has-a-key")

    assert isinstance(get_embedder(), LocalBgeEmbedder)
    assert isinstance(get_reranker(), LocalCrossEncoderReranker)


def test_missing_key_falls_back_to_local_even_when_not_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.rag.embedders.settings.rag_offline", False)
    monkeypatch.setattr("app.rag.embedders.settings.voyage_api_key", "")
    monkeypatch.setattr("app.rag.rerankers.settings.rag_offline", False)
    monkeypatch.setattr("app.rag.rerankers.settings.voyage_api_key", "")

    assert isinstance(get_embedder(), LocalBgeEmbedder)
    assert isinstance(get_reranker(), LocalCrossEncoderReranker)


def test_a_real_key_and_offline_off_selects_voyage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.embedders.settings.rag_offline", False)
    monkeypatch.setattr("app.rag.embedders.settings.voyage_api_key", "real-key")
    monkeypatch.setattr("app.rag.rerankers.settings.rag_offline", False)
    monkeypatch.setattr("app.rag.rerankers.settings.voyage_api_key", "real-key")

    assert isinstance(get_embedder(), VoyageEmbedder)
    assert isinstance(get_reranker(), VoyageReranker)
