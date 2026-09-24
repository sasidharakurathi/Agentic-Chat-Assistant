"""RAG pipeline: knowledge base ingestion + hybrid retrieval (Phase 2).

Layout mirrors the plan's ``app/rag/`` package: ``vectorstore/`` (pgvector,
later Qdrant), ``embedders/`` (Voyage, local fallback), ``rerankers/``
(Voyage, local fallback), and eventually ``retrieve.py`` (hybrid + RRF +
rerank, task 2.7).
"""

from __future__ import annotations

# voyage-3-large's output dimension — the fixed width of every embedding
# column and index in this app. Changing embedding models means a migration.
EMBEDDING_DIM = 1024

__all__ = ["EMBEDDING_DIM"]
