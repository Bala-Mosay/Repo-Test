"""Context items and bundles passed from retrieval to prompt construction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field


class ArtifactCategory(StrEnum):
    """Repository artifact classes that may be offered to the generator.

    ``SOURCE`` is the anchor class always available to every strategy; the other
    six are the independently ablated classes of the research question.
    """

    SOURCE = "source"
    RELATED_TEST = "related_test"
    FIXTURE = "fixture"
    CONFTEST = "conftest"
    HELPER = "helper"
    IMPORT_EDGE = "import_edge"
    PROJECT_CONFIG = "project_config"
    RANDOM_CONTROL = "random_control"

    @classmethod
    def ablated(cls) -> tuple[ArtifactCategory, ...]:
        """The six classes that the research arms vary."""
        return (
            cls.RELATED_TEST,
            cls.FIXTURE,
            cls.CONFTEST,
            cls.HELPER,
            cls.IMPORT_EDGE,
            cls.PROJECT_CONFIG,
        )


class ContextItem(BaseModel):
    """One file (or file fragment) selected for the prompt."""

    category: ArtifactCategory
    path: str
    content: str = ""
    score: float = 0.0
    rationale: str = ""
    sha256: str = ""
    tokens: int = 0

    @staticmethod
    def hash_content(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class CategoryBudget(BaseModel):
    """What a single artifact class was allowed to spend and did spend."""

    category: ArtifactCategory
    limit_tokens: int
    used_tokens: int = 0
    offered_items: int = 0
    accepted_items: int = 0
    truncated: bool = False
    reallocated: bool = False  # always False: budget is never back-filled


class ContextBundle(BaseModel):
    """The complete, budget-enforced context for one generation call."""

    strategy: str
    items: list[ContextItem] = Field(default_factory=list)
    budgets: list[CategoryBudget] = Field(default_factory=list)
    total_tokens: int = 0
    ceiling_tokens: int = 0
    dropped: list[str] = Field(default_factory=list, description="paths skipped, with reasons")

    def by_category(self, category: ArtifactCategory) -> list[ContextItem]:
        return [item for item in self.items if item.category is category]

    def category_totals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            key = item.category.value
            out[key] = out.get(key, 0) + item.tokens
        return dict(sorted(out.items()))

    def as_prompt_block(self) -> str:
        """Render the context as a stable, human-inspectable prompt section."""
        if not self.items:
            return "(no repository context retrieved for this strategy)"
        blocks: list[str] = []
        for category in ArtifactCategory:
            group = self.by_category(category)
            if not group:
                continue
            blocks.append(f"### {category.value}")
            for item in group:
                # Deliberately not `--- path ---`: that is visually identical to
                # a unified-diff file header, and models copy it straight into
                # their output as one.
                blocks.append(f"=== FILE: {item.path} ===")
                blocks.append(item.content.rstrip())
                blocks.append("")
        return "\n".join(blocks).rstrip()


@dataclass
class RetrievalReport:
    """Diagnostics attached to a run record."""

    strategy: str
    bundle: ContextBundle
    candidates_considered: int = 0
    per_stage: dict[str, int] = field(default_factory=dict)
