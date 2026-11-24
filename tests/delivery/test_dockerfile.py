"""The image contract (DELIVERY-IMAGE), read from the Dockerfile."""

from __future__ import annotations

import re

DIGEST = r"@sha256:[0-9a-f]{64}"


def _stages(dockerfile: str) -> list[str]:
    """The Dockerfile split into its stages, each starting with its FROM line."""
    parts = re.split(r"(?m)^(?=FROM )", dockerfile)
    return [part for part in parts if part.startswith("FROM ")]


# @spec DELIVERY-IMAGE-001
def test_two_stages_from_digest_pinned_chainguard_python(dockerfile: str) -> None:
    stages = _stages(dockerfile)
    assert len(stages) == 2
    assert re.match(rf"FROM cgr\.dev/chainguard/python:latest-dev{DIGEST} AS builder", stages[0])
    assert re.match(rf"FROM cgr\.dev/chainguard/python:latest{DIGEST}\s", stages[1])


# @spec DELIVERY-IMAGE-002
def test_builder_runs_as_root_with_pinned_uv_and_two_layer_sync(dockerfile: str) -> None:
    builder = _stages(dockerfile)[0]
    assert re.search(r"(?m)^USER root$", builder)
    assert re.search(rf"COPY --from=ghcr\.io/astral-sh/uv:[0-9.]+{DIGEST} /uv ", builder)
    for setting in ("UV_PYTHON_DOWNLOADS=never", "UV_LINK_MODE=copy", "UV_COMPILE_BYTECODE=1"):
        assert setting in builder
    deps = builder.index("uv sync --frozen --no-dev --no-install-project")
    source = builder.index("COPY src")
    project = builder.index("uv sync --frozen --no-dev --no-editable")
    assert deps < source < project


# @spec DELIVERY-IMAGE-003
def test_tini_is_pinned_and_shipped_with_its_spdx_document(dockerfile: str) -> None:
    builder, runtime = _stages(dockerfile)
    assert re.search(r"apk add --no-cache tini=\d[\w.\-]+", builder)
    assert "/usr/bin/tini" in runtime
    assert re.search(r"/var/lib/db/sbom/tini-[^\s]*\.spdx\.json", runtime)


# @spec DELIVERY-IMAGE-004
def test_runtime_receives_the_venv_but_not_the_source(dockerfile: str) -> None:
    runtime = _stages(dockerfile)[1]
    copies = re.findall(r"(?m)^COPY .*--from=builder (\S+)", runtime)
    assert "/app/.venv" in copies
    assert not any(path.startswith("/app/src") for path in copies)


# @spec DELIVERY-IMAGE-005
def test_runs_as_nonroot_with_tini_entry_point(dockerfile: str) -> None:
    runtime = _stages(dockerfile)[1]
    assert re.search(r"(?m)^USER nonroot$", runtime)
    assert 'ENTRYPOINT ["/usr/bin/tini", "--", "wearstream"]' in runtime
    assert 'CMD ["daemon"]' in runtime


# @spec DELIVERY-IMAGE-006
def test_runtime_environment(dockerfile: str) -> None:
    runtime = _stages(dockerfile)[1]
    for setting in ("PYTHONUNBUFFERED=1", "PYTHONDONTWRITEBYTECODE=1", "HOME=/tmp"):
        assert setting in runtime
    assert 'PATH="/app/.venv/bin:$PATH"' in runtime


# @spec DELIVERY-IMAGE-007
def test_exec_form_healthcheck(dockerfile: str) -> None:
    assert (
        "HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 "
        'CMD ["wearstream", "healthcheck"]'
    ) in dockerfile


# @spec DELIVERY-IMAGE-008
def test_no_ports_or_volumes(dockerfile: str) -> None:
    assert not re.search(r"(?m)^(EXPOSE|VOLUME)\b", dockerfile)


# @spec DELIVERY-IMAGE-009
def test_dockerfile_sets_only_the_default_labels(dockerfile: str) -> None:
    labels = set(re.findall(r"org\.opencontainers\.image\.(\w+)=", dockerfile))
    assert labels == {"title", "description", "licenses"}
    assert 'org.opencontainers.image.licenses="MIT"' in dockerfile
