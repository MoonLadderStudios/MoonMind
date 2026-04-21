# Implementation Plan: Show Recent Manifest Runs

**Branch**: `217-show-recent-manifest-runs` | **Date**: 2026-04-21 | **Spec**: `specs/217-show-recent-manifest-runs/spec.md`
**Input**: `specs/217-show-recent-manifest-runs/spec.md`

## Summary

MM-421 extends the existing unified Manifests page by making Recent Runs a useful manifest execution history surface below the Run Manifest form. Existing code already requests `/api/executions?entry=manifest&limit=200` and places Recent Runs below the form, but it only renders task ID, source label, and status. The implementation will add UI-only normalization, display columns, filters, fallback values, and accessible row actions in `frontend/src/entrypoints/manifests.tsx`, with Vitest/Testing Library coverage in `frontend/src/entrypoints/manifests.test.tsx`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/manifests.tsx` renders Recent Runs after Run Manifest; existing test checks both headings | Preserve behavior | final validation |
| FR-002 | implemented_verified | `frontend/src/entrypoints/manifests.tsx` fetches `/executions?entry=manifest&limit=200`; existing tests mock `/api/executions?entry=manifest&limit=200` | Preserve behavior | final validation |
| FR-003 | partial | Current table lacks manifest/action/start/duration/detail action columns | Add row normalization and columns | frontend unit + runner-integrated UI |
| FR-004 | missing | Current status column does not combine active status with stage | Add stage-aware status display | frontend unit + runner-integrated UI |
| FR-005 | partial | Current Zod schema tolerates only limited fields; optional display fallbacks are not comprehensive | Add optional field parsing and fallback formatters | frontend unit |
| FR-006 | missing | No status, manifest, or search filters exist | Add lightweight filters | frontend unit + runner-integrated UI |
| FR-007 | partial | Current empty message says `No manifest runs found.` but does not point to the form above | Update empty state | frontend unit |
| FR-008 | implemented_unverified | Existing DataTable supports table layout; no story-specific narrow viewport assertion | Keep table readable and avoid hiding identity/status/action | final validation |
| FR-009 | partial | Existing form labels are accessible; Recent Runs filters do not exist yet and row action labels are absent | Add labeled filters and named actions | frontend unit + runner-integrated UI |
| FR-010 | implemented_verified | `spec.md` and Jira input preserve MM-421 | Preserve in artifacts and final report | final verification |
| DESIGN-REQ-001 | implemented_verified | Existing page order in `frontend/src/entrypoints/manifests.tsx` | Preserve behavior | final validation |
| DESIGN-REQ-002 | implemented_verified | Existing query path | Preserve behavior | final validation |
| DESIGN-REQ-003 | partial | Existing table cannot answer stage, action, timing, and details questions | Add history columns and detail action | frontend unit + runner-integrated UI |
| DESIGN-REQ-004 | partial | Existing table lacks recommended columns | Add available columns and fallbacks | frontend unit + runner-integrated UI |
| DESIGN-REQ-005 | missing | No stage-aware status detail | Add status-stage formatter | frontend unit |
| DESIGN-REQ-006 | missing | No filters | Add status/manifest/search filters | frontend unit + runner-integrated UI |
| DESIGN-REQ-007 | partial | Empty message lacks guidance | Update empty message | frontend unit |
| DESIGN-REQ-008 | implemented_unverified | Existing table uses shared responsive styling; story does not require new custom layout | Preserve readable columns and action link | final validation |
| DESIGN-REQ-009 | partial | New filters/actions need accessible labels | Add accessible labels/names | frontend unit + runner-integrated UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but no backend code is expected.
**Primary Dependencies**: React, TanStack Query, Zod, existing `DataTable`, Vitest, Testing Library.
**Storage**: No new persistent storage.
**Unit Testing Tool**: Vitest through `frontend/vite.config.ts`.
**Integration Testing Tool**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx` runner-integrated UI test path.
**Target Platform**: Mission Control browser UI.
**Project Type**: Frontend entrypoint within existing FastAPI-served dashboard.
**Performance Goals**: Filtering 200 returned rows happens locally without expensive UI constructs.
**Constraints**: Use the existing `/api/executions?entry=manifest&limit=200` endpoint; do not redesign backend history APIs; preserve MM-421 traceability.
**Scale/Scope**: One `/tasks/manifests` story; no saved manifest registry or run detail redesign.

## Constitution Check

- Orchestrate, don't recreate: PASS. Reuses existing execution history endpoint and dashboard components.
- One-click deployment: PASS. No new service dependency.
- Runtime configurability: PASS. No hardcoded service URL beyond the explicit existing endpoint suffix joined with `payload.apiBase`.
- Modular/extensible architecture: PASS. Changes stay inside the manifests entrypoint and tests.
- Resilient by default: PASS. Optional fields degrade to placeholders instead of crashing.
- Testing discipline: PASS. Unit and runner-integrated UI tests are required before final verification.
- Documentation separation: PASS. Canonical design remains in `docs/UI/ManifestsPage.md`; this implementation plan stays in `specs/`.

## Project Structure

```text
frontend/src/entrypoints/manifests.tsx
frontend/src/entrypoints/manifests.test.tsx
frontend/src/components/tables/DataTable.tsx
docs/UI/ManifestsPage.md
specs/217-show-recent-manifest-runs/
```

## Complexity Tracking

No constitution violations or additional complexity are required.
