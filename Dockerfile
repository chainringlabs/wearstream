# syntax=docker/dockerfile:1
# WearStream image: a supervisor daemon that captures raw Whoop and Garmin Connect data.
# Stage 1 builds the virtual environment with uv; stage 2 is Chainguard's distroless Python.

# =============================================================================
# Stage 1: builder
# =============================================================================
# @spec DELIVERY-IMAGE-001, DELIVERY-IMAGE-002, DELIVERY-IMAGE-003
FROM cgr.dev/chainguard/python:latest-dev@sha256:8baf9c15f136262ed5578e795e782a1945e3bf6cec000873f4e93421a1ed8370 AS builder

USER root
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /usr/local/bin/uv

# uv must use the image's own Python, never download one (the venv's interpreter link has to
# resolve in the runtime image too).
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# tini is PID 1 in the runtime image (the supervisor spawns process groups).
RUN apk add --no-cache tini=0.19.0-r27

# Dependency layer first, so source changes don't reinstall third-party packages.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then the project itself, installed non-editable so the runtime needs only the venv.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# =============================================================================
# Stage 2: runtime (distroless: no shell, no package manager)
# =============================================================================
# @spec DELIVERY-IMAGE-004, DELIVERY-IMAGE-005, DELIVERY-IMAGE-006, DELIVERY-IMAGE-007
# @spec DELIVERY-IMAGE-008, DELIVERY-IMAGE-009
FROM cgr.dev/chainguard/python:latest@sha256:2f72bd207faa364dcbf8e47b593e8e6b794432f35b3f2f78d62439a3b4cf3088

LABEL org.opencontainers.image.title="wearstream" \
      org.opencontainers.image.description="Self-hosted capture of your raw Whoop and Garmin Connect data" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder /usr/bin/tini /usr/bin/tini
COPY --from=builder /var/lib/db/sbom/tini-0.19.0-r27.spdx.json /var/lib/db/sbom/tini-0.19.0-r27.spdx.json
COPY --from=builder /app/.venv /app/.venv

USER nonroot
WORKDIR /app

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 CMD ["wearstream", "healthcheck"]

ENTRYPOINT ["/usr/bin/tini", "--", "wearstream"]
CMD ["daemon"]
