"""The CI contract (DELIVERY-CI, DELIVERY-REL-001), read from .github/workflows."""

from __future__ import annotations

import re
from pathlib import Path

WRITE_PERMISSIONS = {"packages", "id-token", "attestations", "artifact-metadata"}
SHARED = re.compile(r"chainringlabs/ci/\.github/workflows/([\w-]+\.yml)@([0-9a-f]+)(.*)$")


def _triggers(workflow: dict) -> dict:
    on = workflow["on"]
    return on if isinstance(on, dict) else {name: None for name in on}


def _runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


# @spec DELIVERY-CI-001
def test_permissions_are_empty_at_top_and_pull_requests_never_get_write(workflows: dict) -> None:
    assert workflows, "no workflows found"
    for name, workflow in workflows.items():
        assert workflow.get("permissions") == {}, name
        if "pull_request" in _triggers(workflow):
            for job_name, job in workflow["jobs"].items():
                granted = {k for k, v in (job.get("permissions") or {}).items() if v == "write"}
                assert not granted & WRITE_PERMISSIONS, (name, job_name, granted)


# @spec DELIVERY-CI-002
def test_shared_workflow_calls_are_pinned_to_a_sha_with_a_tag_comment(root: Path) -> None:
    calls = [
        line
        for path in sorted((root / ".github" / "workflows").glob("*.yml"))
        for line in path.read_text().splitlines()
        if "chainringlabs/ci/" in line
    ]
    assert calls
    for line in calls:
        match = SHARED.search(line)
        assert match, line
        assert len(match.group(2)) == 40, line
        assert re.fullmatch(r"\s*# v\d+\.\d+\.\d+", match.group(3)), line


# @spec DELIVERY-CI-003
def test_pull_request_checks(workflows: dict) -> None:
    test = workflows["test.yml"]
    assert set(_triggers(test)) == {"pull_request"}
    check = test["jobs"]["check"]
    assert check["needs"] == "python"
    runs = _runs(check)
    ordered = [
        "uv lock --check",
        "uv sync --frozen",
        "uv run ruff check --no-cache",
        "uv run ruff format --check --no-cache",
        "uv run python scripts/check_sizes.py",
        "uv run python scripts/check_specs.py",
        "uv run pytest",
    ]
    positions = [runs.index(command) for command in ordered]
    assert positions == sorted(positions)
    assert "--cov-fail-under=90" in runs
    assert "needs.python.outputs.version" in str(check)


# @spec DELIVERY-CI-004
def test_pull_request_image_build_and_smoke_test(workflows: dict) -> None:
    image = workflows["test.yml"]["jobs"]["image"]
    build = next(s for s in image["steps"] if "docker/build-push-action" in s.get("uses", ""))
    assert build["with"]["push"] is False
    assert build["with"]["load"] is True
    assert build["with"]["platforms"] == "linux/amd64"
    runs = _runs(image)
    assert "wearstream:test version" in runs  # the image's entry point is `wearstream`
    assert "needs.python.outputs.version" in str(image)
    for hardening in ("--read-only", "--tmpfs /tmp", "--cap-drop ALL", "no-new-privileges"):
        assert hardening in runs, hardening
    assert "WEARSTREAM_SOURCES=whoop,garmin" in runs
    assert "chown 65532:65532" in runs  # three empty mounts owned by the image's uid
    for mount in ("/data", "/state", "/secrets"):
        assert f":{mount}" in runs, mount
    assert "healthcheck" in runs and "no readable heartbeat" in runs  # before the daemon starts
    assert "daemon_started" in runs
    assert "waiting_for_auth" in runs and "whoop" in runs and "garmin" in runs
    assert "docker stop -t 60" in runs
    assert re.search(r"State\.ExitCode", runs)


# @spec DELIVERY-CI-007
def test_supply_chain_caller(workflows: dict) -> None:
    workflow = workflows["supply-chain.yml"]
    triggers = _triggers(workflow)
    assert {"pull_request", "schedule", "workflow_dispatch"} <= set(triggers)
    assert triggers["schedule"] == [{"cron": "0 13 * * 1"}]
    job = workflow["jobs"]["supply-chain"]
    assert job["uses"].startswith("chainringlabs/ci/.github/workflows/supply-chain.yml@")
    assert job["with"]["python-version"] == "${{ needs.python.outputs.version }}"
    assert job["secrets"] == {"SOCKET_SECURITY_API_KEY": "${{ secrets.SOCKET_SECURITY_API_KEY }}"}


# @spec DELIVERY-CI-008
def test_publish_runs_release_check_then_scan_then_publish(workflows: dict) -> None:
    workflow = workflows["docker-publish.yml"]
    triggers = _triggers(workflow)
    assert set(triggers) == {"push"}
    assert triggers["push"] == {"branches": ["main"], "tags": ["v*"]}
    jobs = workflow["jobs"]
    scan, image = jobs["supply-chain"], jobs["image"]
    assert scan["needs"] == "release-check"
    assert scan["uses"].startswith("chainringlabs/ci/.github/workflows/supply-chain.yml@")
    assert scan["with"]["python-version"] == "${{ needs.release-check.outputs.python }}"
    assert image["needs"] == "supply-chain"
    assert image["uses"].startswith("chainringlabs/ci/.github/workflows/docker-publish.yml@")
    assert set(image["permissions"]) == {"contents", *WRITE_PERMISSIONS}


# @spec DELIVERY-CI-009
def test_retention_keeps_latest_and_every_release(workflows: dict) -> None:
    workflow = workflows["ghcr-retention.yml"]
    triggers = _triggers(workflow)
    assert triggers["schedule"] == [{"cron": "0 6 * * 1"}]
    assert "workflow_dispatch" in triggers
    job = workflow["jobs"]["prune"]
    assert job["uses"].startswith("chainringlabs/ci/.github/workflows/ghcr-retention.yml@")
    assert job["with"]["exclude-tags"] == "latest,*.*.*"


# @spec DELIVERY-CI-011
def test_python_version_comes_from_a_first_job(workflows: dict) -> None:
    for name, job_name, output in (
        ("test.yml", "python", "version"),
        ("supply-chain.yml", "python", "version"),
        ("docker-publish.yml", "release-check", "python"),
    ):
        job = workflows[name]["jobs"][job_name]
        assert output in job["outputs"], (name, job_name)
        assert "scripts/python_version.py" in _runs(job), (name, job_name)


# @spec DELIVERY-REL-001
def test_release_check_always_runs_with_full_history(workflows: dict) -> None:
    job = workflows["docker-publish.yml"]["jobs"]["release-check"]
    assert "if" not in job
    checkout = next(s for s in job["steps"] if "actions/checkout" in s.get("uses", ""))
    assert checkout["with"]["fetch-depth"] == 0
    assert "scripts/check_release.py" in _runs(job)
