"""scripts/check_specs.py: every @spec citation resolves, and no spec ID repeats."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_specs.py"
# Built at run time so this file holds no citations the real check would read.
AT = "@" + "spec"


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True
    )


def _tree(tmp_path: Path, specs: str, files: dict[str, str]) -> Path:
    spec_file = tmp_path / "docs" / "intent" / "demo" / "demo-specs.md"
    spec_file.parent.mkdir(parents=True)
    spec_file.write_text(specs)
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


SPECS = "- [ ] **DEMO-001**: one.\n- [x] **DEMO-X-002**: two.\n- [D] **DEMO-003**: three.\n"


# @spec DELIVERY-CI-005
def test_passes_when_every_citation_exists(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        SPECS,
        {
            "src/pkg/a.py": f"# {AT} DEMO-001, DEMO-X-002\n",
            "tests/test_a.py": f"# {AT} DEMO-003\n",
            "Dockerfile": f"# {AT} DEMO-001\n",
            ".github/workflows/x.yml": f"# {AT} DEMO-X-002\n",
            "scripts/s.py": f"# {AT} DEMO-001\n",
        },
    )
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr


# @spec DELIVERY-CI-005
def test_fails_on_a_citation_that_does_not_exist(tmp_path: Path) -> None:
    root = _tree(tmp_path, SPECS, {"src/pkg/a.py": f"# {AT} DEMO-001, DEMO-404\n"})
    result = _run(root)
    assert result.returncode == 1
    assert "DEMO-404" in result.stdout
    assert "src/pkg/a.py" in result.stdout


# @spec DELIVERY-CI-005
def test_fails_when_a_spec_file_repeats_an_id(tmp_path: Path) -> None:
    root = _tree(tmp_path, SPECS + "- [ ] **DEMO-001**: again.\n", {})
    result = _run(root)
    assert result.returncode == 1
    assert "DEMO-001" in result.stdout


# @spec DELIVERY-CI-005
def test_ignores_citations_outside_the_scanned_places(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        SPECS,
        {"AGENTS.md": f"# {AT} NOPE-001\n", "docs/notes.md": f"# {AT} NOPE-002\n"},
    )
    result = _run(root)
    assert result.returncode == 0, result.stdout
