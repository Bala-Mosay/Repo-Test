"""Structural patch validation. Nothing here is ever executed."""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.errors import PatchValidationError
from repotest_bridge.validation.patch import require_valid_patch, validate_patch

TEST_DIFF = """\
--- a/tests/test_repro.py
+++ b/tests/test_repro.py
@@ -0,0 +1,3 @@
+def test_repro():
+    assert boundary(20) == 0
+    assert boundary(19) == 1
"""

PROD_DIFF = """\
--- a/src/lib/pricing.py
+++ b/src/lib/pricing.py
@@ -10,7 +10,7 @@
 def discount_rate(qty, rate):
-    if qty > 20:
+    if qty >= 20:
         return rate
"""


def test_valid_test_patch_passes() -> None:
    verdict = validate_patch(TEST_DIFF)
    assert verdict.valid
    assert verdict.reasons == []
    assert verdict.paths == ["tests/test_repro.py"]
    assert verdict.only_test_paths
    assert verdict.touches_test_files


def test_empty_patch_is_rejected() -> None:
    verdict = validate_patch("   \n")
    assert not verdict.valid
    assert "empty patch" in verdict.reasons


def test_non_diff_text_is_rejected() -> None:
    verdict = validate_patch("I fixed it by changing the comparison operator.")
    assert not verdict.valid
    assert "not a unified diff" in verdict.reasons


def test_diff_without_hunks_is_rejected() -> None:
    verdict = validate_patch("--- a/tests/test_x.py\n+++ b/tests/test_x.py\n")
    assert not verdict.valid
    assert verdict.reasons


def test_binary_patches_are_rejected() -> None:
    diff = (
        "GIT binary patch\n"
        "literal 4\n"
        "zcmVYy1234abcd\n"
        "--- a/tests/blob.bin\n"
        "+++ b/tests/blob.bin\n"
        "@@ -1,1 +1,1 @@\n"
    )
    verdict = validate_patch(diff)
    assert not verdict.valid
    assert any("binary" in r for r in verdict.reasons)


@pytest.mark.parametrize(
    "path",
    ["../../etc/passwd", "/etc/passwd", "C:/windows/system32/evil.py"],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    diff = (
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -1,1 +1,1 @@\n-old\n+new\n"
        "--- a/tests/test_ok.py\n+++ b/tests/test_ok.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    )
    verdict = validate_patch(diff)
    assert not verdict.valid
    assert any("unsafe path" in r for r in verdict.reasons)


def test_production_only_patch_is_rejected() -> None:
    verdict = validate_patch(PROD_DIFF)
    assert not verdict.valid
    assert verdict.only_test_paths is False
    assert any("does not touch any file that looks like a test" in r for r in verdict.reasons)


def test_mixed_test_and_production_patch_is_allowed() -> None:
    verdict = validate_patch(TEST_DIFF + "\n" + PROD_DIFF)
    assert verdict.valid
    assert verdict.only_test_paths is False


def test_require_valid_patch_raises_with_reason() -> None:
    with pytest.raises(PatchValidationError, match="not a unified diff"):
        require_valid_patch("prose")
    assert require_valid_patch(TEST_DIFF).valid


def test_fatal_hunk_mismatch_is_a_parse_failure() -> None:
    """Stray lines before the next @@ header: the shape git apply refuses."""
    diff = (
        "--- a/tests/test_a.py\n"
        "+++ b/tests/test_a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " ctx one\n ctx two\n ctx three\n ctx four\n"
        "+added line\n"
        "@@ -10,3 +11,3 @@\n"
        " ctx a\n-ctx b old\n+ctx b new\n ctx c\n"
    )
    verdict = validate_patch(diff)
    assert not verdict.valid
    assert any("declares 2 old / 3 new lines" in r for r in verdict.reasons)


def test_harmless_hunk_mismatch_is_recorded_but_still_valid() -> None:
    """LLMs miscount the final hunk constantly; it still applies, so it stays."""
    diff = (
        "--- a/tests/test_a.py\n"
        "+++ b/tests/test_a.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+one\n+two\n+three\n"
    )
    verdict = validate_patch(diff)
    assert verdict.valid
    assert any("declares 0 old / 2 new lines" in r for r in verdict.reasons)


def test_reasons_do_not_leak_into_the_unsafe_path_gate() -> None:
    """A hunk warning must not be mistaken for an unsafe-path rejection."""
    diff = (
        "--- a/tests/test_a.py\n"
        "+++ b/tests/test_a.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+one\n+two\n+three\n"
    )
    verdict = validate_patch(diff)
    assert verdict.valid
    assert not any("unsafe path" in r for r in verdict.reasons)


def test_normalisation_strips_repo_prefixes() -> None:
    diff = (
        "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
        "@@ -1,1 +1,1 @@\n-a\n+b\n"
    )
    assert validate_patch(diff).paths == ["tests/test_x.py"]


def test_allowed_root_is_recorded_but_not_required(tmp_path: Path) -> None:
    verdict = validate_patch(TEST_DIFF, allowed_root=tmp_path)
    assert verdict.valid
