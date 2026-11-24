---
parent: high-level-design
prefix: DELIVERY
---

# Delivery

## Context and Design Philosophy

Delivery is how WearStream reaches a person's host: the repository's toolchain, the container
image, the CI that tests, builds and publishes it, dependency updates, releases, and the reference
deployments and documentation an operator starts from.

**Reuse the organization's delivery, build nothing beside it.** Chain Ring Labs already provides
shared, reviewed pieces: reusable GitHub Actions workflows in `chainringlabs/ci` (image build and
publish with signing, SBOM and provenance; dependency scanning; registry retention), a shared
Renovate preset in `chainringlabs/renovate-config`, organization defaults in `chainringlabs/.github`
(the security policy), and an organization ruleset on `main`. WearStream calls and extends these and
keeps only what they don't cover: its own tests and image smoke test, its Dockerfile, its release
checks and its deployment examples. It follows the conventions of the organization's other
Chainguard-based Python services, so one person can maintain them all the same way.

## Repository and toolchain

| Item | Choice |
|---|---|
| Python | The version the Chainguard runtime image ships (3.15 today), declared once as `requires-python = ">=3.15"` in `pyproject.toml`. CI reads the major.minor from there, tests on it and passes it to the shared scan; the image smoke test fails if the image's major.minor differs, so a runtime change arrives as a reviewed pull request. There is no `.python-version` file (it is git-ignored by convention) |
| Project | One package, `wearstream`, in `src/wearstream/`, tests in `tests/`, repository scripts in `scripts/`, agent configuration in `.claude/`; `pyproject.toml` declares the `wearstream` console script |
| Dependencies | uv: `pyproject.toml` and `uv.lock` are the single source of truth |
| Dependency cooldown | `[tool.uv] exclude-newer = "7 days"`, which is how the shared Renovate preset applies its cooldown to uv projects, with `garminconnect` exempted (below) |
| Versions | Exact versions come from the lock; `garminconnect` takes a lower bound (it must follow Garmin's changes), and its `curl_cffi` transport is a direct dependency |
| Lint and format | Ruff (`ruff check --no-cache`, `ruff format --check`) |
| Tests | pytest with coverage, at least 90% of lines; tests follow the `@spec` annotation convention |
| Size limits | No Python file under `src/`, `scripts/` or `tests/` over 300 physical lines, and no function over 50 lines (from its definition to its last line, by the syntax tree), checked by a script in CI |
| Local environment | `uv sync`, or `uv venv --python 3.15` followed by `uv pip install -e . --group dev` |

Files at the root: `pyproject.toml`, `uv.lock`, `Dockerfile`, `.dockerignore`
(the `.gitignore` entries plus `.git/`, `docs/`, `tests/`, `scripts/`, `.claude/`), `.env.example`,
`renovate.json`, `README.md`, `AGENTS.md` and `CLAUDE.md`, `LICENSE`. An `osv-scanner.toml` is added
only if a vulnerability must be ignored, with the reason beside it.

## The image

A two-stage build from Chainguard's Python images, each `FROM` pinned by digest:

1. **Builder**: `cgr.dev/chainguard/python:latest-dev`, switched to `USER root`. It copies `uv` from
   a pinned `ghcr.io/astral-sh/uv` image and sets `UV_PYTHON_DOWNLOADS=never` (uv must use the
   image's Python, never download its own), `UV_LINK_MODE=copy` and `UV_COMPILE_BYTECODE=1`. It
   installs a pinned version of Wolfi's `tini` package. Dependencies are installed first
   (`uv sync --frozen --no-dev --no-install-project`), in a layer of their own, then the project
   (`uv sync --frozen --no-dev --no-editable`), into `/app/.venv`.
2. **Runtime**: `cgr.dev/chainguard/python:latest`, distroless (no shell, no package manager). It
   receives `/app/.venv`, `/usr/bin/tini`, and `tini`'s package SPDX document, so the image's SBOM and
   scanners account for the copied binary.

| Property | Value |
|---|---|
| User | `nonroot` (uid and gid `65532`); a deployment may run it as another non-root uid that owns the mounts |
| Entry point | `["/usr/bin/tini", "--", "wearstream"]` with command `["daemon"]`: `tini` is PID 1, forwards signals and reaps orphaned processes; `docker exec … wearstream auth whoop` runs the same console script directly |
| Environment | `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `HOME=/tmp` (the nonroot home is read-only), `PATH` including `/app/.venv/bin` |
| Health check | `HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 CMD ["wearstream", "healthcheck"]` (exec form, run directly, not through the entry point) |
| Stop signal | `SIGTERM` (the default); deployments give it at least 60 seconds before `SIGKILL` |
| Ports | None exposed |
| Volumes | None declared: an anonymous volume would hide a missing mount |
| Labels | OCI `title`, `description` and `licenses` (`MIT`) as defaults; the shared publish workflow's metadata sets `source`, `revision` (the commit) and `version` on published images, overriding the Dockerfile's |

The image runs with a **read-only root filesystem**: the only writable places are the three mounts
and a `tmpfs` at `/tmp`, which Python's temporary files and `HOME` need. The builder and runtime
images are updated together, in one Renovate group, so the virtual environment is always built by
the Python it runs on.

Moving to the organization's delayed, verified base images (`ghcr.io/chainringlabs/base/python`)
is tracked in issue #17.

## CI

All workflows declare `permissions: {}` at the top and grant each job only what it needs; nothing a
pull request runs holds write or OIDC permissions. Calls to `chainringlabs/ci` are pinned to a full
commit SHA with the release tag in a comment, as the organization requires.

| Workflow | Triggers | Does |
|---|---|---|
| `test.yml` | Pull requests | **Python:** a first job reads the major.minor from `requires-python` for the jobs after it. **Check:** `uv lock --check`; `uv sync --frozen`; Ruff lint and format; the size-limit script; the spec-citation script; pytest with coverage (at least 90%) on that Python. **Image:** build the image locally (no push, `load: true`) and smoke-test it (below) |
| `supply-chain.yml` | Pull requests, Mondays 13:00 UTC, manual | A first job reads the Python major.minor; then calls `ci/supply-chain.yml` with it as `python-version`, passing only the Socket key secret by name (never every secret): OSV in diff mode on pull requests; OSV in full mode plus `pip-audit` on the schedule and manual runs |
| `docker-publish.yml` | Pushes to `main`; `v*` tags | A release-check job always runs (a no-op off tags) and reads the Python major.minor; then `ci/supply-chain.yml` (with that `python-version`), then `ci/docker-publish.yml`, which pushes `ghcr.io/chainringlabs/wearstream` (`latest` on `main`, `X.Y.Z` and `X.Y` on tags, `sha-<short>` always), signs it with cosign and attaches an SBOM and build provenance |
| `ghcr-retention.yml` | Mondays 06:00 UTC, manual | Calls `ci/ghcr-retention.yml` with `exclude-tags: latest,*.*.*`, so release versions are never pruned while `sha-` builds of `main` are |

**The image smoke test** runs the freshly built image with `WEARSTREAM_SOURCES=whoop,garmin`,
three empty mounts owned by uid `65532`, `--read-only --tmpfs /tmp --cap-drop ALL --security-opt
no-new-privileges`, and no credentials. It checks that `wearstream version` runs; that the image's
Python major.minor matches `requires-python`; that `wearstream healthcheck` exits `1` with a missing
heartbeat as its reason before the daemon starts; that the daemon logs `daemon_started`, both sources
reach `waiting_for_auth` without any network call (which exercises child processes, `curl_cffi` and
`garminconnect` in the distroless runtime), and `wearstream healthcheck` then exits `0`; and that
`docker stop -t 60` ends it with exit code `0`.

**The spec-citation script** (`scripts/check_specs.py`) collects every spec ID from
`docs/intent/**/*-specs.md` and fails when a `@spec` annotation in `src/`, `tests/`, `scripts/`,
the `Dockerfile` or `.github/` cites an ID that doesn't exist, or a spec file repeats an ID.

**The scan gates publishing**, as in the organization's other services: an image is built only
after the full supply-chain scan passes. An advisory that has no fix, or doesn't apply, is
acknowledged in `osv-scanner.toml` beside `uv.lock`, with a reason and an optional `ignoreUntil`; the
shared workflow honours that one list for both OSV and `pip-audit`, and an expired entry counts
again.

**Required checks.** A repository ruleset on `main` requires the `test.yml` jobs and the
supply-chain scan, on top of the organization's GitGuardian check, so an automerged dependency
update can't merge with failing tests. It is created in the same change that adds the workflows,
since a check that never reports would block every pull request. The shared preset also expects a
repository cooldown check for images whose registry gives no release date (`cgr.dev`); WearStream
accepts that risk until issue #17 moves it to the organization's delayed base images, and the
digest pin plus the Python-version smoke test bound what an unreviewed base update can change.

## Releases

- **The version** lives in `pyproject.toml`. Between releases it is the next version as a
  development release (`X.Y.Z.dev0`), so a `main` build reports "the upcoming release, unreleased"
  through `wearstream version` and every sidecar's `processor_version`; the image's `revision` label
  names the exact commit. A release is a pull request that sets the plain version (`X.Y.Z`, never a
  pre-release), a `vX.Y.Z` tag on the merge commit, then a pull request setting the next `.dev0`.
- **The release check** (in `docker-publish.yml`, with the full history) refuses a tag that isn't
  `v` plus the `pyproject.toml` version, or whose commit isn't on `main`.
- **A tag ruleset** lets only maintainers create `v*` tags.
- **Cadence.** A release at least once a month, and promptly after a security fix in a dependency or
  the base image, so deployments pinned to a version still receive fixes.
- **Image tags** are `X.Y.Z` and `X.Y` (no `v`); deployments pin `X.Y.Z`. A new GHCR package is
  private until made public once by hand, after the first publish.

## Dependency updates

`renovate.json` extends `github>chainringlabs/renovate-config` and adds:

- the Chainguard builder and runtime images as one group, never updated apart. A digest that changes
  only the Python patch version automerges after CI; one that changes the minor version fails the
  smoke test, and the documented Python-bump pull request updates `requires-python` and re-locks
  together;
- **`garminconnect` without the 7-day cooldown, in its own group with automerge off**: when Garmin
  changes its sign-in, a library fix must reach a release in days, but a same-day release of the
  library that handles the Garmin password is merged by a person, never automatically. uv's
  per-package `exclude-newer` override matches, so lock maintenance can take it too.
  `curl_cffi` keeps the cooldown; the README documents a temporary per-package override for an
  emergency.

Everything else (the cooldown for all other packages, digest pinning, automerge of aged minor and
patch updates, weekly lock maintenance, immediate `chainringlabs/ci` pins) comes from the preset.

## Configuration template

`.env.example` lists every operator setting with its default, commented out, grouped as in the
operator-interface design, plus `WEARSTREAM_SOURCES=whoop,garmin` uncommented. Values are unquoted, because Docker's
environment files keep quotes literally. It omits the development-only path
variables, and a test keeps it matching the settings registry. It holds no credentials: a comment at
the top says credentials go in files under `/secrets`, never in this file.

## Reference deployments

`docs/deploy/` holds a Compose file and an Ansible task that an operator can copy. Both:

- run `ghcr.io/chainringlabs/wearstream:X.Y.Z`, restarting unless stopped (`restart: unless-stopped`
  in Compose, `restart_policy: unless-stopped` in Ansible), with the container named `wearstream`
  so `docker exec wearstream …` works;
- mount host folders at `/data` (a filesystem with hard links), `/state` (a local filesystem) and
  `/secrets`, owned by the container's uid (the README shows the `chown`), refusing to create a
  missing folder (Compose's long syntax with `bind.create_host_path: false`; Ansible's `mounts:` with
  `type: bind`, since its short `volumes:` syntax creates missing folders), with nothing else
  writable: a read-only root filesystem and a `tmpfs` at `/tmp`;
- drop all Linux capabilities and set `no-new-privileges`;
- publish no ports and allocate no TTY;
- give the container **60 seconds** to stop (`stop_grace_period: 60s` in Compose, `stop_timeout: 60`
  in Ansible), so credential exchanges finish before a kill;
- keep the image's health check, whose 120-second start period covers startup recovery;
- leave Docker's `init` off, because the image's entry point is already `tini`;
- rotate the container log (`json-file` with a size and file limit);
- read settings from an environment file built from `.env.example`;
- show, commented, how Compose secrets can supply the static credential files by targeting
  `/secrets/<source>/<file>`, noting that the host file must be readable by the container's uid and
  that Docker may leave an empty placeholder file in the mounted folder after `down`.

## Documentation

`README.md` is the operator's starting point:

1. **What WearStream is** and what it keeps (raw responses, a capture tree that is the bronze layer
   of a data lake), and what it doesn't do (no UI, no parsing).
2. **Before you start:** registering a Whoop developer application (scopes, redirect URI), Garmin
   credentials and MFA, and the host folders: `/data` on a filesystem with hard links, `/state` on a
   local filesystem, ownership by uid `65532` (or the uid the deployment uses), and the umask setting.
3. **Run it:** the Compose quick start; the Ansible task.
4. **Authorize:** `docker exec -it wearstream wearstream auth whoop` and `… auth garmin`; a one-off
   `docker run --rm -it` needs `--read-only --tmpfs /tmp` and the same mounts.
5. **Check on it:** `wearstream status`, `wearstream config`, what the log's ERROR lines and actions
   mean, and the health check.
6. **Backfill:** `WEARSTREAM_<SOURCE>_BACKFILL_FROM` and what to expect of its pace.
7. **Adopting an existing capture tree:** mounting it as `/data`, stopping the other tool before
   WearStream refreshes a credential, and authorizing afresh.
8. **Upgrading** (pin a new `X.Y.Z`; retired settings warn) and **verifying the image** (the
   `gh attestation verify` and `cosign verify` commands from `chainringlabs/ci`).
9. **Configuration reference:** `.env.example` and the operator-interface design.

`AGENTS.md` (and `CLAUDE.md`, a link to it) states the tech stack, commands and architecture for
contributors, with the LID block.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| CI | The organization's reusable workflows, plus a repository `test.yml` | Workflows of WearStream's own | Signing, SBOM, provenance, scanning and retention are built and reviewed once for every repository; only tests and the smoke test are repository-specific. |
| Pull-request image check | Build and smoke-test in `test.yml`, no push | Call `ci/docker-publish.yml` on pull requests | The shared job needs write and OIDC permissions, which pull requests (Renovate's included) shouldn't hold, and it can't run the image; the other services build in `test.yml` too. |
| Base image | `cgr.dev/chainguard/python`, pinned by digest | The delayed `base/python:aged` copies now | `base-images` doesn't track Chainguard Python yet; issue #17 tracks the move. |
| Python version | Whatever the runtime image ships, declared once in `requires-python` and enforced by the smoke test | A `.python-version` file; a fixed version declared by hand | The runtime decides the Python that runs; declaring another lets CI test a version that never ships. `.python-version` is git-ignored by convention, so `requires-python` is the one committed place. |
| PID 1 | `tini` from Wolfi, pinned, with its SPDX document | The daemon reaps orphans itself; Docker's `--init` | The supervisor spawns process groups and needs a real init; bundling it means no deployment has to remember `--init`. |
| Requirements files | None; `uv.lock` only | Export `requirements.txt` files and check them in CI | Hosted Renovate updates only `uv.lock`, so exported files would fail every dependency update; the organization's other services and its scanning workflow work from `uv.lock`. This departs from the general scaffolding rule of committing requirements files. |
| Merge gating | A repository ruleset requiring tests, the image check and the supply-chain scan | Rely on the organization ruleset | It requires only the secret scan, and the preset automerges; without required checks a failing update merges. |
| Registry retention | Keep `latest` and every `X.Y.Z` | Keep the 5 newest tagged versions | Every merge to `main` adds a tagged build; a pinned release would be deleted within a few merges. |
| `garminconnect` cooldown | Exempt, in its own group, merged by a person | The 7-day cooldown like everything else; exempt and automerged | A Garmin sign-in change stops Garmin capture until the library fix ships, so a week's wait is a week of lost capture; but automerging a same-day release of the library holding the password would skip the review the cooldown stands in for. |
| Version of `main` builds | The next version as `X.Y.Z.dev0` in `pyproject.toml` | A commit-counted `X.Y.Z.devN` computed at build time | The shared publish workflow passes no build arguments and keeps no git history, so the build can't compute one; the image's `revision` label names the commit. |
| Acknowledging an advisory | One `osv-scanner.toml` beside `uv.lock`, honoured by OSV and `pip-audit` | Publish without waiting for the scan | Matches the organization's other services; a reasoned, dated entry unblocks publishing without disabling the scan. |
| Install mode | Non-editable virtual environment | Editable install with the source tree copied | The runtime needs only the environment, which is smaller and ships no source. |
| Volumes in the image | None declared | `VOLUME` for each mount | A missing mount then fails loudly (a read-only filesystem) instead of writing to an anonymous volume lost on replacement. |
| Deployment image tag | Exact `X.Y.Z` | `latest`; `X.Y` | Upgrades are deliberate; `X.Y` and `latest` change under a restart. |

## Open Questions & Future Decisions

### Deferred
1. Delayed, verified base images (issue #17).
2. A multi-architecture image (issue #6) needs an `arm64` option in `ci/docker-publish.yml`.

## References

- `chainringlabs/ci`: `docker-publish.yml`, `supply-chain.yml`, `ghcr-retention.yml` and its README
  (image verification)
- `chainringlabs/renovate-config`: the shared preset
- `chainringlabs/base-images`: delayed, verified base images
- The organization's other Chainguard Python services, whose Dockerfiles and workflows these follow
- `docs/intent/supervisor/supervisor-design.md` and `docs/intent/garmin/garmin-design.md`:
  obligations on delivery (`tini`, stop timeout, health-check start period, `/state` local,
  `curl_cffi`)
- `docs/intent/operator/operator-design.md`: settings, secret files, commands, `WEARSTREAM_UMASK`
