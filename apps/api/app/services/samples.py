"""Make an assistant from a shipped sample (task 6.7)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.samples import Sample
from app.services import assistants, data_sources, evals


async def create_from(
    session: AsyncSession,
    sample: Sample,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str | None = None,
    ip: str | None = None,
) -> Assistant:
    """A new draft assistant with the sample's graph, its documents queued
    for indexing, and its eval suite ready to run.

    Everything goes through the same services a person's clicks do (so the
    audit log, the validator and the ingestion queue see an ordinary
    assistant), in the order that keeps it valid at every step: the
    documents before the graph, because a knowledge base with nothing in it
    is a warning the person would meet first.
    """
    assistant = await assistants.create(
        session,
        org_id=org_id,
        user_id=user_id,
        name=(name or sample.name).strip(),
        description=sample.description,
        ip=ip,
    )
    for document in sample.documents:
        await data_sources.create_text(
            session,
            assistant=assistant,
            name=document.name,
            text=document.text,
            user_id=user_id,
            ip=ip,
        )
    await assistants.save_draft_graph(session, assistant, sample.graph)
    if sample.eval_suite is not None:
        suite = await evals.create_suite(
            session,
            assistant=assistant,
            name=sample.eval_suite.name,
            config=sample.eval_suite.config,
            actor_id=user_id,
        )
        await evals.add_cases(session, suite, list(sample.eval_suite.cases), replace=False)
    # Saving the graph changed the row; its server-set columns are read again
    # here, where that is allowed, and not lazily by whoever serializes it.
    await session.refresh(assistant)
    return assistant


__all__ = ["create_from"]
