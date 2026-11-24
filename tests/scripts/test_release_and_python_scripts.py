"""scripts/python_version.py and scripts/check_release.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _pyproject(root: Path, version: str, requires: str = ">=3.15") -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\nrequires-python = "{requires}"\n'
    )


def _script(name: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args], capture_output=True, text=True, cwd=cwd
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(root: Path, version: str) -> str:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _pyproject(root, version)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "c")
    return _git(root, "rev-parse", "HEAD")


# @spec DELIVERY-CI-011
def test_python_version_prints_the_major_minor_floor(tmp_path: Path) -> None:
    _pyproject(tmp_path, "1.0.0", ">=3.15")
    result = _script("python_version.py", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "3.15"


# @spec DELIVERY-CI-011
def test_python_version_refuses_a_floor_that_is_not_major_minor(tmp_path: Path) -> None:
    _pyproject(tmp_path, "1.0.0", ">=3.15.2")
    assert _script("python_version.py", cwd=tmp_path).returncode == 1


# @spec DELIVERY-REL-001
def test_release_check_passes_without_a_tag(tmp_path: Path) -> None:
    _repo(tmp_path, "1.2.0.dev0")
    result = _script("check_release.py", "--ref", "refs/heads/main", cwd=tmp_path)
    assert result.returncode == 0, result.stdout


# @spec DELIVERY-REL-001
def test_release_check_passes_a_matching_tag_on_main(tmp_path: Path) -> None:
    _repo(tmp_path, "1.2.0")
    _git(tmp_path, "tag", "v1.2.0")
    result = _script("check_release.py", "--ref", "refs/tags/v1.2.0", cwd=tmp_path)
    assert result.returncode == 0, result.stdout


# @spec DELIVERY-REL-001
def test_release_check_refuses_a_tag_that_differs_from_the_version(tmp_path: Path) -> None:
    _repo(tmp_path, "1.2.0")
    _git(tmp_path, "tag", "v1.3.0")
    result = _script("check_release.py", "--ref", "refs/tags/v1.3.0", cwd=tmp_path)
    assert result.returncode == 1
    assert "1.2.0" in result.stdout


# @spec DELIVERY-REL-001
def test_release_check_refuses_a_pre_release_version(tmp_path: Path) -> None:
    _repo(tmp_path, "1.2.0.dev0")
    _git(tmp_path, "tag", "v1.2.0.dev0")
    result = _script("check_release.py", "--ref", "refs/tags/v1.2.0.dev0", cwd=tmp_path)
    assert result.returncode == 1


# @spec DELIVERY-REL-001
def test_release_check_refuses_a_tag_off_main(tmp_path: Path) -> None:
    _repo(tmp_path, "1.2.0.dev0")
    _git(tmp_path, "checkout", "-q", "-b", "side")
    _pyproject(tmp_path, "1.2.0")
    _git(tmp_path, "commit", "-qam", "side")
    _git(tmp_path, "tag", "v1.2.0")
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "checkout", "-q", "v1.2.0")
    result = _script("check_release.py", "--ref", "refs/tags/v1.2.0", cwd=tmp_path)
    assert result.returncode == 1
    assert "main" in result.stdout
