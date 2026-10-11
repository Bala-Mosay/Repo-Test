"""Patch validation: structural checks only, nothing executed here."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from repotest_bridge.errors import PatchValidationError
from repotest_bridge.generation.parsing import hunk_mismatches, looks_like_diff, paths_in_diff

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)
TEST_PATH_HINTS = re.compile(
    r"(^|/)(tests?|testing|spec|__tests__)(/|$)|(^|/)test_[^/]+$|_test\.py$|Tests\.java$",
    re.IGNORECASE,
)
PRODUCTION_SUFFIXES = frozenset({".pyx", ".c", ".h", ".so", ".pyd", ".dll", ".exe"})


@dataclass
class PatchVerdict:
    """Pass/fail with machine-readable reasons (protocol outcome codes)."""

    valid: bool
    paths: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    only_test_paths: bool = False

    @property
    def touches_test_files(self) -> bool:
        return any(TEST_PATH_HINTS.search(p) for p in self.paths)


def _normalise(path: str) -> PurePosixPath:
    p = PurePosixPath(path.replace("\\", "/"))
    # Strip repo-root prefixes some models emit.
    parts = list(p.parts)
    while parts and parts[0] in {".", "a", "b"}:
        parts = parts[1:]
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def _is_safe(path: PurePosixPath) -> bool:
    if path.is_absolute() or ".." in path.parts:
        return False
    text = str(path)
    if not text or text == ".":
        return False
    # A drive-qualified path is absolute on Windows even though PurePosixPath
    # does not consider it so.
    return not (len(text) >= 2 and text[1] == ":" and text[0].isalpha())


def validate_patch(
    diff: str,
    *,
    allowed_root: Path | None = None,
    require_test_paths: bool = True,
) -> PatchVerdict:
    """Structural safety check for a model-produced diff.

    Rejects: empty output, non-diff text, path traversal, absolute paths,
    production-code-only patches, and binary blobs. Never executes the patch.
    """
    reasons: list[str] = []

    if not diff or not diff.strip():
        return PatchVerdict(valid=False, reasons=["empty patch"])
    if not looks_like_diff(diff):
        return PatchVerdict(valid=False, reasons=["not a unified diff"])
    if not HUNK_RE.search(diff):
        return PatchVerdict(valid=False, reasons=["no @@ hunks"])
    mismatches = hunk_mismatches(diff)
    fatal = [m.message() for m in mismatches if m.is_fatal]
    if fatal:
        return PatchVerdict(valid=False, reasons=fatal)
    # A count mismatch on the final hunk is common in LLM output and still
    # applies cleanly, so it is recorded rather than fatal. Kept out of
    # `reasons` until the end so it cannot trip the unsafe-path rejection.
    hunk_warnings = [m.message() for m in mismatches]
    if re.search(r"^GIT binary patch", diff, re.MULTILINE):
        return PatchVerdict(valid=False, reasons=["binary patches are not accepted"])

    raw_paths = paths_in_diff(diff)
    if not raw_paths:
        return PatchVerdict(valid=False, reasons=["no file paths found"])

    paths: list[str] = []
    for raw in raw_paths:
        norm = _normalise(raw)
        if not _is_safe(norm):
            reasons.append(f"unsafe path: {raw}")
            continue
        paths.append(norm.as_posix())

    if reasons:
        return PatchVerdict(valid=False, paths=paths, reasons=reasons)

    test_paths = [p for p in paths if TEST_PATH_HINTS.search(p)]
    non_test = [p for p in paths if not TEST_PATH_HINTS.search(p)]
    only_test = not non_test

    if require_test_paths and not test_paths:
        reasons.append(
            "patch does not touch any file that looks like a test "
            f"(paths: {', '.join(paths[:6])})"
        )
    if non_test and all(
        PurePosixPath(p).suffix in PRODUCTION_SUFFIXES for p in non_test
    ):
        reasons.append(f"patch only touches compiled artifacts: {', '.join(non_test)}")

    if require_test_paths and not test_paths:
        return PatchVerdict(
            valid=False, paths=paths, reasons=reasons, only_test_paths=only_test
        )

    return PatchVerdict(
        valid=bool(paths),
        paths=paths,
        reasons=[*reasons, *hunk_warnings],
        only_test_paths=only_test,
    )


def require_valid_patch(diff: str, **kwargs: object) -> PatchVerdict:
    verdict = validate_patch(diff, **kwargs)  # type: ignore[arg-type]
    if not verdict.valid:
        raise PatchValidationError("; ".join(verdict.reasons) or "invalid patch")
    return verdict
