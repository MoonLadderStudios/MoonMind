# Implementation Plan: Author Governed Tool Steps

**Branch**: `289-author-governed-tool-steps` | **Date**: 2026-05-01 | **Spec**: `specs/289-author-governed-tool-steps/spec.md`
**Input**: Single-story runtime spec from `specs/289-author-governed-tool-steps/spec.md`

## Summary

Add governed Tool authoring affordances to the existing Create page Tool panel: trusted tool discovery, searchable grouped choices, visible contract metadata, and dynamic Jira target-status options sourced through the trusted MCP tool call surface. Existing manual Tool id/version/JSON authoring and backend executable-step contract validation remain the fallback and submission boundary. Current branch evidence shows the frontend authoring behavior and backend executable-step contract validation are implemented and covered by focused tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` fetches `/mcp/tools`; `frontend/src/entrypoints/task-create.test.tsx` covers trusted discovery | no new implementation | frontend integration |
| FR-002 | implemented_verified | discovery failure fallback test keeps manual Tool id/version/inputs available | no new implementation | frontend integration |
| FR-003 | implemented_verified | grouped/searchable Tool choices implemented in `task-create.tsx` and covered by focused UI test | no new implementation | frontend integration |
| FR-004 | implemented_verified | discovered Tool selection updates Tool id while preserving inputs; covered by focused UI test | no new implementation | frontend integration |
| FR-005 | implemented_verified | `task-create.tsx` calls `/mcp/tools/call` with `jira.get_transitions`; dynamic status test covers the flow | no new implementation | frontend integration |
| FR-006 | implemented_verified | target status selection updates Tool inputs JSON with `targetStatus`; dynamic status test verifies submitted payload | no new implementation | frontend integration |
| FR-007 | implemented_verified | Tool contract metadata copy exists without introducing Script as a Step Type concept; covered by focused UI tests and artifact review | no new implementation | frontend integration |
| FR-008 | implemented_verified | `task-create.test.tsx` validates Tool payload and `test_task_contract.py` rejects conflicting payloads | no new implementation | frontend + unit |
| SC-001 | implemented_verified | focused Create-page test verifies grouped/filterable trusted Tool choices | no new implementation | frontend integration |
| SC-002 | implemented_verified | focused Create-page test verifies Jira target status selection and submitted Tool payload | no new implementation | frontend integration |
| SC-003 | implemented_verified | focused Create-page test verifies discovery and transition failure fallback | no new implementation | frontend integration |
| SC-004 | implemented_verified | `tests/unit/workflows/tasks/test_task_contract.py` rejects skill payload and shell-like fields | no new implementation | unit |
| SC-005 | implemented_verified | `spec.md`, `plan.md`, `tasks.md`, and `verification.md` preserve MM-576 and source design IDs | no new implementation | artifact review |
| DESIGN-REQ-007 | implemented_verified | UI displays Tool contract metadata for schema-backed governed execution | no new implementation | frontend integration |
| DESIGN-REQ-008 | implemented_verified | UI supports search/grouping and trusted Jira dynamic target-status options | no new implementation | frontend integration |
| DESIGN-REQ-019 | implemented_verified | user-facing labels preserve Tool terminology and avoid Script Step Type language | no new implementation | frontend integration |
| DESIGN-REQ-020 | implemented_verified | task contract rejects shell-like executable fields; Tool UI does not add arbitrary shell fields | no new implementation | unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control Create page; Python 3.12/Pydantic v2 for existing task contract validation.
**Primary Dependencies**: React, TanStack Query, Vitest, Testing Library, existing FastAPI MCP endpoints.
**Storage**: No new persistent storage.
**Unit Testing**: `pytest tests/unit/workflows/tasks/test_task_contract.py -q`.
**Integration Testing**: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`.
**Target Platform**: Mission Control browser UI backed by existing MoonMind API routes.
**Project Type**: Existing web frontend with Python backend contract validation.
**Performance Goals**: Tool discovery and dynamic option fetches are best-effort UI calls; manual authoring remains responsive if unavailable.
**Constraints**: Use trusted `/mcp/tools` and `/mcp/tools/call`; do not use raw Jira credentials; preserve manual fallback; do not introduce arbitrary shell as a Step Type.
**Scale/Scope**: Single Create page Tool panel and focused tests.

## Constitution Check

| Principle | Status | Evidence |
| --- | --- | --- |
| I Orchestrate, Don't Recreate | PASS | Reuses MCP tool discovery/call surfaces rather than creating a new Jira client. |
| II One-Click Agent Deployment | PASS | No new required dependencies or persistent services. |
| III Avoid Vendor Lock-In | PASS | Jira dynamic option behavior is tool-specific and behind existing trusted tool surface. |
| IV Own Your Data | PASS | No external storage; data remains in existing control plane responses. |
| V Skills Are First-Class | PASS | Does not alter skill runtime semantics. |
| VI Scientific Method | PASS | TDD tasks include focused frontend and contract tests before code. |
| VII Runtime Configurability | PASS | No hardcoded secrets; endpoints are existing same-origin control-plane paths. |
| VIII Modular Architecture | PASS | Changes stay inside Create page UI and existing task contract tests. |
| IX Resilient by Default | PASS | Failures are visible and manual Tool authoring remains available. |
| X Continuous Improvement | PASS | Final verification produces structured outcome. |
| XI Spec-Driven Development | PASS | spec.md, plan.md, tasks.md, and verification artifacts are generated. |
| XII Canonical Docs Separation | PASS | Runtime rollout details stay in feature artifacts. |
| XIII Delete, Don't Deprecate | PASS | No compatibility aliases introduced. |

## Project Structure

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
tests/unit/workflows/tasks/test_task_contract.py
moonmind/workflows/tasks/task_contract.py
specs/289-author-governed-tool-steps/
```

## Complexity Tracking

No constitution violations or complexity exceptions.
