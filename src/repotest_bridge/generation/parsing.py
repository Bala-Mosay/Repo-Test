"""Extraction of a unified diff (and optional rationale) from model output.

The model's output is untrusted. Parsing never executes anything, only splits
text into fenced blocks and validates that the result looks like a diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from repotest_bridge.errors import ParseError

DIFF_FENCE_RE = re.compile(
    r"```(?:diff|patch|udiff|diff-\w+)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
RATIONALE_FENCE_RE = re.compile(
    r"```rationale\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)
HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
PATH_RE = re.compile(r"^(?:---|\+\+\+) ([ab]/\S+|\S+)", re.MULTILINE)


@dataclass(frozen=True)
class HunkMismatch:
    """One ``@@`` header whose counts disagree with the lines under it."""

    line: int
    old_declared: int
    new_declared: int
    old_supplied: int
    new_supplied: int
    followed_by_hunk: bool

    @property
    def is_fatal(self) -> bool:
        """True when stray lines stand before the next ``@@`` header.

        That is the shape ``git apply`` refuses with "patch fragment without
        header", so it is rejected up front instead of during evaluation.
        """
        return self.followed_by_hunk

    def message(self) -> str:
        return (
            f"hunk at line {self.line} declares {self.old_declared} old / "
            f"{self.new_declared} new lines but supplies "
            f"{self.old_supplied} / {self.new_supplied}"
        )


@dataclass
class ParsedOutput:
    """Result of parsing one completion."""

    raw: str
    diff: str | None = None
    rationale: str | None = None
    fences_found: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.diff is not None and not self.errors


def extract_rationale(text: str) -> str | None:
    match = RATIONALE_FENCE_RE.search(text)
    return match.group(1).strip() if match else None


def extract_diff_blocks(text: str) -> list[str]:
    """All fenced blocks tagged as a diff, in document order."""
    return [m.group(1).strip() for m in DIFF_FENCE_RE.finditer(text)]


def paths_in_diff(diff: str) -> list[str]:
    """Normalised repo-relative paths touched by the diff."""
    out: list[str] = []
    for match in PATH_RE.finditer(diff):
        path = match.group(1)
        if path in {"a/", "b/"}:
            continue
        if path.startswith(("a/", "b/")):
            path = path[2:]
        if path not in out:
            out.append(path)
    # `--- /dev/null` / `+++ /dev/null` pairs are creation/deletion markers.
    return [p for p in out if p != "/dev/null"]


def looks_like_diff(diff: str) -> bool:
    """Cheap structural check: has file headers and at least one hunk."""
    if not diff.strip():
        return False
    has_header = bool(re.search(r"^(---|\+\+\+) ", diff, re.MULTILINE))
    has_hunk = bool(HUNK_RE.search(diff))
    changed = any(
        line.startswith(("+", "-", "diff --git", "@@"))
        for line in diff.splitlines()
    )
    return has_header and has_hunk and changed


def hunk_mismatches(diff: str) -> list[HunkMismatch]:
    """Every ``@@`` header whose declared line counts disagree with its body.

    LLM output violates this constantly and it is still frequently applicable,
    so a mismatch is only *fatal* when stray lines are left standing before the
    next ``@@`` header: that is the exact shape ``git apply`` rejects with an
    opaque ``patch fragment without header`` pointing nowhere near the real
    mistake. Anything else is recorded as a warning and the patch is applied.
    """
    found: list[HunkMismatch] = []
    lines = diff.rstrip("\n").splitlines()
    total = len(lines)
    header_positions = [i for i, line in enumerate(lines) if HUNK_HEADER_RE.match(line)]
    last_header = header_positions[-1] if header_positions else -1

    index = 0
    while index < total:
        header = HUNK_HEADER_RE.match(lines[index])
        if header is None:
            index += 1
            continue
        old_declared = int(header.group(2)) if header.group(2) is not None else 1
        new_declared = int(header.group(4)) if header.group(4) is not None else 1
        old_supplied = new_supplied = 0
        body: list[str] = []
        cursor = index + 1
        while cursor < total:
            line = lines[cursor]
            starts_new_file = (
                line.startswith("--- ")
                and cursor + 1 < total
                and lines[cursor + 1].startswith("+++ ")
            )
            if HUNK_HEADER_RE.match(line) or starts_new_file:
                break
            if line.startswith("\\"):  # "\ No newline at end of file"
                cursor += 1
                continue
            if not line.startswith(("+", "-", " ", "\t")):
                break
            body.append(line)
            cursor += 1
        # Blank lines used as section padding are not part of any hunk.
        while body and body[-1] == "":
            body.pop()
        for line in body:
            if line.startswith("+"):
                new_supplied += 1
            elif line.startswith("-"):
                old_supplied += 1
            else:
                old_supplied += 1
                new_supplied += 1
        if (old_supplied, new_supplied) != (old_declared, new_declared):
            found.append(
                HunkMismatch(
                    line=index + 1,
                    old_declared=old_declared,
                    new_declared=new_declared,
                    old_supplied=old_supplied,
                    new_supplied=new_supplied,
                    followed_by_hunk=index < last_header,
                )
            )
        index = cursor
    return found


def hunk_errors(diff: str) -> list[str]:
    """Readable messages for every :func:`hunk_mismatches` finding."""
    return [m.message() for m in hunk_mismatches(diff)]


def parse_completion(text: str) -> ParsedOutput:
    """Parse a completion into a diff + rationale, collecting diagnostics."""
    if not text or not text.strip():
        return ParsedOutput(raw=text or "", errors=["empty completion"])

    rationale = extract_rationale(text)
    blocks = extract_diff_blocks(text)
    result = ParsedOutput(raw=text, rationale=rationale, fences_found=[b[:80] for b in blocks])

    if not blocks:
        # Fall back to any hunk-bearing region, in case the fence language tag
        # was omitted or misspelled.
        stripped = text.strip()
        if HUNK_RE.search(stripped):
            blocks = [stripped]
        else:
            result.errors.append("no ```diff fence and no unified-diff hunks found")
            return result

    # Prefer the largest block: models sometimes emit a sample then the real diff.
    chosen = max(blocks, key=lambda b: (looks_like_diff(b), len(b)))
    if not looks_like_diff(chosen):
        result.errors.append("diff block lacks file headers or @@ hunks")
        return result

    result.diff = chosen
    if not paths_in_diff(chosen):
        result.errors.append("diff contains no parseable file paths")
        result.diff = None
    return result


def require_diff(text: str) -> ParsedOutput:
    """Parse or raise :class:`ParseError`."""
    parsed = parse_completion(text)
    if not parsed.ok:
        raise ParseError("; ".join(parsed.errors) or "could not parse completion")
    return parsed
