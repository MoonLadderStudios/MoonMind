# Implementation Plan: Live Logs Phase 2 Active Tail

**Branch**: `148-live-logs-phase2-active-tail` | **Date**: 2026-04-10 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/148-live-logs-phase2-active-tail/spec.md`

## Summary

Make the existing Live Logs fetch path useful during active Codex managed-session runs by teaching the merged-log endpoint to render the structured observability event journal before falling back to spool or artifacts. Keep `/observability-summary` as the source for live-stream status and session snapshot metadata, and preserve the current SSE availability contract.

## Technical Context

**Language/Version**: Python 3.11+ backend, TypeScript/React frontend left unchanged for this slice  
**Primary Dependencies**: FastAPI router, `ManagedRunStore`, `RunObservabilityEvent`, workspace spool transport  
**Storage**: JSON managed-run records, artifact-root files, workspace-local `live_streams.spool`  
**Testing**: pytest through `./tools/test_unit.sh`; focused router unit tests during TDD  
**Target Platform**: Linux/Docker Compose MoonMind services and managed-agent workers  
**Project Type**: Web control-plane backend with Mission Control frontend consumer  
**Performance Goals**: Render active merged tail from bounded local files without blocking stream attach; preserve existing chunked response behavior  
**Constraints**: Artifact-first semantics; current UI lifecycle remains summary -> merged tail -> optional SSE; invalid rows must degrade through fallbacks  
**Scale/Scope**: One task-run observability surface, scoped to `api_service/api/routers/task_runs.py` and tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change projects MoonMind-owned observability records without changing Codex behavior.
- **II. One-Click Agent Deployment**: PASS. No new external service or secret requirement.
- **III. Avoid Vendor Lock-In**: PASS. The endpoint consumes normalized `RunObservabilityEvent` rows, not provider-native payloads.
- **IV. Own Your Data**: PASS. Active history is read from operator-controlled journals, spools, and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill contract change.
- **VI. Scientific Method / Tests Are the Anchor**: PASS. TDD router tests precede implementation.
- **VII. Runtime Configurability**: PASS. No new hardcoded operator policy.
- **VIII. Modular and Extensible Architecture**: PASS. Scope stays inside existing router/helper boundaries.
- **IX. Resilient by Default**: PASS. Missing or malformed active log rows degrade through fallbacks.
- **X. Facilitate Continuous Improvement**: PASS. Summary and merged views improve operator diagnosis.
- **XI. Spec-Driven Development**: PASS. This spec/plan/tasks set governs the runtime change.
- **XII. Canonical Documentation Separation**: PASS. No canonical docs are converted into migration checklists.
- **XIII. Pre-Release Velocity**: PASS. No compatibility aliases are introduced; existing internal behavior is updated directly.

## Project Structure

### Documentation (this feature)

```text
specs/148-live-logs-phase2-active-tail/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── task_runs.py

tests/
└── unit/
    └── api/
        └── routers/
            └── test_task_runs.py
```

**Structure Decision**: Use the existing task-run router and router unit-test suite because the feature changes current API response behavior and does not require frontend or adapter contract changes.

## Phase 0: Research

See [research.md](./research.md).

## Phase 1: Design & Contracts

See [data-model.md](./data-model.md), [contracts/active-tail.md](./contracts/active-tail.md), and [quickstart.md](./quickstart.md).

## Post-Design Constitution Check

- **Artifact-first observability** remains intact because journal, spool, final merged artifact, and split artifacts are all local durable or transport-owned MoonMind sources.
- **Resiliency** improves because the router skips invalid active rows and falls through to remaining evidence rather than treating one corrupt line as a failed log response.
- **No compatibility shim** is added; the merged endpoint's source preference is updated in place while retaining the existing route and response shape for the current UI.

No Complexity Tracking entries are required.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
