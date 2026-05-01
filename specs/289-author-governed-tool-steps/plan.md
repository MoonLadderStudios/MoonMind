# Implementation Plan: Author Governed Tool Steps

**Branch**: `289-author-governed-tool-steps` | **Date**: 2026-05-01 | **Spec**: `specs/289-author-governed-tool-steps/spec.md`
**Input**: Single-story runtime spec from `specs/289-author-governed-tool-steps/spec.md`

## Summary

Add governed Tool authoring affordances to the existing Create page Tool panel: trusted tool discovery, searchable grouped choices, visible contract metadata, and dynamic Jira target-status options sourced through the trusted MCP tool call surface. Existing manual Tool id/version/JSON authoring and backend executable-step contract validation remain the fallback and submission boundary. Unit contract coverage already exists for forbidden executable fields; this story requires focused frontend integration tests and implementation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | Tool panel currently uses manual id input only in `frontend/src/entrypoints/task-create.tsx` | fetch `/mcp/tools` and expose discovered tools | frontend integration |
| FR-002 | implemented_unverified | manual fields and invalid JSON tests exist in `frontend/src/entrypoints/task-create.test.tsx` | preserve fallback and add discovery-failure test | frontend integration |
| FR-003 | missing | no grouped/searchable tool choices found | add search and grouped choice rendering | frontend integration |
| FR-004 | missing | no discovered tool selection flow found | selecting a tool populates id and preserves inputs | frontend integration |
| FR-005 | missing | no dynamic target-status option flow found | call trusted `jira.get_transitions` via `/mcp/tools/call` for Jira transition tool | frontend integration |
| FR-006 | missing | existing manual JSON updates only | update JSON inputs from selected trusted target status without transition IDs | frontend integration |
| FR-007 | partial | Tool copy exists and Script is absent in existing tests | add contract metadata copy while preserving terminology | frontend integration |
| FR-008 | implemented_verified | `task-create.test.tsx` validates Tool payload and `test_task_contract.py` rejects conflicting payloads | preserve with targeted and final tests | frontend + unit |
| SC-001 | missing | no grouped/filter test | add test | frontend integration |
| SC-002 | missing | no dynamic status submission test | add test | frontend integration |
| SC-003 | missing | no discovery/transition failure fallback test | add test | frontend integration |
| SC-004 | implemented_verified | `tests/unit/workflows/tasks/test_task_contract.py` rejects skill payload and shell-like fields | rerun focused unit test | unit |
| SC-005 | missing | new spec artifacts needed | preserve traceability in artifacts and verification | artifact review |
| DESIGN-REQ-007 | partial | manual Tool payload exists but contract metadata not visible | display trusted contract metadata | frontend integration |
| DESIGN-REQ-008 | missing | no grouped search or dynamic options | implement grouped search and Jira status options | frontend integration |
| DESIGN-REQ-019 | partial | Tool terminology exists | preserve Tool terminology in new UI | frontend integration |
| DESIGN-REQ-020 | implemented_verified | shell-like fields rejected in task contract tests | rerun focused unit test | unit |

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
