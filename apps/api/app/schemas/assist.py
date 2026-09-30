from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.graph.nodes import Graph
from app.graph.validate import ValidationResult
from app.schemas.assistant_config import AssistantConfig
from app.schemas.common import ApiModel


class PromptGenerateIn(ApiModel):
    """What the assistant is for, in the builder's words (task 5.5)."""

    description: str = Field(min_length=10, max_length=4_000)
    #: The prompt it has now, to improve rather than start over.
    current_prompt: str | None = Field(default=None, max_length=100_000)


class PromptSuggestion(ApiModel):
    """A draft to review: nothing has been saved."""

    system_prompt: str
    rules: list[str]
    #: "model" when a model wrote it, "template" on the free path.
    source: Literal["model", "template"]
    model: str | None = None
    cost_usd: float = 0.0


class PipelineRecommendIn(ApiModel):
    """What the assistant is for, in the builder's words (task 5.6)."""

    description: str = Field(min_length=10, max_length=4_000)
    #: Before the assistant exists (the wizard): what it will be called.
    name: str = Field(default="the assistant", min_length=1, max_length=200)


class PipelineCapability(ApiModel):
    """One thing the recommended pipeline uses, and why."""

    key: str
    label: str
    why: str | None = None


class PipelineSuggestion(ApiModel):
    """A whole starter pipeline to preview: nothing has been saved. Apply it
    by saving `config` as the draft config, which lays out the graph."""

    config: AssistantConfig
    #: How the canvas would look (positions kept for nodes already on it).
    graph: Graph
    validation: ValidationResult
    capabilities: list[PipelineCapability]
    #: What applying it would change, in words (empty for a new assistant).
    changes: list[str]
    source: Literal["model", "template"]
    model: str | None = None
    cost_usd: float = 0.0


__all__ = [
    "PipelineCapability",
    "PipelineRecommendIn",
    "PipelineSuggestion",
    "PromptGenerateIn",
    "PromptSuggestion",
]
