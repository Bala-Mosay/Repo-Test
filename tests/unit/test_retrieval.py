"""Repository classification, lexical signals, and per-class extractors."""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.repository import (
    inspect_repository,
    iter_files,
    read_text,
)
from repotest_bridge.retrieval import make_extraction_context
from repotest_bridge.retrieval.extractors import (
    EXTRACTORS,
    ExtractionContext,
    _static_imports,
    run_extractor,
)
from repotest_bridge.retrieval.signals import (
    BM25Index,
    contains_entity,
    extract_issue_entities,
    split_identifier,
    tokenize,
)

ALL_CATEGORIES = {
    "source": "source",
    "related_test": "related_test",
    "fixture": "fixture",
    "conftest": "conftest",
    "helper": "helper",
    "import_edge": "import_edge",
    "project_config": "project_config",
}


# -- repository -------------------------------------------------------------


def test_demo_repo_classification(demo_project) -> None:
    assert demo_project.test_dirs == ("tests",)
    assert "tests/conftest.py" in demo_project.test_tree_files
    assert "tests/helpers.py" in demo_project.test_tree_files
    assert "tests/test_pricing.py" in demo_project.test_files
    assert "pricinglib/pricing.py" in demo_project.source_files
    assert "pytest.ini" in demo_project.config_files
    assert demo_project.test_command
    assert len(demo_project.digest) == 64


def test_small_repo_classification(small_repo: Path) -> None:
    project = inspect_repository(small_repo)
    assert project.language == "python"
    assert "src/tinylib/billing.py" in project.source_files
    assert "tests/test_billing.py" in project.test_files
    assert "tests/helpers.py" in project.test_tree_files
    assert "tests/helpers.py" not in project.test_files
    assert "README.md" not in project.source_files


def test_test_dirs_detection(tmp_path: Path) -> None:
    (tmp_path / "testing").mkdir()
    (tmp_path / "testing" / "test_x.py").write_text("def test_x(): pass\n")
    project = inspect_repository(tmp_path)
    assert "testing" in project.test_dirs
    assert "testing/test_x.py" in project.test_files
    assert project.language == "python"


def test_iter_files_skips_caches_and_hidden_dirs(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("A = 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"\x00")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"\x00")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_files(tmp_path)}
    assert "pkg/a.py" in found
    assert not any(f.startswith(".git/") or "__pycache__" in f for f in found)


def test_read_text_respects_byte_cap(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * 500)
    assert len(read_text(big, max_bytes=100) or "") <= 100
    assert read_text(tmp_path / "missing.py") is None
    assert read_text(big) == "x" * 500


# -- signals ----------------------------------------------------------------

def test_tokenize_splits_identifiers() -> None:
    tokens = tokenize("discount_rate applies bulk")
    assert "discount_rate" in tokens
    assert "discount" in tokens and "rate" in tokens
    assert tokenize("") == []


def test_split_identifier_handles_camel_and_snake() -> None:
    assert split_identifier("applyTaxAmount") == ["apply", "tax", "amount"]
    assert split_identifier("bulk_discount") == ["bulk", "discount"]


def test_issue_entities_include_code_identifiers() -> None:
    entities = extract_issue_entities(
        "discount_rate() returns the wrong value at quantity 20.", limit=5
    )
    assert "discount_rate" in entities
    assert len(entities) <= 5


def test_contains_entity_is_substring_aware() -> None:
    assert contains_entity("def discount_rate(qty):", "discount_rate")
    assert not contains_entity("def other(x):", "discount_rate")


def test_bm25_ranks_matching_document_first() -> None:
    index = BM25Index()
    index.build(
        {
            "pricing.py": "discount rate applies to bulk quantity threshold",
            "unrelated.py": "nothing relevant here at all",
        }
    )
    ranked = index.rank(tokenize("bulk quantity discount threshold"))
    assert ranked
    assert ranked[0][0] == "pricing.py"
    assert all(score > 0 for _, score in ranked)


# -- extractors -------------------------------------------------------------


def test_every_extractor_is_registered() -> None:
    assert set(EXTRACTORS) == set(ALL_CATEGORIES)


def test_extractor_categories_are_fixed(demo_view, demo_project) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    for name, expected in ALL_CATEGORIES.items():
        items = run_extractor(name, ctx)
        if name == "import_edge":
            # Computed; may legitimately be empty if no static edge exists.
            continue
        assert items, f"extractor {name!r} produced nothing for the demo task"
        assert {i.category.value for i in items} == {expected}


def test_extractors_only_emit_their_own_class(demo_view, demo_project) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    for name in EXTRACTORS:
        for item in run_extractor(name, ctx):
            assert item.category.value == ALL_CATEGORIES[name], (
                f"{name} emitted {item.category.value}"
            )


def test_every_item_path_stays_inside_the_checkout(
    demo_view, demo_project
) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    root = demo_project.root.resolve()
    for name in EXTRACTORS:
        for item in run_extractor(name, ctx):
            if item.path.startswith("<"):  # synthetic, e.g. <import-edges.md>
                continue
            resolved = (root / item.path).resolve()
            assert resolved.is_relative_to(root), item.path
            assert resolved.is_file(), item.path


def test_conftest_and_helper_are_separate_classes(
    demo_view, demo_project
) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    conftests = run_extractor("conftest", ctx)
    helpers = run_extractor("helper", ctx)
    assert [i.path for i in conftests] == ["tests/conftest.py"]
    assert "tests/helpers.py" in [i.path for i in helpers]
    assert "tests/conftest.py" not in [i.path for i in helpers]


def test_fixture_extractor_finds_pytest_fixtures(demo_view, demo_project) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    fixtures = run_extractor("fixture", ctx)
    assert fixtures, "demo conftest declares fixtures"
    assert all("@pytest.fixture" in i.content for i in fixtures)


def test_fixture_extractor_skips_non_fixture_modules(
    demo_view, demo_project
) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    paths = {i.path for i in run_extractor("fixture", ctx)}
    assert "pricinglib/pricing.py" not in paths


def test_project_config_prefers_runner_files(demo_view, demo_project) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    paths = [i.path for i in run_extractor("project_config", ctx)]
    assert "pytest.ini" in paths
    pytest_ini = next(i for i in run_extractor("project_config", ctx) if i.path == "pytest.ini")
    assert pytest_ini.score >= 8.0


def test_static_imports_never_execute_code() -> None:
    source = (
        "import os\n"
        "from pathlib import Path\n"
        "raise SystemExit('this must not run')\n"
        "import definitely_missing_module\n"
    )
    assert _static_imports(source) == {"os", "pathlib", "definitely_missing_module"}


def test_static_imports_survive_syntax_errors() -> None:
    assert _static_imports("def broken(:\n    pass\n") == set()


def test_unknown_extractor_is_rejected(demo_view, demo_project) -> None:
    ctx = make_extraction_context(demo_view, demo_project)
    with pytest.raises(KeyError, match="unknown extractor"):
        run_extractor("gold_patch", ctx)


def test_broken_extractor_does_not_abort_the_run(demo_view, demo_project) -> None:
    import repotest_bridge.retrieval.extractors as extractors

    def boom(_ctx: ExtractionContext):
        raise RuntimeError("kaboom")

    original = extractors.EXTRACTORS["source"]
    extractors.EXTRACTORS["source"] = boom  # type: ignore[assignment]
    try:
        assert run_extractor("source", make_extraction_context(demo_view, demo_project)) == []
    finally:
        extractors.EXTRACTORS["source"] = original  # type: ignore[assignment]
