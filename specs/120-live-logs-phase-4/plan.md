# Implementation Plan: live-logs-phase-4

**Branch**: `120-live-logs-phase-4-affordances` | **Date**: 2026-03-31 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/120-live-logs-phase-4/spec.md`

## Summary

Implement Phase 4 Mission Control observability affordances on the task detail page by making the Live Logs viewer artifact-first, expanding observability into separate stdout/stderr/diagnostics panels, and tightening client lifecycle behavior around collapse, ended runs, and page visibility.

## Technical Context

**Language/Version**: TypeScript + React 19
**Primary Dependencies**: React, TanStack Query, Vite/Vitest
**Testing**: `npm run ui:test`, plus repo-approved unit validation via `./tools/test_unit.sh` when touched Python scope requires it
**Project Type**: Frontend entrypoint consumed by Mission Control
**Constraints**: Reuse existing repo dependencies; do not add `@types/anser` because that package does not exist and this phase does not require new viewer dependencies

## Constitution Check

*GATE: Must pass before implementation and after design updates.*

- **I. Orchestrate, Don't Recreate**: PASS. The UI consumes MoonMind observability APIs rather than introducing a parallel terminal or provider-specific control path.
- **II. One-Click Agent Deployment**: PASS. The work stays within existing frontend dependencies and repo-managed scripts.
- **VIII. Modular and Extensible Architecture**: PASS. The task-detail entrypoint remains the presentation layer over stable observability endpoints.
- **IX. Resilient by Default**: PASS. Artifact-backed initial load and degraded states remain available even when live streaming is unavailable or interrupted.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The spec, plan, and tasks are aligned to the implemented Phase 4 scope.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. This phase removes thin SSE-only assumptions rather than preserving duplicate UI paths.

## Scope

### In Scope

- Live Logs panel lifecycle and viewer-state behavior
- Artifact-backed initial merged-tail loading
- SSE follow mode for active runs
- Per-line provenance rendering
- Stdout, stderr, and diagnostics panels
- Wrap/copy/download affordances
- Frontend tests covering collapse, reconnection, ended runs, and panel behavior

### Out of Scope

- Backend streaming transport redesign
- New terminal emulation or OAuth terminal work
- Adding virtualization or ANSI-rendering dependencies not required by the implemented Phase 4 slice

## Structure Decision

- Keep the implementation in `frontend/src/entrypoints/task-detail.tsx` because the feature is scoped to Mission Control task detail rendering.
- Keep coverage in `frontend/src/entrypoints/task-detail.test.tsx` so lifecycle and observability behaviors stay exercised at the entrypoint boundary.
- Keep feature documentation under `specs/120-live-logs-phase-4/` synchronized with the actual implementation.

## Verification Plan

### Automated Tests

1. Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` to verify the frontend entrypoint behavior for this phase.
2. Run `./tools/test_unit.sh --python-only tests/unit/schemas/test_agent_runtime_models.py tests/unit/schemas/test_agent_runtime_models_boundary.py` only when touched Python scope requires it.

### Manual Validation

1. Open a task detail page with a task run id and confirm Live Logs remains collapsed until expanded.
2. Confirm stdout, stderr, and diagnostics panels load independently and expose wrap/copy/download controls.
3. Confirm live streaming shuts down on collapse or tab hide and resumes only when appropriate.
