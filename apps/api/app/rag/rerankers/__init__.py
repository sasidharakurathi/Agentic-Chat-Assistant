from app.config import settings
from app.rag.rerankers.base import RankedResult, Reranker
from app.rag.rerankers.local import LocalCrossEncoderReranker
from app.rag.rerankers.voyage import VoyageReranker


def get_reranker() -> Reranker:
    if settings.rag_offline or not settings.voyage_api_key:
        return LocalCrossEncoderReranker()
    return VoyageReranker()


__all__ = [
    "LocalCrossEncoderReranker",
    "RankedResult",
    "Reranker",
    "VoyageReranker",
    "get_reranker",
]
