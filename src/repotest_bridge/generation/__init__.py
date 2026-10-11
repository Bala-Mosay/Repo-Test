"""Test-patch generation from an issue plus budgeted repository context."""

from __future__ import annotations

from repotest_bridge.generation.generator import (
    GenerationRecord,
    prepare_project,
    run_arms,
    run_generation,
)
from repotest_bridge.generation.parsing import ParsedOutput, parse_completion, paths_in_diff
from repotest_bridge.generation.prompts import build_messages, describe_bundle, prompt_digest

__all__ = [
    "GenerationRecord",
    "ParsedOutput",
    "build_messages",
    "describe_bundle",
    "parse_completion",
    "paths_in_diff",
    "prepare_project",
    "prompt_digest",
    "run_arms",
    "run_generation",
]
