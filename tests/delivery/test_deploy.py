"""The configuration template and the reference deployments (DELIVERY-DEPLOY-001..003)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from wearstream.settings.registry import REGISTRY

DEPLOY = ("data", "state", "secrets")


def _default(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(value)
    return str(value)


def _release(pyproject: dict) -> str:
    return re.sub(r"\.dev\d+$", "", pyproject["project"]["version"])


# @spec DELIVERY-DEPLOY-001
def test_env_example_matches_the_registry(root: Path) -> None:
    text = (root / ".env.example").read_text()
    commented = dict(re.findall(r"^# (WEARSTREAM_[A-Z0-9_]+)=(.*)$", text, re.MULTILINE))
    active = dict(re.findall(r"^(WEARSTREAM_[A-Z0-9_]+)=(.*)$", text, re.MULTILINE))
    assert active == {"WEARSTREAM_SOURCES": "whoop,garmin"}
    expected = {
        name: _default(setting.default)
        for name, setting in REGISTRY.items()
        if setting.group != "path" and name != "WEARSTREAM_SOURCES"
    }
    assert commented == expected
    assert '"' not in text and "'" not in "".join(commented.values())
    assert "/secrets" in text.split("WEARSTREAM_")[0]  # the header says where credentials go
    for word in ("PASSWORD", "SECRET", "TOKEN"):
        assert not re.search(rf"^#? ?\w*{word}\w*=", text, re.MULTILINE), word


@pytest.fixture(scope="module")
def compose(root: Path) -> dict:
    return yaml.safe_load((root / "docs" / "deploy" / "compose.yaml").read_text())["services"][
        "wearstream"
    ]


@pytest.fixture(scope="module")
def ansible(root: Path) -> dict:
    [task] = yaml.safe_load((root / "docs" / "deploy" / "wearstream-task.yml").read_text())
    return task["community.docker.docker_container"]


# @spec DELIVERY-DEPLOY-002
def test_compose_runs_a_pinned_hardened_container(compose: dict, pyproject: dict) -> None:
    assert compose["image"] == f"ghcr.io/chainringlabs/wearstream:{_release(pyproject)}"
    assert (compose["container_name"], compose["restart"]) == ("wearstream", "unless-stopped")
    assert compose["read_only"] is True and compose["tmpfs"] == ["/tmp"]
    assert compose["cap_drop"] == ["ALL"]
    assert compose["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in compose and compose.get("tty", False) is False
    assert compose["init"] is False
    assert compose["stop_grace_period"] == "60s"
    logging = compose["logging"]
    assert logging["driver"] == "json-file"
    assert {"max-size", "max-file"} <= set(logging["options"])
    assert "healthcheck" not in compose  # the image's own health check is kept


# @spec DELIVERY-DEPLOY-003
def test_compose_binds_existing_host_folders_and_reads_an_env_file(compose: dict) -> None:
    mounts = {m["target"]: m for m in compose["volumes"]}
    assert set(mounts) == {f"/{name}" for name in DEPLOY}
    for mount in mounts.values():
        assert mount["type"] == "bind"
        assert mount["bind"]["create_host_path"] is False
    assert compose["env_file"]


# @spec DELIVERY-DEPLOY-002
def test_ansible_runs_a_pinned_hardened_container(ansible: dict, pyproject: dict) -> None:
    assert ansible["image"] == f"ghcr.io/chainringlabs/wearstream:{_release(pyproject)}"
    assert (ansible["name"], ansible["restart_policy"]) == ("wearstream", "unless-stopped")
    assert ansible["read_only"] is True and ansible["tmpfs"] == ["/tmp"]
    assert ansible["cap_drop"] == ["ALL"]
    assert ansible["security_opts"] == ["no-new-privileges:true"]
    assert "published_ports" not in ansible and ansible.get("tty", False) is False
    assert ansible["init"] is False
    assert ansible["stop_timeout"] == 60
    assert ansible["log_driver"] == "json-file"
    assert {"max-size", "max-file"} <= set(ansible["log_options"])


# @spec DELIVERY-DEPLOY-003
def test_ansible_binds_existing_host_folders_and_reads_an_env_file(ansible: dict) -> None:
    assert "volumes" not in ansible  # its short syntax creates missing host folders
    mounts = {m["target"]: m for m in ansible["mounts"]}
    assert set(mounts) == {f"/{name}" for name in DEPLOY}
    assert all(m["type"] == "bind" for m in mounts.values())
    assert ansible["env_file"]
