# Implementation Plan: Unified CLI Single Queue Worker Runtime

**Branch**: `018-unified-cli-queue` | **Date**: 2026-02-16 | **Spec**: `specs/018-unified-cli-queue/spec.md`
**Input**: Feature specification from `/specs/018-unified-cli-queue/spec.md`

## Summary

Implement a single-queue AI worker runtime that keeps `speckit` inside the shared worker image while adding Claude CLI support, runtime selection via `MOONMIND_WORKER_RUNTIME`, and runtime-neutral queue consumption through one queue (`moonmind.jobs`) with compatibility handling for legacy queue environment variables.

## Technical Context

**Language/Version**: Python 3.12 (runtime image), Bash/Dockerfile syntax for container build and startup scripts  
**Primary Dependencies**: Celery worker bootstrap modules, MoonMind workflow settings, Docker Compose service definitions, npm CLI tooling installs in `api_service/Dockerfile`  
**Storage**: Existing RabbitMQ broker queues and existing PostgreSQL/result persistence (no new database tables)  
**Testing**: Unit tests via `./tools/test_unit.sh`; targeted worker/config tests for queue and runtime-mode behavior  
**Target Platform**: Linux Docker runtime used by existing `api`, `celery-worker`, and specialized worker services  
**Project Type**: Backend worker orchestration and container runtime configuration  
**Performance Goals**: Preserve fair worker scheduling (`prefetch=1`) and avoid requeue thrash for runtime-neutral jobs  
**Constraints**: Keep `speckit` in `api_service/Dockerfile`; no credential baking in image layers; preserve backwards compatibility during queue migration  
**Scale/Scope**: Worker image tooling, runtime-mode gating, queue defaulting, compose runtime selection, and corresponding test coverage

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` is placeholder-only and has no enforceable MUST/SHOULD constraints.
- `AGENTS.md` constraints apply:
  - use global spec numbering (feature `018`);
  - runtime implementation (not docs-only);
  - use `./tools/test_unit.sh` for unit tests.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/018-unified-cli-queue/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   ├── runtime-job-contract.md
│   └── worker-runtime-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── Dockerfile

docker-compose.yaml

moonmind/
├── config/settings.py
└── workflows/speckit_celery/
    ├── __init__.py
    └── celeryconfig.py

celery_worker/
├── speckit_worker.py
└── gemini_worker.py

tests/
└── unit/
    ├── config/
    │   └── test_settings.py
    └── workflows/speckit_celery/
        └── test_celeryconfig.py
```

**Structure Decision**: Implement queue/runtime behavior where MoonMind currently derives Celery queue settings and worker bootstrap health checks, while extending the shared Docker runtime image and compose topology.

## Phase 0: Research Plan

1. Confirm safe/maintainable Claude CLI installation pattern aligned with existing Codex/Gemini/Speckit fallback behavior in `api_service/Dockerfile`.
2. Determine queue migration strategy that defaults to `moonmind.jobs` while allowing temporary compatibility with existing `SPEC_WORKFLOW_CODEX_QUEUE` and `GEMINI_CELERY_QUEUE` variables.
3. Define runtime mode validation strategy (`codex|gemini|claude|universal`) that fails fast in worker startup.
4. Define runtime-neutral payload contract representation and universal-targeting semantics without introducing queue partitioning.
5. Define validation approach and test coverage for queue/routing/runtime settings changes.

## Phase 1: Design Outputs

- `research.md`: technology and migration decisions with alternatives.
- `data-model.md`: runtime mode and job payload model definitions.
- `contracts/runtime-job-contract.md`: runtime-neutral payload contract.
- `contracts/worker-runtime-contract.md`: startup/runtime mode/health-check contract.
- `contracts/requirements-traceability.md`: `DOC-REQ-*` mappings to FRs, implementation surfaces, and validation strategy.
- `quickstart.md`: local build/run/validation commands for homogeneous and mixed runtime fleets.

## Post-Design Constitution Re-check

- Runtime deliverables are explicit and include production file changes.
- Validation tasks are explicit and include unit-test command execution.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
