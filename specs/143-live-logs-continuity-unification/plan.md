# Implementation Plan: Live Logs Continuity Unification

**Branch**: `143-live-logs-continuity-unification` | **Date**: 2026-04-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/143-live-logs-continuity-unification/spec.md`

## Summary

Implement Phase 5 of the Live Logs session-aware plan by tightening the seam between timeline rows and continuity artifacts. The implementation will keep the current Live Logs timeline and Session Continuity panel, preserve the projection as the artifact drill-down source, add inline artifact links for session publication/reset events, and update task-detail copy so operators understand the difference between the timeline and durable evidence.

## Technical Context

**Language/Version**: Python 3.12, TypeScript, React 19, Vitest  
**Primary Dependencies**: existing task-run observability router, existing task-detail timeline viewer, existing session projection endpoint  
**Storage**: no new persistent store; reuse observability event metadata and grouped session projection metadata  
**Testing**: `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`  
**Target Platform**: FastAPI task-run APIs and Mission Control task detail page  
**Project Type**: backend/frontend observability UX refinement  
**Constraints**: keep the current Session Continuity control flow intact, avoid adding a new route unless event metadata is insufficient, and preserve legacy fallback behavior for older observability events

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The UI continues to consume MoonMind-owned observability and artifact contracts.
- **II. One-Click Agent Deployment**: PASS. No deployment or runtime prerequisites change.
- **III. Avoid Vendor Lock-In**: PASS. Artifact links remain tied to MoonMind artifact refs rather than provider-native session state.
- **IV. Own Your Data**: PASS. The timeline links directly into durable MoonMind artifacts.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The change tightens existing metadata contracts instead of introducing a second continuity transport.
- **VII. Powerful Runtime Configurability**: PASS. The richer timeline behavior remains behind the existing session-timeline frontend path.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay bounded to `task_runs.py`, task-detail rendering, and tests.
- **IX. Resilient by Default**: PASS. Older runs still degrade through generic `artifactRef` metadata or existing continuity drill-down.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This slice gets a dedicated spec/plan/tasks package before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. This implementation plan references the canonical docs and applies only the Phase 5 rollout slice.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The work reuses the current timeline and projection surfaces without adding compatibility wrappers.

## Project Structure

### Documentation

```text
specs/143-live-logs-continuity-unification/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── live-logs-continuity-unification.md
├── checklists/
│   └── requirements.md
├── speckit_analyze_report.md
└── tasks.md
```

### Source Code

```text
api_service/api/routers/task_runs.py          # MODIFY: preserve specific artifact-ref metadata for synthesized session rows
tests/unit/api/routers/test_task_runs.py      # MODIFY: regression coverage for historical event ref metadata
frontend/src/entrypoints/task-detail.tsx      # MODIFY: inline timeline artifact links and copy/label updates
frontend/src/entrypoints/task-detail.test.tsx # MODIFY: TDD coverage for artifact links and explanatory copy
frontend/src/styles/mission-control.css       # MODIFY: timeline artifact-link styling
```

## Research

- The Live Logs timeline already renders distinct session/publication/boundary row types and parses arbitrary event metadata, so Phase 5 can stay additive.
- The managed-session supervisor already writes specific metadata keys such as `summaryRef`, `checkpointRef`, `controlEventRef`, and `resetBoundaryRef` into persisted observability events.
- Historical fallback synthesis in `api_service/api/routers/task_runs.py` currently preserves only generic `artifactRef` values for publication/reset events, so backend normalization is the main contract gap for older runs.
- The Session Continuity panel already exposes grouped artifacts and latest refs, but it does not yet explain its drill-down role relative to Live Logs.

## Data Model

- See [data-model.md](./data-model.md) for the timeline-link ref precedence and continuity copy semantics.

## Contracts

- [contracts/live-logs-continuity-unification.md](./contracts/live-logs-continuity-unification.md)

## Implementation Plan

1. Add failing backend tests for synthesized publication/reset event metadata and failing frontend tests for timeline artifact links plus operator-facing copy.
2. Normalize synthesized historical event metadata in `task_runs.py` so the UI can read specific ref keys for summary/checkpoint/control/reset rows.
3. Update `task-detail.tsx` to derive inline artifact links from event metadata with a generic `artifactRef` fallback.
4. Update the Live Logs and Session Continuity panel copy to distinguish event history from durable drill-down evidence.
5. Add the minimal CSS needed for inline artifact-link presentation and rerun focused verification plus scope validation.

## Verification Plan

### Automated Tests

1. `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
2. `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
3. `SPECIFY_FEATURE=143-live-logs-continuity-unification ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
4. `SPECIFY_FEATURE=143-live-logs-continuity-unification ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

### Manual Validation

1. Open a managed Codex run with summary/checkpoint publications and confirm the timeline rows expose direct artifact links.
2. Trigger a clear/reset and confirm the clear/boundary rows expose both control-event and reset-boundary links.
3. Confirm the Live Logs panel explains timeline semantics while the continuity panel explains artifact drill-down semantics.
