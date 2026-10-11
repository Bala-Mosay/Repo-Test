"""Run record writer: the reproducibility artefacts of protocol §6.

Layout of ``experiments/results/<experiment_id>/``:

    meta.json      experiment id, host, git SHA, manifest hash, model, budgets, seed
    tasks.jsonl    one record per (task, strategy)
    patches/       generated patches, never overwritten
    logs/          sanitized, truncated stdout/stderr
    summary.csv    one row per strategy
    report.md      human-readable summary
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repotest_bridge.config import AppConfig

RESULTS_ROOT = Path("experiments") / "results"
PROMPT_TEMPLATE_VERSION = "2026.10-issue-to-test"
TASKS_FILENAME = "tasks.jsonl"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_state() -> dict[str, Any]:
    def run(args: list[str]) -> str:
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""

    sha = run(["git", "rev-parse", "HEAD"])
    dirty = bool(run(["git", "status", "--porcelain"]))
    return {"sha": sha or None, "dirty": dirty}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_experiment_id(prefix: str = "exp") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass
class RunRecord:
    """Creates and populates one experiment directory."""

    experiment_id: str
    root: Path
    config: AppConfig
    manifest_path: Path | None = None
    strategies: list[str] = field(default_factory=list)
    provider_name: str = ""
    model: str = ""
    seed: int = 0
    task_ids: list[str] = field(default_factory=list)
    _tasks_fh: Any = field(default=None, repr=False)
    _written: set[tuple[str, str]] = field(default_factory=set)

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        config: AppConfig,
        *,
        experiment_id: str | None = None,
        prefix: str = "exp",
        root: Path | None = None,
        manifest_path: Path | None = None,
        strategies: Iterable[str] = (),
        provider_name: str = "",
        model: str = "",
        seed: int = 0,
        task_ids: Iterable[str] = (),
        resume: bool = True,
    ) -> RunRecord:
        base = root or RESULTS_ROOT
        eid = experiment_id or new_experiment_id(prefix)
        directory = base / eid
        if directory.exists() and not resume:
            raise FileExistsError(
                f"{directory} already exists; refusing to overwrite (protocol §6). Pass "
                "an explicit --experiment-id to resume it."
            )
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "patches").mkdir(exist_ok=True)
        (directory / "logs").mkdir(exist_ok=True)

        record = cls(
            experiment_id=eid,
            root=directory,
            config=config,
            manifest_path=manifest_path,
            strategies=list(strategies),
            provider_name=provider_name,
            model=model,
            seed=seed,
            task_ids=list(task_ids),
        )
        record.write_meta()
        record._tasks_fh = (directory / TASKS_FILENAME).open(
            "a", encoding="utf-8", newline="\n"
        )
        record._written = set(record.completed_keys())
        return record

    # -- meta --------------------------------------------------------------

    def write_meta(self, **extra: Any) -> dict[str, Any]:
        manifest_hash = None
        if self.manifest_path is not None and Path(self.manifest_path).is_file():
            manifest_hash = _sha256_file(Path(self.manifest_path))
        meta = {
            "experiment_id": self.experiment_id,
            "created_at": utc_now(),
            "host": platform.node(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "git": _git_state(),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "manifest_sha256": manifest_hash,
            "strategies": self.strategies,
            "provider": self.provider_name,
            # Protocol §6: a mock run validates the pipeline, never the model.
            "pipeline_validation": self.provider_name == "mock",
            "model": self.model,
            "temperature": self.config.generation.temperature,
            "max_output_tokens": self.config.generation.max_output_tokens,
            "max_retries": self.config.generation.max_retries,
            "budgets": {
                "max_context_tokens": self.config.retrieval.max_context_tokens,
                "per_category_tokens": self.config.retrieval.per_category_tokens,
                "max_items_per_category": self.config.retrieval.max_items_per_category,
            },
            "evaluation": {
                "test_timeout_seconds": self.config.evaluation.test_timeout_seconds,
                "max_output_bytes": self.config.evaluation.max_output_bytes,
            },
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "seed": self.seed,
            "task_ids": self.task_ids,
            **extra,
        }
        (self.root / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return meta

    # -- tasks.jsonl -------------------------------------------------------

    @staticmethod
    def _key(task_id: str, strategy: str) -> tuple[str, str]:
        return (task_id, strategy)

    def completed_keys(self) -> set[tuple[str, str]]:
        path = self.root / TASKS_FILENAME
        if not path.is_file():
            return set()
        done: set[tuple[str, str]] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(self._key(str(row.get("task_id")), str(row.get("strategy"))))
        return done

    def is_done(self, task_id: str, strategy: str) -> bool:
        return self._key(task_id, strategy) in self._written

    def append(self, record: dict[str, Any]) -> None:
        """Append one (task, strategy) record. Never overwrites a finished row."""
        key = self._key(str(record.get("task_id")), str(record.get("strategy")))
        if key in self._written:
            return
        self._tasks_fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        self._tasks_fh.flush()
        self._written.add(key)

    # -- side artefacts ----------------------------------------------------

    def save_patch(self, task_id: str, strategy: str, diff: str) -> Path:
        safe_task = "".join(c if c.isalnum() or c in "-_." else "_" for c in task_id)
        safe_strat = "".join(c if c.isalnum() or c in "-_." else "_" for c in strategy)
        path = self.root / "patches" / f"{safe_task}__{safe_strat}.diff"
        if path.exists():
            # Protocol §6: generated patches are never overwritten.
            path = path.with_suffix(".2.diff")
        path.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8")
        return path

    def save_log(self, name: str, content: str, *, max_bytes: int = 65_536) -> Path:
        path = self.root / "logs" / name
        blob = content.encode("utf-8", errors="replace")
        if len(blob) > max_bytes:
            blob = blob[:max_bytes] + b"\n... [truncated by RepoTest-Bridge]\n"
        path.write_bytes(blob)
        return path

    def read_tasks(self) -> list[dict[str, Any]]:
        path = self.root / TASKS_FILENAME
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def close(self) -> None:
        if self._tasks_fh is not None and not self._tasks_fh.closed:
            self._tasks_fh.close()

    def __enter__(self) -> RunRecord:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
