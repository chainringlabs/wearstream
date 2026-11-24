"""Shared helpers for the delivery contract tests: they read the repository's own files."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


@pytest.fixture(scope="session")
def renovate() -> dict:
    return json.loads((ROOT / "renovate.json").read_text())


@pytest.fixture(scope="session")
def dockerfile() -> str:
    return (ROOT / "Dockerfile").read_text()


def load_workflow(name: str) -> dict:
    """Parse a workflow; PyYAML reads the `on:` key as the boolean True, so normalise it."""
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    if True in data:
        data["on"] = data.pop(True)
    return data


@pytest.fixture(scope="session")
def workflows() -> dict[str, dict]:
    return {path.name: load_workflow(path.name) for path in sorted(WORKFLOWS.glob("*.yml"))}
