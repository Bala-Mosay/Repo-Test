"""Regenerate examples/demo_task/fixtures/mock_response.diff with correct headers."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURE = Path("examples/demo_task/fixtures/mock_response.diff")
REPO = Path("examples/demo_task/repo")
TARGET = "tests/test_issue_repro.py"


def added_body(diff_text: str) -> str:
    lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines) + "\n"


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", check=True
    )


def main() -> int:
    body = added_body(FIXTURE.read_text(encoding="utf-8"))

    scratch = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("experiments/_diffgen")
    if scratch.exists():
        shutil.rmtree(scratch)
    (scratch / "tests").mkdir(parents=True)
    (scratch / TARGET).write_text(body, encoding="utf-8", newline="\n")

    run(["git", "init", "--quiet"], scratch)
    run(
        ["git", "-c", "user.email=repotest@local", "-c", "user.name=RepoTest-Bridge", "add", "-A"],
        scratch,
    )
    diff = run(
        [
            "git",
            "-c",
            "user.email=repotest@local",
            "-c",
            "user.name=RepoTest-Bridge",
            "diff",
            "--cached",
            "--no-color",
        ],
        scratch,
    ).stdout

    if not re.search(r"^@@ -0,0 \+\d+,\d+ @@", diff, re.MULTILINE):
        print("unexpected diff shape:\n", diff[:800])
        return 1

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(diff, encoding="utf-8", newline="\n")
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    print(f"wrote {FIXTURE} ({added} added lines)")
    shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
