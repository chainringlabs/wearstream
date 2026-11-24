# Delivery — EARS specs

Design: [delivery-design.md](delivery-design.md). Prefix `DELIVERY`. Facets: `REPO`, `IMAGE`,
`CI`, `REL`, `DEPS`, `DEPLOY`, `DOC`.
"The image" is `ghcr.io/chainringlabs/wearstream`; "the shared workflows" are those in
`chainringlabs/ci`; a "pinned" `chainringlabs/ci` call names a full commit SHA with its release tag
in a comment.

## Repository

- [x] **DELIVERY-REPO-001**: The repository shall keep `pyproject.toml` and `uv.lock` as the only dependency declarations, with no committed `requirements*.txt` files.
- [x] **DELIVERY-REPO-002**: `pyproject.toml` shall declare the major.minor Python version the runtime image ships as `requires-python`'s floor (`>=3.15` today), and the repository shall keep no `.python-version` file.
- [x] **DELIVERY-REPO-003**: `pyproject.toml` shall set `[tool.uv] exclude-newer = "7 days"`, with a per-package override that exempts `garminconnect`.
- [x] **DELIVERY-REPO-004**: The repository shall lay out the package in `src/wearstream/`, tests in `tests/`, repository scripts in `scripts/`, design documents in `docs/`, and agent configuration in `.claude/`, and `pyproject.toml` shall declare the `wearstream` console script.
- [x] **DELIVERY-REPO-005**: `.dockerignore` shall exclude everything `.gitignore` excludes, plus `.git/`, `docs/`, `tests/`, `scripts/` and `.claude/`.

## The image

- [x] **DELIVERY-IMAGE-001**: The Dockerfile shall build in two stages, from `cgr.dev/chainguard/python:latest-dev` and then `cgr.dev/chainguard/python:latest`, each pinned by digest.
- [x] **DELIVERY-IMAGE-002**: The builder stage shall run as root, take `uv` from a digest-pinned `ghcr.io/astral-sh/uv` image, set `UV_PYTHON_DOWNLOADS=never`, `UV_LINK_MODE=copy` and `UV_COMPILE_BYTECODE=1`, install dependencies with `uv sync --frozen --no-dev --no-install-project` before copying the source, and then install the project with `uv sync --frozen --no-dev --no-editable` into `/app/.venv`.
- [x] **DELIVERY-IMAGE-003**: The builder stage shall install a pinned version of Wolfi's `tini` package, and the runtime stage shall receive `/usr/bin/tini` and that package's SPDX document.
- [x] **DELIVERY-IMAGE-004**: The runtime stage shall receive only `/app/.venv` from the build, not the source tree.
- [x] **DELIVERY-IMAGE-005**: The image shall run as `nonroot` (uid and gid 65532), with entry point `["/usr/bin/tini", "--", "wearstream"]` and command `["daemon"]`.
- [x] **DELIVERY-IMAGE-006**: The image shall set `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `HOME=/tmp`, and a `PATH` that starts with `/app/.venv/bin`.
- [x] **DELIVERY-IMAGE-007**: The image shall declare `HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 CMD ["wearstream", "healthcheck"]` in exec form.
- [x] **DELIVERY-IMAGE-008**: The image shall expose no port and declare no volume.
- [x] **DELIVERY-IMAGE-009**: The Dockerfile shall set the OCI labels `title`, `description` and `licenses` (`MIT`), leaving `source`, `revision` and `version` to the shared publish workflow's metadata.

## CI

- [x] **DELIVERY-CI-001**: Every workflow shall declare `permissions: {}` at the top level and grant each job only the permissions it uses, and no job that a pull request triggers shall be granted `packages: write`, `id-token: write`, `attestations: write` or `artifact-metadata: write`.
- [x] **DELIVERY-CI-002**: Every call to a shared workflow shall be pinned to a full commit SHA with its release tag in a comment.
- [x] **DELIVERY-CI-011**: Each workflow that needs the Python version shall read the major.minor from `requires-python` in a first job and pass it to later jobs and to shared workflows through that job's outputs.
- [x] **DELIVERY-CI-003**: On every pull request, `test.yml` shall run `uv lock --check`, `uv sync --frozen`, Ruff lint and format checks without a cache, the size-limit script, the spec-citation script, and pytest with coverage on the Python of DELIVERY-CI-011, failing below 90% line coverage.
- [ ] **DELIVERY-CI-004**: On every pull request, `test.yml` shall build the image without pushing it and run it with `WEARSTREAM_SOURCES=whoop,garmin`, three empty mounts owned by uid 65532, no credentials, and `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`, verifying that `wearstream version` runs; that the image's Python major.minor equals the one in `requires-python`; that `wearstream healthcheck` exits 1 naming a missing heartbeat before the daemon starts; that after `daemon_started` both sources reach `waiting_for_auth` and `wearstream healthcheck` exits 0; and that `docker stop -t 60` ends the container with exit code 0.
- [x] **DELIVERY-CI-005**: The spec-citation script shall fail when a `@spec` annotation in `src/`, `tests/`, `scripts/`, the `Dockerfile` or `.github/` cites an ID absent from `docs/intent/**/*-specs.md`, or when a spec file repeats an ID.
- [x] **DELIVERY-CI-006**: The size-limit script shall fail when a Python file under `src/`, `scripts/` or `tests/` has more than 300 physical lines, or a function in one spans more than 50 lines from its definition to its last line by the syntax tree.
- [x] **DELIVERY-CI-007**: `supply-chain.yml` shall call the shared `supply-chain.yml` on pull requests, every Monday at 13:00 UTC and on manual runs, with `python-version` from DELIVERY-CI-011, passing only the `SOCKET_SECURITY_API_KEY` secret, by name.
- [x] **DELIVERY-CI-008**: On pushes to `main` and on `v*` tags, `docker-publish.yml` shall run its release-check job, then call the shared `supply-chain.yml` with `python-version` from DELIVERY-CI-011, then call the shared `docker-publish.yml`, each only if the step before it succeeded.
- [x] **DELIVERY-CI-009**: `ghcr-retention.yml` shall call the shared `ghcr-retention.yml` every Monday at 06:00 UTC and on manual runs, with `exclude-tags` keeping `latest` and every `X.Y.Z` version.
- [ ] **DELIVERY-CI-010**: The repository shall have a ruleset on `main` requiring the `test.yml` jobs and the supply-chain scan to pass before merging, created together with those workflows.

## Releases

- [x] **DELIVERY-REL-001**: The release-check job of `docker-publish.yml` shall run on every trigger, succeed without checks when the trigger is not a `v*` tag, and, for a `v*` tag, check out the full history and fail unless the tag equals `v` followed by the `pyproject.toml` version, that version is a plain `X.Y.Z`, and the tagged commit is an ancestor of `main`.
- [x] **DELIVERY-REL-002**: Between releases, `pyproject.toml` shall hold the next release's version as `X.Y.Z.dev0`, so a build from `main` reports it through `wearstream version` and the sidecar `processor_version`.
- [ ] **DELIVERY-REL-003**: The repository shall have a tag ruleset allowing only maintainers to create `v*` tags.

## Dependency updates

- [x] **DELIVERY-DEPS-001**: `renovate.json` shall extend `github>chainringlabs/renovate-config`.
- [x] **DELIVERY-DEPS-002**: `renovate.json` shall group the Chainguard builder and runtime images into one update.
- [x] **DELIVERY-DEPS-003**: `renovate.json` shall exempt `garminconnect` from the minimum release age and put its updates in a group of their own with automerge off.

## Configuration template and deployments

- [ ] **DELIVERY-DEPLOY-001**: `.env.example` shall list every operator setting except the path settings, with its default, commented out and unquoted, plus `WEARSTREAM_SOURCES=whoop,garmin` uncommented, and no credential; a test shall fail when it and the settings registry disagree.
- [ ] **DELIVERY-DEPLOY-002**: The reference Compose file and Ansible task in `docs/deploy/` shall run the image by an exact `X.Y.Z` tag in a container named `wearstream`, restarting unless stopped, with a read-only root filesystem, a `tmpfs` at `/tmp`, all capabilities dropped, `no-new-privileges`, no published ports, no TTY, Docker's `init` off, a rotated `json-file` log, and a 60-second stop timeout.
- [ ] **DELIVERY-DEPLOY-003**: The reference deployments shall bind-mount host folders at `/data`, `/state` and `/secrets` without creating missing host folders (Compose's long syntax with `bind.create_host_path: false`; Ansible's `mounts:` with `type: bind`), and read settings from an environment file.

## Documentation

- [ ] **DELIVERY-DOC-001**: `README.md` shall cover, in order: what WearStream is and isn't; the prerequisites (a Whoop developer application, Garmin credentials, host folders on filesystems with hard links for `/data` and local storage for `/state`, their ownership, and the umask setting); running with Compose and Ansible; authorizing each source; checking on it; backfill; adopting an existing capture tree; upgrading and verifying the image; and where the configuration reference is.
