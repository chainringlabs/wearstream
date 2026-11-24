"""Repository layout, toolchain and dependency-update contract (DELIVERY-REPO, -REL, -DEPS)."""

from __future__ import annotations

import re
from pathlib import Path


# @spec DELIVERY-REPO-001
def test_dependencies_are_declared_only_in_pyproject_and_the_lock(root: Path) -> None:
    assert (root / "pyproject.toml").is_file()
    assert (root / "uv.lock").is_file()
    assert sorted(p.name for p in root.glob("requirements*.txt")) == []


# @spec DELIVERY-REPO-002
def test_python_is_declared_once_as_a_major_minor_floor(root: Path, pyproject: dict) -> None:
    assert re.fullmatch(r">=3\.\d+", pyproject["project"]["requires-python"])
    assert not (root / ".python-version").exists()


# @spec DELIVERY-REPO-003
def test_uv_cooldown_is_seven_days_with_garminconnect_exempt(pyproject: dict) -> None:
    uv = pyproject["tool"]["uv"]
    assert uv["exclude-newer"] == "7 days"
    assert uv["exclude-newer-package"] == {"garminconnect": False}


# @spec DELIVERY-REPO-004
def test_layout_and_console_script(root: Path, pyproject: dict) -> None:
    for folder in ("src/wearstream", "tests", "scripts", "docs", ".claude"):
        assert (root / folder).is_dir(), folder
    assert pyproject["project"]["scripts"] == {"wearstream": "wearstream.cli:entry"}


# @spec DELIVERY-REPO-005
def test_dockerignore_covers_gitignore_plus_the_build_extras(root: Path) -> None:
    def entries(name: str) -> set[str]:
        lines = (root / name).read_text().splitlines()
        return {line.strip() for line in lines if line.strip() and not line.startswith("#")}

    dockerignore = entries(".dockerignore")
    assert entries(".gitignore") <= dockerignore
    assert {".git/", "docs/", "tests/", "scripts/", ".claude/"} <= dockerignore


# @spec DELIVERY-REL-002
def test_version_is_a_release_or_the_next_dev0(pyproject: dict) -> None:
    """`X.Y.Z.dev0` between releases; plain `X.Y.Z` only in a release's own pull request."""
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev0)?", pyproject["project"]["version"])


# @spec DELIVERY-DEPS-001
def test_renovate_extends_the_organization_preset(renovate: dict) -> None:
    assert renovate["extends"] == ["github>chainringlabs/renovate-config"]


def _rule_for(renovate: dict, name: str) -> dict:
    for rule in renovate.get("packageRules", []):
        if name in rule.get("matchDepNames", []) + rule.get("matchPackageNames", []):
            return rule
    raise AssertionError(f"no package rule for {name}")


# @spec DELIVERY-DEPS-002
def test_chainguard_builder_and_runtime_update_together(renovate: dict) -> None:
    rule = _rule_for(renovate, "cgr.dev/chainguard/python")
    assert rule.get("groupName")


# @spec DELIVERY-DEPS-003
def test_garminconnect_is_exempt_grouped_alone_and_never_automerged(renovate: dict) -> None:
    rule = _rule_for(renovate, "garminconnect")
    assert rule["minimumReleaseAge"] is None
    assert rule["automerge"] is False
    assert rule.get("groupName")
    assert rule.get("matchPackageNames", rule.get("matchDepNames")) == ["garminconnect"]
