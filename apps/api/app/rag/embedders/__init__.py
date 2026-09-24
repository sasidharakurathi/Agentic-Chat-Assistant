from app.config import settings
from app.rag.embedders.base import Embedder
from app.rag.embedders.local_bge import LocalBgeEmbedder
from app.rag.embedders.voyage import VoyageEmbedder


def get_embedder() -> Embedder:
    """Same auto-detection shape as ``app.agent.driver.get_driver``: explicit
    opt-out (``RAG_OFFLINE=1``) or a missing key both fall back to the free
    local model, so RAG never silently requires a paid key to function."""
    if settings.rag_offline or not settings.voyage_api_key:
        return LocalBgeEmbedder()
    return VoyageEmbedder()


__all__ = ["Embedder", "LocalBgeEmbedder", "VoyageEmbedder", "get_embedder"]
