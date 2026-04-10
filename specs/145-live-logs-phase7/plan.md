# Implementation Plan: Live Logs Phase 7 Hardening and Rollback

**Branch**: `145-live-logs-phase7` | **Date**: 2026-04-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/145-live-logs-phase7/spec.md`

## Summary

Implement the remaining high-signal Phase 7 work that is still absent after the prior Live Logs slices: instrument the summary/history/stream router surfaces with operational metrics, add explicit owner-access regression coverage for `/observability/events`, and add a dedicated frontend rollback switch that disables structured-history loading while preserving the rest of the Live Logs panel lifecycle.

## Technical Context

**Language/Version**: Python 3.12, TypeScript, React 19  
**Primary Dependencies**: FastAPI task-run routers, MoonMind StatsD emitter, Workflow/feature flag settings, Mission Control task-detail page, Vitest, pytest  
**Storage**: Existing managed-run store, session store, artifact-backed event journals, shared spool transport, browser query cache  
**Testing**: `pytest`, `./tools/test_unit.sh`, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`  
**Target Platform**: Docker/Compose-hosted MoonMind API service plus Mission Control frontend  
**Project Type**: backend + frontend hardening slice  
**Performance Goals**: keep metrics best-effort, keep the current summary -> history/tail -> SSE lifecycle unchanged, and avoid adding extra network round trips when structured-history rollback is disabled  
**Constraints**: preserve existing route contracts, preserve current Live Logs viewer semantics, do not make metrics authoritative for success, and keep rollback behavior runtime-configurable  
**Scale/Scope**: Phase 7 operational hardening only; no new route family, no rewrite of the viewer, and no feature-flag removal

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The work instruments MoonMind-owned observability surfaces and preserves normalized router/frontend contracts.
- **II. One-Click Agent Deployment**: PASS. No new service or deployment dependency is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Metrics and rollback flags are expressed in MoonMind routes/config, not provider-native transports.
- **IV. Own Your Data**: PASS. Structured history and merged fallback remain MoonMind artifact-backed surfaces.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The slice strengthens existing contracts instead of adding a parallel observability API.
- **VII. Powerful Runtime Configurability**: PASS. The rollback path is an explicit feature flag instead of a code change.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay bounded to feature flags, task-run routers, task-detail logic, and tests.
- **IX. Resilient by Default**: PASS. Metrics are best-effort, denied requests are handled explicitly, and the browser can roll back to merged history without losing the panel.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Phase 7 work is isolated into a dedicated spec/plan/tasks slice.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs remain unchanged; rollout/hardening stays in this feature package.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The rollback switch reuses the existing merged-tail path rather than adding another legacy viewer.

## Project Structure

### Documentation

```text
specs/145-live-logs-phase7/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── live-logs-phase7-hardening.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
moonmind/config/settings.py                        # MODIFY: structured-history rollback feature flag
api_service/api/routers/task_dashboard_view_model.py # MODIFY: expose grouped Live Logs rollback config
api_service/api/routers/task_runs.py              # MODIFY: summary/history metrics and owner-access-safe instrumentation
frontend/src/entrypoints/task-detail.tsx          # MODIFY: skip structured-history fetch when rollback flag disables it
tests/unit/config/test_settings.py                # MODIFY: feature-flag coverage
tests/unit/api/routers/test_task_dashboard_view_model.py # MODIFY: runtime-config coverage
tests/unit/api/routers/test_task_runs.py          # MODIFY: metrics and ownership regression coverage
frontend/src/entrypoints/task-detail.test.tsx     # MODIFY: rollback-path browser coverage
```

**Structure Decision**: Keep the hardening slice on the existing backend router/config and task-detail entrypoint path. The rollout/rollback behavior should remain a config-driven extension of the current Live Logs lifecycle, not a new UI or API surface.

## Research

- The router already emits `livelogs.stream.connect`, `livelogs.stream.disconnect`, and `livelogs.stream.error`, but it does not emit summary latency or structured-history latency/source metrics.
- `/observability/events` already enforces observability access through `_require_observability_access`, yet explicit owner-based regression tests exist for summary and continuity routes but not for the events route.
- The frontend already supports `liveLogsSessionTimelineEnabled` and rollout scoping, but it has no dedicated kill switch for the `/observability/events` path once the session-aware timeline is enabled.
- The merged-tail fallback is already production-safe and stable, so Phase 7 rollback can reuse that path instead of inventing a second compatibility model.

## Data Model

- See [data-model.md](./data-model.md) for the router metric events, history source tags, and structured-history rollback flag behavior.

## Contracts

- [contracts/live-logs-phase7-hardening.md](./contracts/live-logs-phase7-hardening.md)

## Implementation Plan

1. Add failing backend tests for summary/history metrics and `/observability/events` owner access, plus failing frontend/runtime-config tests for the structured-history rollback flag.
2. Extend feature flag settings and dashboard runtime config to expose `liveLogsStructuredHistoryEnabled`.
3. Instrument `task_runs.py` so summary and history routes emit best-effort latency/source/error metrics while preserving existing SSE metrics.
4. Update `task-detail.tsx` so Live Logs skips `/observability/events` and uses merged history directly when the rollback flag is disabled.
5. Run focused UI/backend verification plus scope validation, then mark all completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
2. `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py`
3. `SPECIFY_FEATURE=145-live-logs-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
4. `SPECIFY_FEATURE=145-live-logs-phase7 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

### Manual Validation

1. Disable structured history in runtime config and confirm Live Logs loads merged history without requesting `/observability/events`.
2. Re-enable structured history and confirm the panel returns to `/observability/events` first.
3. Observe emitted StatsD traffic or patched test doubles and confirm summary/history/stream router metrics fire on success and error paths.

## Complexity Tracking

No constitution violations or complexity exceptions are required for this slice.
