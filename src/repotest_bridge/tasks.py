"""Task manifest models with enforced separation of generation and gold data.

Two distinct types leave this module:

* :class:`GenerationView` -- the only object ever handed to retrieval, prompt
  construction, or generation. Its field set is fixed and asserted by test.
* :class:`EvaluationInputs` -- reachable only through :func:`load_evaluation`,
  which evaluation code calls explicitly.

There is deliberately no function that returns both.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from repotest_bridge.errors import ManifestError, RepositoryError

Split = Literal["demo", "dev", "eval"]
SPLIT_VALUES: tuple[str, ...] = get_args(Split)

# Substrings that must never appear on a GenerationView field name.
GOLD_FIELD_MARKERS: tuple[str, ...] = (
    "evaluation",
    "fixed",
    "gold",
    "fail_to_pass",
    "pass_to_pass",
    "code_patch",
    "test_patch",
    "oracle",
    "solution",
)


class RuntimeSpec(BaseModel):
    python: str = ">=3.11"
    extra: dict[str, str] = Field(default_factory=dict)


class GenerationInputs(BaseModel):
    """Everything a generator is allowed to see. No gold data by construction.

    The shape mirrors ``docs/experiment_protocol.md`` §1 exactly: a flat
    ``generation:`` block. ``evaluation:`` is a *sibling* key and is never read
    while constructing this object.
    """

    repo_url: str | None = None
    local_path: str | None = None
    base_commit: str | None = None
    issue_title: str = Field(min_length=1)
    issue_body: str = Field(min_length=1)
    issue_source_url: str | None = None
    test_command: str = Field(min_length=1)
    language: str = "python"
    test_framework: str = "pytest"
    runtime_requirements: RuntimeSpec = Field(default_factory=RuntimeSpec)
    license: str = "unknown"
    provenance: str = "unknown"
    notes: str | None = None
    exclusion_reason: str | None = None

    @field_validator("local_path")
    @classmethod
    def _relative_only(cls, value: str | None) -> str | None:
        if value is None:
            return value
        p = Path(value)
        if p.is_absolute():
            raise ValueError("local_path must be relative to the manifest file")
        if ".." in p.parts:
            raise ValueError("local_path must not escape the manifest directory")
        return p.as_posix()


class EvaluationInputs(BaseModel):
    """Evaluation-only data. Never constructed by generation code."""

    fixed_commit: str | None = None
    code_patch_file: str | None = None
    gold_test_patch_file: str | None = None
    fail_to_pass: list[str] = Field(default_factory=list)

    @field_validator("code_patch_file", "gold_test_patch_file")
    @classmethod
    def _relative_only(cls, value: str | None) -> str | None:
        if value is None:
            return value
        p = Path(value)
        if p.is_absolute():
            raise ValueError("patch paths must be relative to the manifest file")
        if ".." in p.parts:
            raise ValueError("patch paths must not escape the manifest directory")
        return p.as_posix()


class GenerationView(BaseModel):
    """The sole interface between a task and everything before evaluation.

    Constructed only by :func:`generation_view`. If a field name matching
    :data:`GOLD_FIELD_MARKERS` is ever added here, the test suite fails --
    see ``tests/unit/test_tasks.py``.
    """

    task_id: str
    split: Split
    repo_url: str | None
    local_path: str | None
    base_commit: str | None
    issue_title: str
    issue_body: str
    issue_source_url: str | None
    test_command: str
    language: str
    test_framework: str
    runtime_requirements: dict[str, str]
    license: str
    provenance: str
    notes: str | None
    exclusion_reason: str | None


class TaskEntry(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    split: Split
    generation: GenerationInputs
    evaluation: EvaluationInputs | None = None

    @field_validator("task_id")
    @classmethod
    def _slug(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        if not set(value) <= allowed:
            raise ValueError("task_id may only contain [A-Za-z0-9-_.]")
        return value


class TaskManifest(BaseModel):
    version: int = Field(ge=1)
    tasks: list[TaskEntry]

    @field_validator("tasks")
    @classmethod
    def _unique_ids(cls, tasks: list[TaskEntry]) -> list[TaskEntry]:
        seen: set[str] = set()
        for task in tasks:
            if task.task_id in seen:
                raise ValueError(f"duplicate task_id: {task.task_id}")
            seen.add(task.task_id)
        return tasks


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_manifest(path: str | Path) -> tuple[TaskManifest, Path]:
    """Load and validate a manifest. Returns the manifest and its resolved path."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ManifestError(f"manifest not found: {resolved}")
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest {resolved} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest {resolved} must be a mapping at the top level")
    try:
        manifest = TaskManifest.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ManifestError(f"manifest {resolved} invalid: {problems}") from exc
    if not manifest.tasks:
        raise ManifestError(f"manifest {resolved} contains no tasks")
    return manifest, resolved


def generation_view(task: TaskEntry) -> GenerationView:
    """Project a task onto the generation-safe view."""
    gen = task.generation
    return GenerationView(
        task_id=task.task_id,
        split=task.split,
        repo_url=gen.repo_url,
        local_path=gen.local_path,
        base_commit=gen.base_commit,
        issue_title=gen.issue_title,
        issue_body=gen.issue_body,
        issue_source_url=gen.issue_source_url,
        test_command=gen.test_command,
        language=gen.language,
        test_framework=gen.test_framework,
        runtime_requirements={
            "python": gen.runtime_requirements.python,
            **gen.runtime_requirements.extra,
        },
        license=gen.license,
        provenance=gen.provenance,
        notes=gen.notes,
        exclusion_reason=gen.exclusion_reason,
    )


def load_evaluation(task: TaskEntry) -> EvaluationInputs | None:
    """Return gold data. Callers outside ``repotest_bridge.evaluation`` must not use this."""
    return task.evaluation


def assert_generation_view_is_pure(view: GenerationView) -> None:
    """Raise if any generation field name betrays gold information."""
    offending = [
        name
        for name in type(view).model_fields
        if any(marker in name.lower() for marker in GOLD_FIELD_MARKERS)
    ]
    if offending:
        raise AssertionError(f"GenerationView exposes gold-like fields: {offending}")


def generation_inputs_fingerprint(view: GenerationView) -> str:
    """Stable hash of exactly what the generator was shown (for run records)."""
    payload = view.model_dump(mode="json")
    blob = yaml.safe_dump(payload, sort_keys=True, allow_unicode=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_in_manifest_dir(manifest_path: Path, relative: str | None) -> Path | None:
    """Resolve a manifest-relative path, refusing escapes."""
    if relative is None:
        return None
    base = manifest_path.parent.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise ManifestError(f"path escapes the manifest directory: {relative}")
    return target


def content_digest(root: Path) -> str:
    """Deterministic digest of a directory tree, used when no commit is pinned."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or "/.git/" in rel:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def materialize_checkout(
    view: GenerationView,
    manifest_path: Path,
    destination: Path,
    *,
    use_git: bool = True,
) -> Path:
    """Create a pristine checkout of the task's base state at ``destination``.

    * ``local_path`` tasks are copied (their identity is a content digest).
    * ``repo_url`` tasks are cloned and pinned to ``base_commit``.

    Never mutates the source checkout.
    """
    if destination.exists():
        raise RepositoryError(f"checkout destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if view.local_path is not None:
        source = resolve_in_manifest_dir(manifest_path, view.local_path)
        if source is None or not source.is_dir():
            raise RepositoryError(f"local_path not found for {view.task_id}: {view.local_path}")
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", ".pytest_cache", ".repotest-eval", "*.pyc"
            ),
        )
        return destination

    if not view.repo_url:
        raise RepositoryError(
            f"task {view.task_id} has neither local_path nor repo_url",
        )
    if not use_git:
        raise RepositoryError(
            f"task {view.task_id} requires cloning {view.repo_url}, which is disabled",
        )
    if not view.base_commit:
        raise RepositoryError(f"task {view.task_id} sets repo_url without base_commit")

    import subprocess  # local import: only clone paths need it

    destination.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["git", "clone", "--quiet", view.repo_url, str(destination)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if clone.returncode != 0:
        raise RepositoryError(
            f"git clone failed for {view.task_id}: {clone.stderr.strip()[:400]}"
        )
    checkout = subprocess.run(
        ["git", "-C", str(destination), "checkout", "--quiet", view.base_commit],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if checkout.returncode != 0:
        raise RepositoryError(
            f"base_commit {view.base_commit} not resolvable for {view.task_id}: "
            f"{checkout.stderr.strip()[:400]}"
        )
    return destination


def validate_task_resolvable(view: GenerationView, manifest_path: Path) -> str | None:
    """Return an exclusion reason, or ``None`` when the task can be used."""
    if view.exclusion_reason:
        return f"declared exclusion: {view.exclusion_reason}"
    if not view.test_command.strip():
        return "test_command is empty"
    if view.local_path is not None:
        source = resolve_in_manifest_dir(manifest_path, view.local_path)
        if source is None or not source.is_dir():
            return f"local_path does not exist: {view.local_path}"
        return None
    if not view.repo_url:
        return "neither local_path nor repo_url is set"
    if not view.base_commit:
        return "repo_url is set but base_commit is missing"
    return None


def select_tasks(
    manifest: TaskManifest,
    *,
    splits: Sequence[str] | None = None,
    task_ids: Sequence[str] | None = None,
) -> list[TaskEntry]:
    """Filter tasks by split and/or explicit id, preserving manifest order."""
    out: list[TaskEntry] = []
    for task in manifest.tasks:
        if splits is not None and task.split not in splits:
            continue
        if task_ids is not None and task.task_id not in task_ids:
            continue
        out.append(task)
    return out


def describe_manifest(manifest: TaskManifest, manifest_path: Path) -> dict[str, Any]:
    """Summary used by the `research` CLI command."""
    by_split: dict[str, int] = {}
    for task in manifest.tasks:
        by_split[task.split] = by_split.get(task.split, 0) + 1
    return {
        "path": str(manifest_path),
        "version": manifest.version,
        "task_count": len(manifest.tasks),
        "by_split": dict(sorted(by_split.items())),
        "with_gold": sum(1 for t in manifest.tasks if t.evaluation is not None),
        "splits_valid": set(by_split) <= set(SPLIT_VALUES),
    }
