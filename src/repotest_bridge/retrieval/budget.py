"""Token budgeting.

Two hard rules, both enforced here and asserted by the test suite:

1. **Per-class ceilings.** No artifact class may exceed its own allowance.
2. **No reallocation.** Budget an arm cannot use is *not* handed to another
   class -- otherwise arms would silently differ in total context size and the
   cost-normalised comparison in H3/H4 would be invalid.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from repotest_bridge.config import RetrievalConfig
from repotest_bridge.retrieval.types import ArtifactCategory, CategoryBudget, ContextItem

# Rough but *deterministic* estimate: words + punctuation separated out.
_WORD_RE = re.compile(r"\S+")
_CLIPPED_WORDS_PER_TOKEN = 0.75  # measured ratio for typical Python/prompt text


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate (no tokenizer dependency).

    Uses a word-piece heuristic calibrated against ordinary English + Python
    source. It is used for *budgeting*, where consistency across arms matters
    far more than absolute accuracy; provider-reported token counts are
    recorded separately in run metadata.
    """
    if not text:
        return 0
    words = len(_WORD_RE.findall(text))
    # Long identifiers, paths and code lines split into more than one token.
    heavy = sum(1 for w in _WORD_RE.findall(text) if len(w) > 12 or "/" in w or "." in w)
    return max(1, int(words / _CLIPPED_WORDS_PER_TOKEN) + heavy)


def _category_limit(config: RetrievalConfig, category: ArtifactCategory) -> int:
    if category is ArtifactCategory.RANDOM_CONTROL:
        # The control is matched against the full arm's spend, so it gets the
        # global ceiling and is trimmed by the caller to a target size.
        return config.max_context_tokens
    return config.per_category_tokens.get(
        category.value, config.max_context_tokens
    )


def apply_budget(
    candidates: Iterable[ContextItem],
    config: RetrievalConfig,
    *,
    max_items_per_category: int | None = None,
) -> tuple[list[ContextItem], list[CategoryBudget], int, list[str]]:
    """Trim candidates to per-class and global ceilings.

    Returns ``(accepted, budgets, total_tokens, dropped_paths)``.
    """
    per_category_cap = (
        max_items_per_category
        if max_items_per_category is not None
        else config.max_items_per_category
    )
    buckets: dict[ArtifactCategory, list[ContextItem]] = {}
    for item in candidates:
        buckets.setdefault(item.category, []).append(item)

    accepted: list[ContextItem] = []
    budgets: list[CategoryBudget] = []
    dropped: list[str] = []

    for category in ArtifactCategory:
        items = buckets.get(category)
        if not items:
            continue
        items = sorted(items, key=lambda i: (-i.score, i.path))
        limit = _category_limit(config, category)
        budget = CategoryBudget(
            category=category, limit_tokens=limit, offered_items=len(items)
        )
        spent = 0
        kept: list[ContextItem] = []
        for item in items:
            if len(kept) >= per_category_cap:
                budget.truncated = True
                dropped.append(f"{item.path}: item cap ({per_category_cap}) for {category.value}")
                continue
            cost = item.tokens or estimate_tokens(item.content)
            if spent + cost > limit:
                budget.truncated = True
                dropped.append(
                    f"{item.path}: would exceed {category.value} budget "
                    f"({spent}+{cost} > {limit})"
                )
                continue
            item.tokens = cost
            kept.append(item)
            spent += cost
        budget.used_tokens = spent
        budget.accepted_items = len(kept)
        budgets.append(budget)
        accepted.extend(kept)

    # Global ceiling. Applied last, deterministically, in category order then
    # by score, so every strategy hits the wall the same way.
    total = sum(i.tokens for i in accepted)
    if total > config.max_context_tokens:
        order = sorted(
            accepted,
            key=lambda i: (
                list(ArtifactCategory).index(i.category) if i.category in ArtifactCategory else 99,
                -i.score,
                i.path,
            ),
        )
        survivors: list[ContextItem] = []
        spent = 0
        kept_ids: set[int] = set()
        for item in order:
            if spent + item.tokens <= config.max_context_tokens:
                survivors.append(item)
                kept_ids.add(id(item))
                spent += item.tokens
            else:
                dropped.append(
                    f"{item.path}: global ceiling ({config.max_context_tokens} tokens)"
                )
        # Preserve deterministic category order in the final bundle.
        accepted = [i for i in accepted if id(i) in kept_ids]
        total = spent
        for budget in budgets:
            spent_for_cat = sum(i.tokens for i in accepted if i.category is budget.category)
            if spent_for_cat < budget.used_tokens:
                budget.truncated = True
                budget.used_tokens = spent_for_cat

    budgets = [b for b in budgets if b.offered_items > 0]
    return accepted, budgets, total, dropped


def spend_by_category(items: Iterable[ContextItem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item.category.value] = out.get(item.category.value, 0) + item.tokens
    return dict(sorted(out.items()))
