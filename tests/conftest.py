"""Shared fixtures for the RepoTest-Bridge test suite.

None of these fixtures execute repository test code; the only tests that do are
marked ``integration`` (see pyproject markers).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repotest_bridge.config import AppConfig, load_config
from repotest_bridge.repository import ProjectInfo, inspect_repository
from repotest_bridge.tasks import GenerationView, TaskEntry, generation_view, load_manifest

ROOT = Path(__file__).resolve().parents[1]
DEMO_MANIFEST = ROOT / "examples" / "demo_task" / "manifest.yaml"
DEMO_REPO = ROOT / "examples" / "demo_task" / "repo"
DEMO_FIXTURE = ROOT / "examples" / "demo_task" / "fixtures" / "mock_response.diff"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def config() -> AppConfig:
    """The shipped default configuration, loaded exactly as the CLI would."""
    return load_config(ROOT / "configs" / "default.yaml", environ={})


@pytest.fixture(scope="session")
def demo_manifest_path() -> Path:
    if not DEMO_MANIFEST.is_file():
        pytest.skip(f"demo manifest missing: {DEMO_MANIFEST}")
    return DEMO_MANIFEST


@pytest.fixture(scope="session")
def demo_manifest(demo_manifest_path: Path):
    manifest, path = load_manifest(demo_manifest_path)
    return manifest, path


@pytest.fixture(scope="session")
def demo_task(demo_manifest) -> TaskEntry:
    manifest, _ = demo_manifest
    return manifest.tasks[0]


@pytest.fixture(scope="session")
def demo_view(demo_task: TaskEntry) -> GenerationView:
    return generation_view(demo_task)


@pytest.fixture(scope="session")
def demo_project() -> ProjectInfo:
    if not DEMO_REPO.is_dir():
        pytest.skip(f"demo repo missing: {DEMO_REPO}")
    return inspect_repository(DEMO_REPO)


@pytest.fixture(scope="session")
def mock_patch_text() -> str:
    if not DEMO_FIXTURE.is_file():
        pytest.skip(f"mock fixture missing: {DEMO_FIXTURE}")
    return DEMO_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    """A minimal, hand-written checkout used by extractor/retriever tests.

    Layout is deliberately awkward: helpers and conftest live beside the tests,
    the package sits under ``src/``, and one unrelated file uses a different
    language so classification has something to reject.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'tiny'\nrequires-python = '>=3.11'\n", encoding="utf-8"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    src = tmp_path / "src" / "tinylib"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "billing.py").write_text(
        "def apply_tax(amount, rate):\n"
        "    if amount < 0:\n"
        "        raise ValueError('negative')\n"
        "    return amount + amount * rate\n"
        "\n"
        "def late_fee(days):\n"
        "    return 0.0 if days <= 0 else min(days * 0.05, 5.0)\n",
        encoding="utf-8",
    )
    (src / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "conftest.py").write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def invoice():\n"
        "    return {'total': 100.0}\n",
        encoding="utf-8",
    )
    (tests / "helpers.py").write_text(
        "def cents(value):\n    return round(value * 100)\n", encoding="utf-8"
    )
    (tests / "test_billing.py").write_text(
        "from tinylib.billing import apply_tax, late_fee\n"
        "from tests.helpers import cents\n\n\n"
        "def test_apply_tax(invoice):\n"
        "    assert cents(apply_tax(invoice['total'], 0.1)) == 11000\n\n\n"
        "def test_late_fee_zero_days():\n"
        "    assert late_fee(0) == 0.0\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# tiny\n", encoding="utf-8")
    return tmp_path
