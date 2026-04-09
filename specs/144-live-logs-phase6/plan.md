# Implementation Plan: Live Logs Phase 6 Compatibility and Cleanup

**Branch**: `144-live-logs-phase6` | **Date**: 2026-04-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/144-live-logs-phase6/spec.md`

## Summary

Implement the Phase 6 rollout and compatibility slice of the Live Logs session-aware migration by tightening the boundary between dashboard config, frontend viewer eligibility, and frontend observability-event normalization. This slice will 1) respect `liveLogsSessionTimelineRollout` when enabling the session-aware viewer, 2) fall back to merged logs when structured history is present but empty, and 3) normalize observability events from both history and SSE across camelCase, snake_case, and older minimal payload shapes so frontend and backend slices can roll independently for a short migration window.

## Technical Context

**Language/Version**: Python 3.12, TypeScript, React 19, Vitest  
**Primary Dependencies**: task dashboard runtime config helpers, task-detail Live Logs viewer, Zod schemas, existing `/api/task-runs/*` observability routes  
**Storage**: Browser query cache only; no new persistent store required  
**Testing**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `npm run ui:typecheck`, `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`, `pytest` unit coverage for dashboard config helpers  
**Target Platform**: Mission Control React frontend served by FastAPI task dashboard boot payload  
**Project Type**: frontend compatibility hardening plus dashboard runtime-config support  
**Performance Goals**: preserve the current summary -> history -> SSE lifecycle without extra round trips or duplicate timeline rows  
**Constraints**: keep `/logs/merged` stable, keep `/logs/stream` stable, avoid backend compatibility wrappers for event aliases, preserve older runs, keep rollout safety behind feature flags  
**Scale/Scope**: Phase 6 only; no new backend observability route family and no feature-flag removal

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The UI continues to consume MoonMind-owned observability contracts and normalizes only MoonMind payload aliases, not provider-native events.
- **II. One-Click Agent Deployment**: PASS. No new service, dependency, or deployment prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Rollout gating is expressed in MoonMind runtime config and normalized event shapes, not vendor-specific UI branches.
- **IV. Own Your Data**: PASS. Historical fallback remains artifact-backed through existing MoonMind routes.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The main work is contract hardening around the existing viewer and route payloads, not a new transport.
- **VII. Powerful Runtime Configurability**: PASS. The session-aware viewer now respects rollout scope instead of a coarse boolean only.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay bounded to runtime-config helpers, frontend normalization helpers, and tests.
- **IX. Resilient by Default**: PASS. Empty-history fallback and alias-tolerant parsing improve deployment safety for historical runs and mixed-version windows.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This rollout slice has dedicated spec/plan/tasks artifacts before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical Live Logs docs remain declarative; this plan covers the Phase 6 rollout slice only.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The cleanup consolidates the viewer onto one compatibility-aware normalization path instead of adding another long-lived model.

## Research

- The backend already exposes `liveLogsSessionTimelineRollout` and `liveLogsSessionTimelineEnabled` in the dashboard boot payload, but the frontend currently uses only the boolean and ignores rollout scope.
- The backend task-runs router normalizes observability events with Pydantic `by_alias=True`, so event payloads come back with camelCase session fields such as `sessionId`, while the frontend currently reads only snake_case keys such as `session_id`.
- The frontend already degrades to `/logs/merged` when `/observability/events` is missing or errors, but it does not fall back when the structured-history route succeeds with zero events and merged compatibility text still exists.
- The current Live Logs viewer already keeps the correct lifecycle order and already supports legacy line-view mode, which means Phase 6 is a compatibility hardening slice rather than a renderer rewrite.
- The runtime scope validation script requires at least one production change under `api_service/` or `moonmind/`, so the plan must include a concrete runtime-config helper change in addition to frontend/test work.

## Project Structure

### Documentation

```text
specs/144-live-logs-phase6/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── live-logs-phase6-compatibility.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
api_service/api/routers/task_dashboard_view_model.py  # MODIFY: centralize Live Logs feature config helpers used by dashboard consumers
tests/unit/api/routers/test_task_dashboard_view_model.py # MODIFY: rollout config coverage
frontend/src/entrypoints/task-detail.tsx             # MODIFY: rollout-aware eligibility, event alias normalization, empty-history fallback
frontend/src/entrypoints/task-detail.test.tsx        # MODIFY: TDD coverage for rollout scope, alias compatibility, empty-history fallback
```

**Structure Decision**: Keep Phase 6 bounded to the dashboard runtime config helper and the existing task-detail entrypoint. Do not add new routes or a second viewer entrypoint.

## Data Model

- See [data-model.md](./data-model.md) for the rollout eligibility inputs, compatibility-normalized event shape, and merged-fallback trigger rules.

## Contracts

- [contracts/live-logs-phase6-compatibility.md](./contracts/live-logs-phase6-compatibility.md)

## Implementation Plan

1. Add failing tests for the Phase 6 compatibility seam:
   - rollout scope eligibility in dashboard config and task-detail rendering,
   - empty structured-history fallback to merged logs,
   - camelCase and snake_case event alias handling in history and SSE,
   - graceful rendering of older minimal SSE payloads.
2. Refactor the dashboard runtime-config helper so grouped Live Logs feature flags stay centralized and testable for rollout-aware frontend consumption.
3. Introduce a compatibility-aware frontend normalization path in `task-detail.tsx` that:
   - computes timeline eligibility from rollout scope plus run context,
   - accepts both camelCase and snake_case event aliases,
   - treats empty structured history as fallback-eligible when merged content exists.
4. Keep the existing summary -> history -> SSE lifecycle intact while routing eligible runs to the timeline viewer and ineligible runs to the legacy line viewer.
5. Run focused UI/runtime-config verification, scope validation, and final UI regression commands, then mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
2. `npm run ui:typecheck`
3. `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
4. `SPECIFY_FEATURE=144-live-logs-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
5. `SPECIFY_FEATURE=144-live-logs-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
6. `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`

### Manual Validation

1. Open Live Logs for a Codex managed run with rollout `codex_managed` and confirm the session-aware timeline renders.
2. Open Live Logs for a non-Codex managed run with rollout `codex_managed` and confirm the legacy line viewer remains active.
3. Open a historical run where `/observability/events` returns an empty list but `/logs/merged` still has content and confirm the merged compatibility text is shown.
4. Stream one live event carrying camelCase session metadata and one carrying snake_case metadata and confirm the session snapshot header updates in both cases.
