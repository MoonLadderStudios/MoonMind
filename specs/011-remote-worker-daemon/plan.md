# Implementation Plan: Agent Queue Remote Worker Daemon (Milestone 3)

**Branch**: `011-remote-worker-daemon` | **Date**: 2026-02-13 | **Spec**: `specs/011-remote-worker-daemon/spec.md`
**Input**: Feature specification from `/specs/011-remote-worker-daemon/spec.md`

## Summary

Implement Milestone 3 from `docs/CodexTaskQueue.md` by adding a standalone `moonmind-codex-worker` daemon CLI that polls queue jobs, executes `codex_exec` payloads, uploads execution artifacts, maintains lease heartbeats, and transitions jobs to completed/failed states with validation tests.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: httpx, FastAPI-compatible queue contracts, existing queue schemas/services, subprocess-based CLI execution  
**Storage**: Local filesystem worker workdir for cloned repos and temporary artifacts; MoonMind queue/artifact persistence remains PostgreSQL + artifact filesystem via existing APIs  
**Testing**: pytest via `./tools/test_unit.sh`  
**Target Platform**: Linux containers and local shells running Codex CLI with MoonMind API access  
**Project Type**: Backend worker daemon + CLI package entrypoint  
**Performance Goals**: Poll loop remains bounded by configurable interval; heartbeat cadence renews leases before expiry for long-running jobs  
**Constraints**: Must run outside Celery, fail fast when Codex CLI/login checks fail, and keep runtime scope to Milestone 3 (`codex_exec` path)  
**Scale/Scope**: Add new worker package (`moonmind/agents/codex_worker`), pyproject script entrypoint, and focused unit tests for CLI/preflight/handler/heartbeat behavior

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` currently contains unresolved placeholders and no enforceable MUST/SHOULD directives.
- No additional constitution constraints can be objectively applied; AGENTS and repository instructions are treated as binding constraints.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/011-remote-worker-daemon/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── codex-worker-runtime-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── agents/
    └── codex_worker/
        ├── __init__.py
        ├── cli.py                    # moonmind-codex-worker entrypoint
        ├── worker.py                 # daemon loop, config, queue client
        ├── handlers.py               # codex_exec execution pipeline
        └── utils.py                  # CLI preflight executable checks

pyproject.toml                        # poetry script registration

tests/
└── unit/
    └── agents/
        └── codex_worker/
            ├── test_cli.py
            ├── test_worker.py
            └── test_handlers.py
```

**Structure Decision**: Introduce a dedicated worker package under `moonmind/agents/codex_worker` to keep daemon runtime concerns separate from API and Celery workflow modules, with unit tests mirroring CLI/worker/handler boundaries.

## Phase 0: Research Plan

1. Choose worker-to-queue integration strategy (REST client abstraction over existing queue endpoints).
2. Define Codex preflight validation behavior (`verify_cli_is_executable` + `codex login status`).
3. Define heartbeat lifecycle model for long-running jobs and graceful cancellation.
4. Define `codex_exec` handler artifact strategy (log + patch + optional publish metadata).

## Phase 1: Design Outputs

- `research.md`: documented technical decisions and rejected alternatives.
- `data-model.md`: runtime worker models and payload contracts.
- `contracts/codex-worker-runtime-contract.md`: queue endpoint interactions and expected request/response shapes used by the daemon.
- `contracts/requirements-traceability.md`: one row per `DOC-REQ-*` with implementation/validation strategy.
- `quickstart.md`: local setup, env vars, daemon invocation, and verification flow.

## Post-Design Constitution Re-check

- Design delivers runtime implementation and validation tests per AGENTS constraints.
- No enforceable constitution violations identified due placeholder-only constitution file.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
