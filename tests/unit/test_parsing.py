"""Untrusted model output parsing. Nothing here is ever executed."""

from __future__ import annotations

import pytest

from repotest_bridge.errors import ParseError
from repotest_bridge.generation.parsing import (
    ParsedOutput,
    extract_diff_blocks,
    hunk_errors,
    hunk_mismatches,
    looks_like_diff,
    parse_completion,
    paths_in_diff,
    require_diff,
)

DIFF = """\
--- a/tests/test_new.py
+++ b/tests/test_new.py
@@ -0,0 +1,4 @@
+def test_boundary():
+    # boundary condition from the issue
+    assert apply(20) == 0
+    assert apply(19) == 1
"""


def test_fenced_diff_is_extracted() -> None:
    parsed = parse_completion(f"Here is the test.\n\n```diff\n{DIFF}```\n")
    assert parsed.ok
    assert parsed.diff is not None
    assert "test_boundary" in parsed.diff
    assert parsed.errors == []


def test_every_fenced_diff_block_is_listed() -> None:
    text = f"```diff\n{DIFF}```\n\nnotes\n\n```diff\n{DIFF}```\n"
    blocks = extract_diff_blocks(text)
    assert len(blocks) == 2
    assert all("test_boundary" in b for b in blocks)


def test_unfenced_diff_falls_back_to_hunk_detection() -> None:
    parsed = parse_completion(DIFF)
    assert parsed.ok, parsed.errors


def test_rationale_block_is_captured_separately() -> None:
    text = f"```diff\n{DIFF}```\n\n```rationale\nCovers the tier boundary.\n```\n"
    parsed = parse_completion(text)
    assert parsed.ok
    assert parsed.rationale == "Covers the tier boundary."


def test_empty_completion_is_rejected() -> None:
    for blank in ("", "   ", "\n\t"):
        parsed = parse_completion(blank)
        assert not parsed.ok
        assert "empty completion" in parsed.errors


def test_prose_without_a_diff_is_rejected() -> None:
    parsed = parse_completion("I could not reproduce the issue, sorry.")
    assert not parsed.ok
    assert parsed.diff is None


def test_fence_without_headers_or_hunks_is_rejected() -> None:
    parsed = parse_completion("```diff\njust some prose\n```\n")
    assert not parsed.ok


def test_largest_plausible_block_wins() -> None:
    small = "```diff\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```"
    big = (
        "```diff\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -1,2 +1,3 @@\n def test():\n-    pass\n+    assert True\n+    assert True\n```"
    )
    parsed = parse_completion(f"sample:\n{small}\n\nnow the real one:\n{big}\n")
    assert parsed.ok
    assert "tests/test_a.py" in (parsed.diff or "")


def test_paths_are_normalised_across_prefix_styles() -> None:
    for prefix in ("a/", "b/", ""):
        diff = f"--- {prefix}tests/test_x.py\n+++ {prefix}tests/test_x.py\n@@ -1 +1 @@\n"
        assert paths_in_diff(diff) == ["tests/test_x.py"]
    assert paths_in_diff("--- /dev/null\n+++ /dev/null\n@@ -1 +1 @@\n") == []


def test_looks_like_diff_needs_header_hunk_and_change() -> None:
    assert looks_like_diff(DIFF)
    assert not looks_like_diff("--- a/x\n+++ b/x\n")
    assert not looks_like_diff("@@ -1 +1 @@\n-a\n+b\n")
    assert not looks_like_diff("")


@pytest.mark.parametrize("text", ["", "nope"])
def test_require_diff_raises(text: str) -> None:
    with pytest.raises(ParseError):
        require_diff(text)


def test_require_diff_returns_parsed_output() -> None:
    parsed = require_diff(f"```diff\n{DIFF}```")
    assert isinstance(parsed, ParsedOutput)
    assert parsed.ok


# --- hunk arithmetic --------------------------------------------------------

OVER_LONG_THEN_ANOTHER_HUNK = """\
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,2 +1,3 @@
 ctx one
 ctx two
 ctx three
 ctx four
+added line
@@ -10,3 +11,3 @@
 ctx a
-ctx b old
+ctx b new
 ctx c
"""

OVER_LONG_FINAL_HUNK = """\
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -0,0 +1,2 @@
+one
+two
+three
"""


def test_matching_hunks_produce_no_findings() -> None:
    assert hunk_mismatches(DIFF) == []
    assert hunk_errors(DIFF) == []


def test_stray_lines_before_the_next_hunk_are_fatal() -> None:
    """This is the shape `git apply` rejects as 'patch fragment without header'."""
    mismatches = hunk_mismatches(OVER_LONG_THEN_ANOTHER_HUNK)
    assert len(mismatches) == 1
    assert mismatches[0].is_fatal
    assert "declares 2 old / 3 new" in mismatches[0].message()
    assert "supplies 4 / 5" in mismatches[0].message()


def test_mismatch_on_the_final_hunk_is_only_a_warning() -> None:
    """Common in LLM output and still applicable -- record, do not reject."""
    mismatches = hunk_mismatches(OVER_LONG_FINAL_HUNK)
    assert len(mismatches) == 1
    assert not mismatches[0].is_fatal
    assert not mismatches[0].followed_by_hunk


def test_real_malformed_model_patch_is_flagged() -> None:
    """The `repo_aware` patch from smoke-real-01: two bad hunks, first is fatal."""
    diff = """\
--- tests/test_pricing.py ---
+++ tests/test_pricing.py ---
@@ -1,6 +1,7 @@
ctx line 1
ctx line 2
ctx line 3
ctx line 4
ctx line 5
ctx line 6
+'""' + '""'
ctx tail 1
ctx tail 2
ctx tail 3
ctx tail 4
ctx tail 5
@@ -12,6 +13,20 @@
more 1
more 2
more 3
more 4
more 5
more 6
"""
    mismatches = hunk_mismatches(diff)
    assert mismatches
    assert any(m.is_fatal for m in mismatches)


def test_blank_section_padding_is_not_counted_as_context() -> None:
    diff = DIFF + "\n--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert hunk_mismatches(diff) == []
