"""scripts/check_sizes.py: file and function size limits."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_sizes.py"


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True
    )


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _function(name: str, body_lines: int) -> str:
    return f"def {name}():\n" + "    x = 1\n" * (body_lines - 1)


# @spec DELIVERY-CI-006
def test_passes_at_the_limits(tmp_path: Path) -> None:
    text = _function("f", 50) + "\n" * (300 - 50)
    for folder in ("src", "scripts", "tests"):
        _write(tmp_path, f"{folder}/ok.py", text)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout


# @spec DELIVERY-CI-006
def test_fails_on_a_file_over_300_physical_lines(tmp_path: Path) -> None:
    _write(tmp_path, "tests/long.py", "x = 1\n" * 301)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "tests/long.py" in result.stdout


# @spec DELIVERY-CI-006
def test_fails_on_a_function_over_50_lines_by_its_syntax_tree_span(tmp_path: Path) -> None:
    method = _function("m", 51).replace("\n", "\n    ")
    _write(tmp_path, "src/pkg/big.py", "class C:\n    " + method)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "src/pkg/big.py" in result.stdout
    assert "m" in result.stdout


# @spec DELIVERY-CI-006
def test_ignores_python_files_outside_the_checked_folders(tmp_path: Path) -> None:
    _write(tmp_path, "docs/example.py", "x = 1\n" * 400)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
