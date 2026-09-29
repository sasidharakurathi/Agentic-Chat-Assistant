"""The local embedder runs one encode at a time, in small batches.

Two ingestion jobs in one worker used to encode in parallel: no faster (the
model already uses every core) and twice the memory, past 15 GB with two
large files. A fake model stands in for bge-m3, so this needs no download.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from app.rag.embedders import local_bge

pytestmark = pytest.mark.anyio


class _FakeModel:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.batch_sizes: list[int] = []
        self._lock = threading.Lock()

    def encode(self, texts: list[str], *, batch_size: int, **_: Any) -> list[Any]:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.batch_sizes.append(batch_size)
        time.sleep(0.05)
        with self._lock:
            self.active -= 1

        class _Vec(list):  # stands in for a numpy row
            def tolist(self) -> list[float]:
                return list(self)

        return [_Vec([0.0]) for _ in texts]


async def test_concurrent_jobs_encode_one_at_a_time_in_small_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(local_bge, "_get_model", lambda: fake)
    embedder = local_bge.LocalBgeEmbedder()

    await asyncio.gather(*(embedder.embed_documents(["a", "b"]) for _ in range(4)))

    assert fake.peak == 1, "encodes overlapped"
    assert set(fake.batch_sizes) == {local_bge.BATCH_SIZE}
    assert local_bge.BATCH_SIZE <= 8
