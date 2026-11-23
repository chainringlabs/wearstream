# WearStream

## Overview

WearStream is a self-hosted container that keeps a complete local copy of one person's raw
Whoop and Garmin Connect data. A supervisor runs each source on its own schedule, re-captures
a trailing window of recent days (vendors restate history and sync late), backfills history
to a configured start date, and writes every response as-is, with a sidecar, to an
append-only capture tree under `/data`. It has no UI and no listening port; the operator log
is the interface. Design: `docs/high-level-design.md`.

## Tech Stack

- Language: Python 3.15, managed with uv (one `pyproject.toml`, one `uv.lock`)
- HTTP: httpx (Whoop); `garminconnect` (Garmin Connect, consumer sign-in)
- Logging: structlog (console by default, JSON Lines with `LOG_FORMAT=json`)
- Lint: Ruff; tests: pytest
- Runtime: Chainguard distroless Python image, non-root, read-only root filesystem, `linux/amd64`
- CI: `chainringlabs/ci` shared workflows (supply chain, image build/sign/attest, GHCR retention); Renovate
- Storage: local filesystem only (`/data`, `/state`, `/secrets`); no database

## Commands

*(Planned; the code does not exist yet.)*

- `uv sync --frozen` — install from the lock
- `uv run ruff check --no-cache` — lint
- `uv run pytest` — run tests
- `wearstream daemon` — run the supervisor (the container entry point)
- `wearstream run <source> <job> [--day D]` — run one unit of work
- `wearstream auth whoop|garmin` — one-time credential bootstrap (interactive)
- `wearstream status` — summary of runs, backfill and checks from `/state`

## Architecture

See the Components table in `docs/high-level-design.md`. Components (one design node each
under `docs/intent/`): capture, supervisor, checks, operator interface, whoop, garmin,
delivery. Sources depend on the core components, never the reverse.

## Environment Variables

*(Defined in the operator-interface design; none are final yet.)* Credentials are supplied
as secret files, never as plain environment values. Template: `.env.example` once defined.
Never commit `.env`.

## LID
- Mode: Full
- Version: 1.3.0

## Linked-Intent Development (MANDATORY)

**Consult the `linked-intent-dev` skill for ALL code changes.** All changes flow through the arrow of intent in one direction:

```
HLD → LLDs → EARS → Tests → Code
```

- **New features and refactors**: full six-phase workflow (HLD check → LLD check/draft → EARS → intent-narrowing edge audit → tests-first → code).
- **Bug fixes**: walk the arrow like any other change — find where behavior diverged from intent and cascade from there. No short-circuit.
- **If unsure**: use the full workflow.

Stop after each phase for user review. **Docs carry current intent, written to be read cold** — write each doc as if authored fresh today, from current intent alone: no narration of how it changed, no meaning that needs the conversation that produced it, no rebuttals to questions only a past discussion raised. Rationale, considered alternatives, and constraints a fresh author would independently write stay; record rejected alternatives and why in the LLD's Decisions & Alternatives table, not as asides in body prose.

**Memory vs. intent.** Before saving durable project knowledge to agent or tool memory, test whether it is project *intent* — would a fresh agent, in any tool, next session, need it to build this system correctly? If yes, record it in the arrow (HLD / LLD / EARS / decision doc), which travels and cascades — not in private, per-tool memory, where intent escapes the arrow. Knowledge about the user or how they like to work stays in memory.

### Navigation

| What you need | Where to look |
|---|---|
| High-level design | `docs/high-level-design.md` |
| Design tree (sub-HLDs, LLDs, their specs) | `docs/intent/` — one folder per node |
| EARS specs | beside each design doc as `{node}-specs.md` in the node's folder under `docs/intent/` |
| Decision docs | `docs/decisions/` (project-level) and `docs/intent/<segment>/decisions/` |
| Arrow of intent overlay | `docs/arrows/index.yaml` and per-segment docs in `docs/arrows/` |

### Terminology

- **HLD**: High-Level Design — single project-level doc at `docs/high-level-design.md`.
- **LLD**: Low-Level Design — detailed component design doc in `docs/intent/`. The design layer is a recursive tree: the root is the HLD, leaf LLDs own EARS, and a component deep enough to outgrow one doc becomes a sub-HLD (HLD-shaped, owns no EARS) with children beneath it. "HLD" and "LLD" are roles by position; depth-2 (one HLD over flat leaf LLDs) is the default.
- **EARS**: Easy Approach to Requirements Syntax — structured one-line requirements beside each design doc as `{node}-specs.md` in the node's folder under `docs/intent/`. IDs are path-concatenated — the root-to-leaf path of the owning segment plus a number — so a prefix grep gathers a subtree. Markers: `[x]` implemented, `[ ]` active gap, `[D]` deferred.
- **Arrow**: the unidirectional chain from vision to code (HLD → LLDs → EARS → Tests → Code). Strictly a DAG of intent.
- **Arrow segment**: the territory owned by one leaf LLD — the LLD itself plus the specs, tests, and code that cite its EARS IDs. The boundary is the leaf prefix. Within-segment cascade is free; across-segment cascade pauses.
- **Cascade**: propagating a change downstream through the arrow so adjacent levels stay coherent.

### Code annotations

Annotate code and tests with `@spec` comments citing EARS IDs:

```
# @spec SUPERVISOR-EXEC-001
```

Place the annotation at the *entry point of the behavior's implementation graph* — the topmost function or module owning the specified behavior, not every helper. When a behavior spans multiple subsystems (UI + API + database, for example), annotate at the entry point in each subsystem. Tests follow the same rule: annotate the test that directly exercises the spec, not every inner assertion.
