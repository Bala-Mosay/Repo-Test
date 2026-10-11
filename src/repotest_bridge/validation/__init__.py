"""Structural patch validation and safe application to working copies."""

from __future__ import annotations

from repotest_bridge.validation.collect import (
    ApplyResult,
    apply_diff,
    diff_worktree,
    prepare_workdir,
    reset_hard,
    stage_all,
)
from repotest_bridge.validation.patch import PatchVerdict, validate_patch

__all__ = [
    "ApplyResult",
    "PatchVerdict",
    "apply_diff",
    "diff_worktree",
    "prepare_workdir",
    "reset_hard",
    "stage_all",
    "validate_patch",
]
