"""Bounded, isolated subprocess execution.

This is the only module in the package permitted to run repository code, and it
does so with an explicit timeout, bounded captured output, a controlled working
directory, and a scrubbed environment (docs/architecture.md invariant 5).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from repotest_bridge.config import EvaluationConfig
from repotest_bridge.errors import ExecutionError

# Substrings that indicate the *test framework* could not run at all.
COLLECTION_MARKERS = (
    "errors while collecting",
    "error collecting",
    "collectionerror",
    "importerror",
    "modulenotfounderror",
    "cannot import name",
    "syntaxerror",
    "failed to import",
    "internal error occurred",
)
TIMEOUT_MARKERS = ("timeout", "timed out", "command timed out")

BASE_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
}


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment: no API keys, no provider credentials."""
    env = {k: v for k, v in os.environ.items() if k in BASE_ENV_ALLOWLIST}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    # Never let a repository's tests see the model credentials.
    for key in list(env):
        if key.endswith("_API_KEY") or key in {"OPENAI_API_KEY", "GROQ_API_KEY"}:
            env.pop(key, None)
    if extra:
        env.update(extra)
    return env


@dataclass
class CommandResult:
    """Outcome of one bounded subprocess run."""

    command: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_ms: float
    truncated: bool = False
    cwd: str = ""
    pid: int | None = None

    def combined(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)


@dataclass
class ExecutionPlan:
    """A shell-ish command string parsed into argv with a working directory."""

    raw: str
    cwd: Path

    @property
    def argv(self) -> list[str]:
        if not self.raw.strip():
            raise ExecutionError("empty test command")
        tokens = shlex.split(self.raw, posix=os.name != "nt")
        if not tokens:
            raise ExecutionError(f"could not parse test command: {self.raw!r}")
        if tokens[0] in {"python", "python3"} and tokens[0] != sys.executable:
            # Pin to the current interpreter so `python -m pytest` is stable.
            tokens = [sys.executable, *tokens[1:]]
        return tokens


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text, False
    clipped = encoded[:limit].decode("utf-8", errors="replace")
    return clipped + "\n... [output truncated by RepoTest-Bridge]", True


def run_command(
    plan: ExecutionPlan,
    config: EvaluationConfig,
    *,
    extra_env: dict[str, str] | None = None,
) -> CommandResult:
    """Run one command with timeout + bounded output. Never raises on non-zero exit."""
    if not plan.cwd.is_dir():
        raise ExecutionError(f"working directory does not exist: {plan.cwd}")

    argv = plan.argv
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(plan.cwd),
            env=build_env(extra_env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except OSError as exc:
        return CommandResult(
            command=" ".join(argv),
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr=f"failed to launch: {exc}",
            duration_ms=(time.perf_counter() - started) * 1000,
            cwd=str(plan.cwd),
        )

    timed_out = False
    try:
        out, err = proc.communicate(timeout=config.test_timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
            out, err = "", ""
    duration = (time.perf_counter() - started) * 1000

    out = out or ""
    err = err or ""
    out_t, trunc1 = _truncate(out, config.max_output_bytes)
    err_t, trunc2 = _truncate(err, config.max_output_bytes)

    return CommandResult(
        command=" ".join(argv),
        exit_code=None if timed_out else proc.returncode,
        timed_out=timed_out,
        stdout=out_t,
        stderr=err_t,
        duration_ms=duration,
        truncated=trunc1 or trunc2,
        cwd=str(plan.cwd),
        pid=proc.pid,
    )
