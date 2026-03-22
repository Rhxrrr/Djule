from __future__ import annotations

from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent
EXAMPLES = REPO_ROOT / "examples"
FIXTURES = TESTS_ROOT / "fixtures"


def example_path(filename: str) -> Path:
    fixture = FIXTURES / filename
    if fixture.exists():
        return fixture
    return EXAMPLES / filename
