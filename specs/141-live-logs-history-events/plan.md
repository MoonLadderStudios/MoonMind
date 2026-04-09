# Implementation Plan: live-logs-history-events

**Branch**: `141-live-logs-history-events` | **Date**: 2026-04-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/141-live-logs-history-events/spec.md`

## Summary

Implement the Phase 3 structured-history slice by hardening the existing task-run observability APIs instead of inventing a new surface. The work will extend `/api/task-runs/{id}/observability/events` with the missing query contract (`since`, `limit`, stream filters, kind filters), keep `/logs/stream` on the canonical `RunObservabilityEvent` payload shape, and ensure `/observability-summary` returns a truthful session snapshot and live-stream status for both active and completed runs. The existing merged-log endpoint remains the human-readable fallback path.

## Technical Context

**Language/Version**: Python 3.12, TypeScript consumer unchanged for this slice  
**Primary Dependencies**: FastAPI task-runs router, `ManagedRunStore`, `ManagedSessionStore`, `RunObservabilityEvent`, `SpoolLogReader`, task-detail Live Logs consumer expectations  
**Storage**: file-backed managed-run/session JSON records plus artifact-backed `observability.events.jsonl` history and existing stdout/stderr/diagnostics artifacts  
**Testing**: pytest unit tests via `./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind API and worker services  
**Project Type**: backend observability API contract hardening  
**Performance Goals**: bounded filtered history reads for up to the route limit without regressing current artifact-first fallback behavior  
**Constraints**: keep the existing route path, keep `/logs/merged` stable, preserve shared run authorization, do not add a second observability event model, do not require frontend phase-4 rendering changes  
**Scale/Scope**: Phase 3 only; no new frontend timeline renderer work and no session-plane producer changes beyond verifying SSE contract compatibility

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The work strengthens MoonMind-owned observability surfaces rather than exposing provider-native history payloads.
- **II. One-Click Agent Deployment**: PASS. No new infrastructure or dependency is added; this stays within the current API/store/artifact stack.
- **III. Avoid Vendor Lock-In**: PASS. The route contract stays on `RunObservabilityEvent`, which remains runtime-neutral.
- **IV. Own Your Data**: PASS. Historical timeline loading continues to prefer MoonMind-owned durable journals and artifact-backed fallbacks.
- **V. Skills Are First-Class and Easy to Add**: PASS. No agent-skill runtime or resolution behavior changes are introduced.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. This slice is contract hardening on top of existing APIs, not a new parallel observability mechanism.
- **VII. Powerful Runtime Configurability**: PASS. Existing live-log transport and timeline rollout flags remain unchanged.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay inside the task-runs observability router and its helper functions.
- **IX. Resilient by Default**: PASS. Structured history continues to degrade through spool and artifact-backed fallbacks for older runs.
- **XI. Spec-Driven Development**: PASS. Phase 3 is isolated in its own spec/plan/tasks slice before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs remain declarative; only spec artifacts describe this implementation slice.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The route hardening reuses the existing canonical event model and removes ad hoc retrieval assumptions directly.

## Research

- See [research.md](./research.md) for the decisions that keep Phase 3 additive and aligned with the shipped Phase 1/2 backend.
- The current code already exposes `/observability/events`, `/observability-summary`, and `/logs/stream`, so the gap is contract completeness, not route existence.
- Mission Control already requests `/observability/events` before `/logs/merged`, which means backend correctness on filtering, fallback, and summary truthfulness is the main blocker for this slice.

## Project Structure

### Documentation (this feature)

```text
specs/141-live-logs-history-events/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── observability-history-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── task_runs.py

moonmind/
├── observability/
│   └── transport.py
└── schemas/
    └── agent_runtime_models.py

tests/
└── unit/
    └── api/
        └── routers/
            └── test_task_runs.py
```

**Structure Decision**: This slice is intentionally router-centric. The canonical event model already exists in `moonmind/schemas/agent_runtime_models.py`, and the current consumer contract already flows through [`task_runs.py`](../../api_service/api/routers/task_runs.py). The implementation should only touch lower layers if a router helper cannot express the required filtering or normalization cleanly.

## Data Model

- See [data-model.md](./data-model.md) for the historical query parameters, response envelope, session snapshot precedence, and fallback ordering semantics.

## Contracts

- [contracts/observability-history-contract.md](./contracts/observability-history-contract.md)

## Implementation Plan

1. Add failing router tests for the missing Phase 3 contract:
   - `/observability/events` supports `since`, `limit`, stream filters, and kind filters,
   - durable event journals remain the preferred source,
   - summary returns a truthful session snapshot and live-stream status,
   - `/logs/stream` remains compatible with `RunObservabilityEvent`.
2. Extend the task-runs observability helpers so historical loading can filter by sequence, stream, and kind after the canonical event normalization step.
3. Keep the durable-source priority explicit: event journal first, spool second, artifact synthesis last.
4. Tighten summary shaping so record-backed and session-backed session snapshots remain truthful for active and completed runs.
5. Verify the SSE route still serializes the canonical event contract and does not drift from the historical schema.
6. Run focused router tests, Spec Kit scope validation, and the full unit suite, then mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py`
2. `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Request `/api/task-runs/{id}/observability/events` with and without `since`, stream, and kind filters and confirm the response shape stays on the canonical event contract.
2. Request `/api/task-runs/{id}/observability-summary` for an active run, a completed run, and a run with only record-backed session fields; confirm the session snapshot and live-stream status are truthful.
3. Open the task detail page for an active run and confirm Live Logs still loads history first, then attaches `/logs/stream` without a schema mismatch.
