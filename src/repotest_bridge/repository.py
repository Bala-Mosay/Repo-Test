"""Read-only repository inspection.

Nothing in this module writes to, or rewrites, a checkout. Its only side effect
is reading files to classify them.
"""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "dist",
        "build",
        "*.egg-info",
        ".eggs",
        "htmlcov",
        ".repotest-eval",
        "site-packages",
    }
)

SOURCE_EXTENSIONS = frozenset({".py", ".pyi"})
TEST_FILE_GLOBS = ("test_*.py", "*_test.py", "*Tests.py")
CONFIG_FILENAMES = (
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "conftest.py",
    "Makefile",
    ".coveragerc",
    "noxfile.py",
)
TEST_DIR_NAMES = ("tests", "test", "testing", "spec", "__tests__")
SOURCE_HINT_DIRS = ("src", "lib", "app", "pkg", "python")

CI_DIR_NAMES = (".github/workflows", ".gitlab", ".circleci")


@dataclass(frozen=True)
class ProjectInfo:
    """Classification of a checkout, used by the retriever and the prompt."""

    root: Path
    language: str
    test_framework: str
    test_dirs: tuple[str, ...]
    source_dirs: tuple[str, ...]
    config_files: tuple[str, ...]
    ci_files: tuple[str, ...]
    test_files: tuple[str, ...]
    test_tree_files: tuple[str, ...]
    source_files: tuple[str, ...]
    test_command: str
    digest: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def _is_ignored(name: str) -> bool:
    if name in IGNORED_DIR_NAMES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORED_DIR_NAMES)


def iter_files(root: Path, *, max_files: int = 50_000) -> list[Path]:
    """List files under ``root`` skipping caches, VCS metadata and build output."""
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            continue
        for entry in entries:
            if entry.is_dir():
                if not _is_ignored(entry.name) and not entry.is_symlink():
                    stack.append(entry)
            elif entry.is_file():
                out.append(entry)
            if len(out) >= max_files:
                return out
    return out


def _has_test_name(path: Path) -> bool:
    name = path.name
    return any(fnmatch.fnmatch(name, pattern) for pattern in TEST_FILE_GLOBS)


def _under_any(path: Path, root: Path, dirs: tuple[str, ...]) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - defensive
        return False
    return any(rel == d or rel.startswith(f"{d}/") for d in dirs)


def detect_language(root: Path) -> str:
    markers = {
        "python": ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini"),
        "javascript": ("package.json",),
        "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
        "go": ("go.mod",),
        "rust": ("Cargo.toml",),
    }
    for language, files in markers.items():
        if any((root / f).exists() for f in files):
            return language
    if any(p.suffix == ".py" for p in iter_files(root)[:500]):
        return "python"
    return "unknown"


def detect_test_framework(root: Path, language: str) -> str:
    if language == "python":
        if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
            return "pytest"
        if (root / "tox.ini").exists():
            return "pytest"
        return "unittest"
    if language == "java":
        return "junit"
    if language == "go":
        return "go test"
    if language == "javascript":
        return "jest"
    return "unknown"


def detect_test_dirs(root: Path, test_files: list[Path]) -> tuple[str, ...]:
    found: set[str] = set()
    for path in test_files:
        parts = path.relative_to(root).parts
        for index, part in enumerate(parts[:-1]):
            if part in TEST_DIR_NAMES:
                found.add("/".join(parts[: index + 1]))
    return tuple(sorted(found))


def detect_source_dirs(
    root: Path, test_dirs: tuple[str, ...], files: list[Path]
) -> tuple[str, ...]:
    found: set[str] = set()
    for path in files:
        if path.suffix not in SOURCE_EXTENSIONS or _has_test_name(path):
            continue
        if _under_any(path, root, test_dirs):
            continue
        parts = path.relative_to(root).parts
        if len(parts) > 1:
            found.add(parts[0])
        else:
            found.add(".")
    hinted = [d for d in SOURCE_HINT_DIRS if (root / d).is_dir()]
    return tuple(sorted(set(hinted) | found))


def detect_config_files(root: Path, files: list[Path]) -> tuple[str, ...]:
    found: list[str] = []
    for name in CONFIG_FILENAMES:
        if (root / name).exists():
            found.append(name)
    for path in files:
        rel = path.relative_to(root).as_posix()
        if any(rel.startswith(f"{d}/") for d in CI_DIR_NAMES) and path.suffix in {
            ".yml",
            ".yaml",
        }:
            found.append(rel)
    return tuple(dict.fromkeys(found))


def detect_test_command(root: Path, language: str, framework: str) -> str:
    if language == "python":
        return "python -m pytest -q"
    if language == "java":
        return "mvn -q test"
    if language == "go":
        return "go test ./..."
    if language == "javascript":
        return "npm test"
    if framework:
        return f"{framework} --help"
    return ""


def inspect_repository(root: Path) -> ProjectInfo:
    """Classify a checkout. ``root`` must already exist."""
    resolved = root.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"not a directory: {resolved}")

    files = iter_files(resolved)
    language = detect_language(resolved)
    framework = detect_test_framework(resolved, language)

    candidates = [
        p for p in files if p.suffix in SOURCE_EXTENSIONS and _has_test_name(p)
    ]
    test_dirs = detect_test_dirs(resolved, candidates)
    # Every Python file inside the test tree, whether or not it is named test_*:
    # conftest.py, fixtures and helpers live here too.
    test_tree = [
        p
        for p in files
        if p.suffix in SOURCE_EXTENSIONS and _under_any(p, resolved, test_dirs)
    ]
    test_files = [p for p in test_tree if _has_test_name(p)]
    source_files = [
        p
        for p in files
        if p.suffix in SOURCE_EXTENSIONS
        and not _under_any(p, resolved, test_dirs)
        and not _has_test_name(p)
    ]
    source_dirs = detect_source_dirs(resolved, test_dirs, files)
    config_files = detect_config_files(resolved, files)

    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(resolved).as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:  # pragma: no cover - unreadable file
            digest.update(b"<unreadable>")
        digest.update(b"\0")

    notes: list[str] = []
    if not test_files:
        notes.append("no test files detected")
    if language == "unknown":
        notes.append("language could not be determined from project markers")

    return ProjectInfo(
        root=resolved,
        language=language,
        test_framework=framework,
        test_dirs=test_dirs,
        source_dirs=source_dirs,
        config_files=config_files,
        ci_files=tuple(f for f in config_files if "/" in f),
        test_files=tuple(p.relative_to(resolved).as_posix() for p in sorted(test_files)),
        test_tree_files=tuple(p.relative_to(resolved).as_posix() for p in sorted(test_tree)),
        source_files=tuple(p.relative_to(resolved).as_posix() for p in sorted(source_files)),
        test_command=detect_test_command(resolved, language, framework),
        digest=digest.hexdigest(),
        notes=tuple(notes),
    )


def read_text(path: Path, *, max_bytes: int = 200_000) -> str | None:
    """Read a file as text, returning ``None`` when it is binary or too large."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > max_bytes:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")
