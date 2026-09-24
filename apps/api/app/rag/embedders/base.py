"""``Embedder``: turns text into vectors of ``app.rag.EMBEDDING_DIM`` floats.

No concrete implementation lives here yet — the Voyage (``voyage-3-large``)
and local ``bge-m3`` fallback implementations are task 2.4. This interface is
what ``ingest_data_source`` (2.5) and ``retrieve.py`` (2.7) will be written
against, so that swap is a config change, not a rewrite.
"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    name: str
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed chunk content for indexing (Voyage's "document" input type)."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query (Voyage's "query" input type — asymmetric
        from document embedding, so this is a separate method, not just
        ``embed_documents([text])[0]``)."""
        ...


__all__ = ["Embedder"]
