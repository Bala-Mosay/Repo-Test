"""Applying a validated diff to a throwaway working copy.

The working copy is always a *copy* of the buggy base checkout that RepoTest-Bridge
owns; the source checkout is never modified. Application uses ``git apply`` so
that partial, reverse, and 3-way failures surface as real tool errors rather
than silent corruption.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repotest_bridge.errors import ExecutionError, PatchValidationError

GIT_IDENTITY = ["-c", "user.email=repotest@local", "-c", "user.name=RepoTest-Bridge"]
GIT_IDENTITY_EXEC = ["-c", "core.autocrlf=false"]


def _git(*args: str) -> list[str]:
    """Build a complete git argv: executable, identity flags, then subcommand."""
    return ["git", *GIT_IDENTITY, *GIT_IDENTITY_EXEC, *args]


def _run(args: list[str], *, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    if not args or args[0] != "git":
        # Every call in this module is a git subcommand; a missing executable
        # would otherwise surface later as a baffling WinError 2.
        raise ExecutionError(f"internal error: argv does not start with 'git': {args[:2]!r}")
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def git_available() -> bool:
    return shutil.which("git") is not None


@dataclass
class ApplyResult:
    applied: bool
    method: str
    stdout: str = ""
    stderr: str = ""
    files_added: list[str] = field(default_factory=list)


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def ensure_repo(path: Path) -> None:
    """Create an unborn-but-initialised git repo so diffs can be applied/reset."""
    if not git_available():
        raise ExecutionError(
            "git is required for patch application but was not found on PATH"
        )
    if is_git_repo(path):
        return
    init = _run(["git", "init", "--quiet"], cwd=path)
    if init.returncode != 0:
        raise ExecutionError(f"git init failed: {init.stderr.strip()[:300]}")
    _run(_git("add", "-A"), cwd=path)
    commit = _run(_git("commit", "--quiet", "-m", "base checkout"), cwd=path)
    if commit.returncode != 0:
        raise ExecutionError(f"initial commit failed: {commit.stderr.strip()[:300]}")


def head_tree(path: Path) -> str | None:
    result = _run(["git", "rev-parse", "HEAD"], cwd=path)
    return result.stdout.strip() if result.returncode == 0 else None


def reset_hard(path: Path) -> None:
    if not is_git_repo(path):
        return
    args = _git("reset", "--hard", "--quiet", "HEAD")
    result = _run(args, cwd=path)
    if result.returncode != 0:
        raise ExecutionError(f"git reset failed: {result.stderr.strip()[:300]}")
    _run(["git", "clean", "-fdq", "--exclude=.repotest-eval"], cwd=path)


def _diff_to_file(path: Path, diff: str) -> Path:
    payload = diff if diff.endswith("\n") else diff + "\n"
    patch_file = path / ".repotest-bridge.patch"
    patch_file.write_text(payload, encoding="utf-8", newline="\n")
    return patch_file


def apply_diff(path: Path, diff: str, *, reverse: bool = False) -> ApplyResult:
    """Apply a unified diff to ``path``. Raises on failure."""
    if not is_git_repo(path):
        ensure_repo(path)

    patch_file = _diff_to_file(path, diff)
    before = _tracked_files(path)

    args = [
        "git",
        *GIT_IDENTITY,
        *GIT_IDENTITY_EXEC,
        "apply",
        "--whitespace=nowarn",
        "--unidiff-zero",
    ]
    if reverse:
        args.append("--reverse")
    args.append(str(patch_file.name))

    result = _run(args, cwd=path)
    if result.returncode != 0:
        # Retry without --unidiff-zero: model diffs normally carry real context.
        args.remove("--unidiff-zero")
        result = _run(args, cwd=path)

    if result.returncode != 0:
        raise PatchValidationError(
            "git apply failed: " + (result.stderr.strip()[:600] or result.stdout.strip()[:600])
        )

    after = _tracked_files(path)
    added = sorted(set(after) - set(before))
    return ApplyResult(
        applied=True,
        method="git apply",
        stdout=result.stdout,
        stderr=result.stderr,
        files_added=added,
    )


def _tracked_files(path: Path) -> list[str]:
    result = _run(["git", "ls-files", "-z"], cwd=path)
    if result.returncode != 0:
        return []
    return [f for f in result.stdout.split("\0") if f]


def stage_all(path: Path) -> None:
    if not is_git_repo(path):
        return
    _run(_git("add", "-A"), cwd=path)


def diff_worktree(path: Path) -> str:
    """The working-tree diff against HEAD, used to harvest generated tests."""
    result = _run(["git", "diff", "--no-color", "--binary", "HEAD"], cwd=path)
    if result.returncode != 0:
        raise ExecutionError(f"git diff failed: {result.stderr.strip()[:300]}")
    return result.stdout


def prepare_workdir(base: Path, dest: Path) -> Path:
    """Copy ``base`` (minus VCS/cache metadata) into ``dest`` and make it a repo."""
    if not base.is_dir():
        raise ExecutionError(f"base checkout does not exist: {base}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(
        base,
        dest,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", ".repotest-eval", "*.pyc"
        ),
    )
    ensure_repo(dest)
    return dest
