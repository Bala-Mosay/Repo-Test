"""One extractor per artifact class.

Each extractor is a pure function of ``(repository, issue) -> list[ContextItem]``
and is registered by name so strategies compose from them declaratively.
No extractor reads anything outside the checkout, and none sees gold data.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from repotest_bridge.repository import ProjectInfo, iter_files, read_text
from repotest_bridge.retrieval.budget import estimate_tokens
from repotest_bridge.retrieval.signals import BM25Index, contains_entity, tokenize
from repotest_bridge.retrieval.types import ArtifactCategory, ContextItem

MAX_FILE_BYTES = 120_000


@dataclass
class ExtractionContext:
    """Everything an extractor is allowed to look at."""

    root: Path
    project: ProjectInfo
    issue_text: str
    issue_entities: list[str]
    seed: int = 0
    index: BM25Index | None = None
    _docs: dict[str, str] | None = field(default=None, repr=False)
    _module_index: dict[str, str] | None = field(default=None, repr=False)
    _test_imports: dict[str, set[str]] | None = field(default=None, repr=False)

    # -- lazy indices -----------------------------------------------------

    def docs(self) -> dict[str, str]:
        """path -> text for every source, test, config file in the checkout."""
        if self._docs is None:
            docs: dict[str, str] = {}
            wanted = (
                *self.project.source_files,
                *self.project.test_files,
                *self.project.test_tree_files,
                *self.project.config_files,
            )
            for rel in dict.fromkeys(wanted):
                text = read_text(self.root / rel, max_bytes=MAX_FILE_BYTES)
                if text:
                    docs[rel] = text
            self._docs = docs
            self.index = BM25Index()
            self.index.build(docs)
        return self._docs

    def rank(self) -> list[tuple[str, float]]:
        if self.index is None:
            self.docs()
        assert self.index is not None
        return self.index.rank(tokenize(self.issue_text))

    def module_index(self) -> dict[str, str]:
        """Dotted module name -> repo-relative file path."""
        if self._module_index is None:
            index: dict[str, str] = {}
            for rel in (*self.project.source_files, *self.project.test_files):
                if not rel.endswith(".py"):
                    continue
                no_suffix = rel[:-3]
                parts = no_suffix.split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                index[".".join(parts)] = rel
            self._module_index = index
        return self._module_index

    def test_imports(self) -> dict[str, set[str]]:
        """Test file -> set of dotted modules it (statically) imports."""
        if self._test_imports is None:
            out: dict[str, set[str]] = {}
            for rel in self.project.test_files:
                if not rel.endswith(".py"):
                    continue
                text = read_text(self.root / rel, max_bytes=MAX_FILE_BYTES)
                if not text:
                    continue
                out[rel] = _static_imports(text)
            self._test_imports = out
        return self._test_imports


def _static_imports(source: str) -> set[str]:
    """Top-level module names imported by a Python file; never executes code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _package_of(rel_path: str) -> str | None:
    parts = rel_path.split("/")
    if len(parts) < 2:
        return None
    if (parts[0] == "src" or parts[0] == "lib") and len(parts) >= 3:
        return parts[1]
    return parts[0]


def _item(
    category: ArtifactCategory,
    root: Path,
    rel: str,
    *,
    score: float,
    rationale: str,
    content: str | None = None,
    max_bytes: int = MAX_FILE_BYTES,
) -> ContextItem | None:
    text = content if content is not None else read_text(root / rel, max_bytes=max_bytes)
    if text is None or not text.strip():
        return None
    clipped = text
    if len(clipped.encode("utf-8")) > max_bytes:
        clipped = clipped[: max_bytes * 3 // 4].rstrip() + "\n# ... truncated by RepoTest-Bridge\n"
    return ContextItem(
        category=category,
        path=rel,
        content=clipped,
        score=score,
        rationale=rationale,
        sha256=ContextItem.hash_content(clipped),
        tokens=estimate_tokens(clipped),
    )


def _entity_hits(text: str, entities: Iterable[str]) -> list[str]:
    return [e for e in entities if contains_entity(text, e)]


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def extract_source(ctx: ExtractionContext) -> list[ContextItem]:
    """Source files most relevant to the issue text."""
    ranked = ctx.rank()
    wanted = set(ctx.project.source_files)
    out: list[ContextItem] = []
    for rel, score in ranked:
        if rel not in wanted:
            continue
        hits = _entity_hits((ctx.docs() or {}).get(rel, ""), ctx.issue_entities[:20])
        item = _item(
            ArtifactCategory.SOURCE,
            ctx.root,
            rel,
            score=score,
            rationale=f"bm25={score:.2f}"
            + (f"; mentions {', '.join(hits[:5])}" if hits else ""),
        )
        if item:
            out.append(item)
    # Files that contain an issue entity but ranked outside the BM25 top list.
    already = {i.path for i in out}
    for rel in ctx.project.source_files:
        if rel in already:
            continue
        text = read_text(ctx.root / rel, max_bytes=MAX_FILE_BYTES)
        if not text:
            continue
        hits = _entity_hits(text, ctx.issue_entities[:20])
        if hits:
            out.append(
                ContextItem(
                    category=ArtifactCategory.SOURCE,
                    path=rel,
                    content=text,
                    score=0.01 * len(hits),
                    rationale=f"issue entities {', '.join(hits[:5])}",
                    sha256=ContextItem.hash_content(text),
                    tokens=estimate_tokens(text),
                )
            )
    return out


def extract_related_tests(ctx: ExtractionContext) -> list[ContextItem]:
    """Test files that exercise code named in the issue.

    This is the class prior work treats as the whole of repository context;
    here it is one of six independent classes.
    """
    ranked = dict(ctx.rank())
    import_map = ctx.test_imports()
    module_index = ctx.module_index()
    ranked_sources = [rel for rel, _ in ctx.rank() if rel in set(ctx.project.source_files)]

    out: list[ContextItem] = []
    for rel in ctx.project.test_files:
        text = read_text(ctx.root / rel, max_bytes=MAX_FILE_BYTES)
        if not text:
            continue
        hits = _entity_hits(text, ctx.issue_entities[:20])
        bm25 = ranked.get(rel, 0.0)
        import_bonus = 0.0
        for imported in import_map.get(rel, set()):
            target = module_index.get(imported)
            if target and ranked_sources and target in ranked_sources[:5]:
                import_bonus = 0.5
                break
        score = bm25 + 1.5 * min(len(hits), 5) + import_bonus
        if score <= 0:
            continue
        out.append(
            ContextItem(
                category=ArtifactCategory.RELATED_TEST,
                path=rel,
                content=text,
                score=score,
                rationale=(
                    f"score={score:.2f}"
                    + (f"; mentions {', '.join(hits[:5])}" if hits else "")
                    + ("; imports top-ranked source" if import_bonus else "")
                ),
                sha256=ContextItem.hash_content(text),
                tokens=estimate_tokens(text),
            )
        )
    return out


def extract_import_edges(ctx: ExtractionContext) -> list[ContextItem]:
    """A static import edge list: which tests import the source of interest."""
    module_index = ctx.module_index()
    import_map = ctx.test_imports()
    ranked_sources = [rel for rel, _ in ctx.rank() if rel in set(ctx.project.source_files)]
    focus = ranked_sources[:8] or list(ctx.project.source_files)[:8]
    focus_modules = {
        rel: {name for name, path in module_index.items() if path == rel} for rel in focus
    }

    lines: list[str] = ["# static import edges (issue-relevant source -> tests)", ""]
    any_edge = False
    for src_rel in focus:
        names = focus_modules.get(src_rel, set())
        edges = sorted(
            test_rel
            for test_rel, mods in import_map.items()
            if names & mods
            or (_package_of(src_rel) or "") in mods
            or any(n in mods for n in names)
        )
        if not edges:
            lines.append(f"{src_rel} -> (no static importer found)")
            continue
        any_edge = True
        lines.append(f"{src_rel} ->")
        for test_rel in edges:
            lines.append(f"  {test_rel}")
    if not any_edge:
        return []
    body = "\n".join(lines)
    return [
        ContextItem(
            category=ArtifactCategory.IMPORT_EDGE,
            path="<import-edges.md>",
            content=body,
            score=float(len(lines)),
            rationale="AST-derived import edges; computed, never executed",
            sha256=ContextItem.hash_content(body),
            tokens=estimate_tokens(body),
        )
    ]


def _fixture_functions(text: str) -> list[str]:
    """Return the source slices of ``@pytest.fixture`` functions."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    out: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = []
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            decorators.append(ast.unparse(target) if _HAS_UNPARSE else "")
        if not any("fixture" in d for d in decorators):
            continue
        start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
        dec_ends = [getattr(d, "end_lineno", d.lineno) for d in node.decorator_list]
        end = max([node.end_lineno or node.lineno, *dec_ends])
        out.append("\n".join(lines[start:end]))
    return out


_HAS_UNPARSE = hasattr(ast, "unparse")


def _test_tree_files(ctx: ExtractionContext) -> list[str]:
    """All Python files inside the test tree: tests, conftest, fixtures, helpers."""
    files = list(ctx.project.test_tree_files)
    for rel in ctx.project.source_files:
        if rel.split("/")[-1].startswith("conftest"):
            files.append(rel)
    return sorted(set(files))


def extract_fixtures(ctx: ExtractionContext) -> list[ContextItem]:
    """``@pytest.fixture`` definitions discovered by AST scan."""
    out: list[ContextItem] = []
    for rel in _test_tree_files(ctx):
        text = read_text(ctx.root / rel, max_bytes=MAX_FILE_BYTES)
        if not text or "fixture" not in text:
            continue
        for body in _fixture_functions(text):
            if not body.strip():
                continue
            score = 2.0
            if _entity_hits(body, ctx.issue_entities[:20]):
                score += 3.0
            out.append(
                ContextItem(
                    category=ArtifactCategory.FIXTURE,
                    path=rel,
                    content=body,
                    score=score,
                    rationale="pytest fixture definition" + (
                        "; mentions issue entity" if score > 2 else ""
                    ),
                    sha256=ContextItem.hash_content(body),
                    tokens=estimate_tokens(body),
                )
            )
    return out


def extract_conftest(ctx: ExtractionContext) -> list[ContextItem]:
    """``conftest.py`` files from the test directory upward."""
    out: list[ContextItem] = []
    for rel in _test_tree_files(ctx):
        if rel.split("/")[-1] != "conftest.py":
            continue
        item = _item(
            ArtifactCategory.CONFTEST,
            ctx.root,
            rel,
            score=10.0,
            rationale="pytest conftest: hooks, fixtures, plugins",
        )
        if item:
            out.append(item)
    # Root-level conftest is not always under a detected test dir.
    if (ctx.root / "conftest.py").exists():
        item = _item(
            ArtifactCategory.CONFTEST,
            ctx.root,
            "conftest.py",
            score=9.0,
            rationale="root conftest",
        )
        if item and not any(i.path == "conftest.py" for i in out):
            out.append(item)
    return out


def extract_helpers(ctx: ExtractionContext) -> list[ContextItem]:
    """Non-test support modules that live inside the test tree."""
    test_names = set(ctx.project.test_files)
    out: list[ContextItem] = []
    for rel in ctx.project.test_tree_files:
        if rel in test_names:
            continue
        name = rel.split("/")[-1]
        if name == "conftest.py":
            continue
        text = read_text(ctx.root / rel, max_bytes=MAX_FILE_BYTES)
        if not text:
            continue
        score = 5.0 + min(len(_entity_hits(text, ctx.issue_entities[:20])), 5)
        out.append(
            ContextItem(
                category=ArtifactCategory.HELPER,
                path=rel,
                content=text,
                score=score,
                rationale="test-tree support module",
                sha256=ContextItem.hash_content(text),
                tokens=estimate_tokens(text),
            )
        )
    return out


def extract_project_config(ctx: ExtractionContext) -> list[ContextItem]:
    """Runner configuration: how tests are collected and executed."""
    out: list[ContextItem] = []
    for rel in ctx.project.config_files:
        if "/" in rel:
            score = 1.0  # CI workflow
        else:
            score = 8.0 if rel in {"pytest.ini", "pyproject.toml", "setup.cfg"} else 4.0
        item = _item(
            ArtifactCategory.PROJECT_CONFIG,
            ctx.root,
            rel,
            score=score,
            rationale="test runner / project configuration",
        )
        if item:
            out.append(item)
    return out


def extract_random_control(
    ctx: ExtractionContext, *, target_tokens: int, seed: int
) -> list[ContextItem]:
    """Token-matched negative control.

    Draws files uniformly at random from the same candidate pool the repo-aware
    strategy uses, ignoring the issue entirely. Seed is derived from
    ``(task, seed)`` so the draw is reproducible and comparable across arms.
    """
    import random

    # Exactly the pool `repo_aware` draws from -- source, test, test-tree
    # (conftest/helpers) and config files. A narrower pool starves the control
    # on small repositories and leaves it permanently under its matched target,
    # which would confound H4 (a token-matched control that never matches).
    docs = ctx.docs()
    pool = sorted(docs)
    if not pool:
        return []
    rng = random.Random(f"{seed}:{ctx.project.digest[:16]}")
    rng.shuffle(pool)

    out: list[ContextItem] = []
    spent = 0
    for rel in pool:
        text = docs[rel]
        if not text:
            continue
        cost = estimate_tokens(text)
        if spent + cost > target_tokens:
            continue
        out.append(
            ContextItem(
                category=ArtifactCategory.RANDOM_CONTROL,
                path=rel,
                content=text,
                score=0.0,
                rationale="uniform draw, issue-agnostic (negative control)",
                sha256=ContextItem.hash_content(text),
                tokens=cost,
            )
        )
        spent += cost
        if spent >= target_tokens * 0.9:
            break
    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

Extractor = Callable[[ExtractionContext], list[ContextItem]]

EXTRACTORS: dict[str, Extractor] = {
    "source": extract_source,
    "related_test": extract_related_tests,
    "fixture": extract_fixtures,
    "conftest": extract_conftest,
    "helper": extract_helpers,
    "import_edge": extract_import_edges,
    "project_config": extract_project_config,
}


def run_extractor(name: str, ctx: ExtractionContext) -> list[ContextItem]:
    if name not in EXTRACTORS:
        raise KeyError(f"unknown extractor: {name!r} (known: {sorted(EXTRACTORS)})")
    try:
        return EXTRACTORS[name](ctx)
    except Exception as exc:  # extractors must never abort the run
        print(f"[repotest] extractor {name!r} failed, continuing: {exc}")
        return []


def iter_files_under(root: Path) -> list[Path]:  # re-export for convenience
    return iter_files(root)
