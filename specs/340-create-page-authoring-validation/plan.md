# Implementation Plan: Create Page Authoring Validation

**Branch**: `340-create-page-authoring-validation` | **Date**: 2026-05-12 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/340-create-page-authoring-validation/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not be used because the managed branch name `change-jira-issue-mm-641-to-status-in-pr-68d46db2` is not numeric-prefixed. Planning used `.specify/feature.json` and the existing feature directory instead.

## Summary

MM-641 requires the Create page to keep task authoring task-first while moving Repository, Branch, and Publish Mode into the Steps card, preserving current publish semantics and canonical task-shaped submission. Repo gap analysis found strong existing coverage for canonical `task.git.branch` and legacy `targetBranch` rejection, plus existing Create page validation for repository, runtime, publish mode, dependencies, attachments, presets, and Jira inputs. The main implementation gap is visual and structural: current frontend tests assert the three controls remain in the bottom submit/floating bar and outside the Steps section. The plan is to write failing frontend unit tests for the new Steps-card placement and integrated submission behavior, adjust the Create page layout without changing payload semantics, and add backend/Temporal regression coverage only where payload boundaries could drift.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/entrypoints/task-create.tsx` presents task-focused create flow; `docs/Tasks/TaskArchitecture.md` defines task-first authoring | preserve task-first wording while moving controls; add scenario coverage | unit |
| FR-002 | partial | Create page state includes repository, branch, publish mode, presets, Jira, attachments, dependencies, runtime in `task-create.tsx`; existing tests cover each in isolation | prove the combined draft remains coherent after layout change | unit |
| FR-003 | partial | `task-create.tsx` validates repository/runtime/publish/attachments/dependencies; backend validates task payload in `api_service/api/routers/executions.py` | add integrated invalid-draft tests for controls after relocation | unit + integration |
| FR-004 | partial | Controls are together, but current tests place them in `.queue-floating-bar` and outside `data-canonical-create-section="Steps"` | move Repository, Branch, and Publish Mode into the Steps card and update layout tests | unit |
| FR-005 | implemented_unverified | attachment target handling exists in `task-create.tsx`; tests around objective and step attachments exist | preserve behavior through relocated controls and add regression coverage where needed | unit |
| FR-006 | partial | user-facing validation messages exist for repository, runtime, publish, attachment, and dependency failures | add negative tests that assert invalid drafts are blocked after relocation | unit + integration |
| FR-007 | partial | frontend builds `payload.task`; backend normalizes `authoredPresets`, attachments, runtime, publish, steps, dependencies, and git | add integrated submission test covering presets/Jira, attachments, dependencies, and branch together | unit + integration |
| FR-008 | implemented_verified | frontend test asserts `task.git == { branch }` and no `targetBranch`; backend rejects `payload.task.git.targetBranch`; integration tests assert no `targetBranch` | keep invariant and include final regression in story validation | none beyond final verify |
| FR-009 | partial | Publish Mode already submits as `task.publish.mode`; current placement is outside Steps | move control without changing submitted `publish.mode`; add before/after payload test | unit |
| FR-010 | implemented_unverified | branch lookup and publish-mode code exist; branch field feeds `task.git.branch` | re-verify branch and publish-mode combinations after control relocation | unit + integration |
| FR-011 | implemented_unverified | `authoredPresets`, applied templates, Jira provenance, and step source metadata are already passed through in frontend/backend code and tests | add combined authoring provenance regression for relocated layout | unit |
| FR-012 | implemented_verified | `spec.md` preserves `MM-641` and the original Jira preset brief | preserve key through remaining artifacts and final verification | final verify |
| SCN-001 | partial | existing layout test asserts controls are in floating submit bar, not Steps card | replace with Steps-card placement assertion | unit |
| SCN-002 | partial | validation code exists for invalid inputs | add invalid-draft scenario coverage post-relocation | unit + integration |
| SCN-003 | implemented_verified | existing frontend/backend/integration tests prove `task.git.branch` and no `targetBranch` | keep as regression evidence | final verify |
| SCN-004 | partial | individual preset/Jira/attachment/dependency tests exist | add combined draft normalization test | unit + integration |
| SCN-005 | implemented_unverified | attachment target tests exist | re-run with new layout and add coverage if selectors changed | unit |
| SC-001 | missing | current test expects controls outside Steps | implement and test Steps-card placement | unit |
| SC-002 | partial | invalid draft coverage exists but not the combined MM-641 matrix | add representative invalid matrix | unit + integration |
| SC-003 | implemented_verified | existing tests cover canonical branch payload | no new implementation | final verify |
| SC-004 | partial | existing tests cover parts of presets/Jira/attachments/dependencies | add combined preservation test | unit + integration |
| SC-005 | implemented_unverified | spec preserves source refs; later artifacts not yet complete | preserve traceability through plan/tasks/verification | final verify |
| DESIGN-REQ-001 | implemented_unverified | Create page is task-oriented; current layout still needs MM-641 verification | preserve task-first behavior and verify after layout change | unit |
| DESIGN-REQ-002 | partial | validation and coherent draft handling exist across frontend/backend | expand coverage for combined draft validation | unit + integration |
| DESIGN-REQ-003 | partial | controls are grouped together but not inside Steps card | move controls into Steps card | unit |
| DESIGN-REQ-004 | implemented_verified | canonical branch payload and `targetBranch` rejection are covered | keep invariant | final verify |
| DESIGN-REQ-005 | implemented_unverified | metadata and attachment references are preserved in code and tests | verify combined provenance after layout change | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control Create page  
**Primary Dependencies**: React, TanStack Query, existing Mission Control stylesheet, FastAPI/Pydantic v2/SQLAlchemy/Temporal boundaries for submission normalization  
**Storage**: Existing execution records and artifact metadata/content stores only; no new persistent storage  
**Unit Testing**: Vitest/Testing Library via `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`; final unit suite through `./tools/test_unit.sh`  
**Integration Testing**: Pytest hermetic integration via `./tools/test_integration.sh`; targeted Temporal/API integration tests for task-shaped submission normalization when backend boundary evidence changes  
**Target Platform**: Mission Control web UI and MoonMind API running in the existing Docker Compose/local development environment  
**Project Type**: Web application with FastAPI backend and React frontend  
**Performance Goals**: No measurable runtime degradation in Create page authoring; branch lookup and submit interactions remain responsive under existing query behavior  
**Constraints**: Do not introduce new storage; do not change Publish Mode semantics; do not reintroduce `targetBranch`; keep browser clients behind MoonMind APIs; preserve `MM-641` traceability  
**Scale/Scope**: One Create page story covering layout, validation, and task-shaped submission behavior for one task draft at a time

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The feature adjusts task authoring UI and submission validation without adding a new agent runtime or cognitive engine.
- II. One-Click Agent Deployment: PASS. No deployment topology or mandatory external service changes are planned.
- III. Avoid Vendor Lock-In: PASS. Repository/Jira/GitHub interactions continue through existing MoonMind-owned abstractions and UI data sources.
- IV. Own Your Data: PASS. Draft data and artifacts remain in existing MoonMind-controlled submission/artifact paths.
- V. Skills Are First-Class and Easy to Add: PASS. Preset and Jira-derived authoring metadata remain represented as task/preset data rather than custom one-off workflow UI.
- VI. Replaceable Scaffolding, Thick Contracts: PASS. Work strengthens Create page and task payload contracts with tests.
- VII. Powerful Runtime Configurability: PASS. Existing runtime, repository, branch, publish, and attachment settings remain configuration/request-driven.
- VIII. Modular and Extensible Architecture: PASS. Changes are scoped to Create page UI, existing submission normalization, and tests.
- IX. Resilient by Default: PASS. Task payload boundaries remain explicit and validation fails before execution receives invalid data.
- X. Facilitate Continuous Improvement: PASS. Artifacts preserve traceability and final verification evidence.
- XI. Spec-Driven Development Is the Source of Truth: PASS. This plan follows `spec.md` and preserves requirements/status evidence before implementation.
- XII. Canonical Documentation Separation: PASS. Implementation backlog lives in this feature directory; canonical docs are treated as desired-state source requirements.

Post-design re-check: PASS. Phase 1 artifacts introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/340-create-page-authoring-validation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-authoring-validation.md
└── tasks.md              # created later by moonspec-tasks
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

api_service/api/routers/
├── executions.py
├── task_dashboard.py
└── task_dashboard_view_model.py

tests/unit/api/routers/
├── test_executions.py
├── test_task_dashboard.py
└── test_task_dashboard_view_model.py

tests/integration/
├── api/test_task_contract_normalization.py
└── temporal/test_task_shaped_submission_normalization.py

docs/
├── Tasks/TaskArchitecture.md
└── UI/CreatePage.md
```

**Structure Decision**: Use the existing Mission Control Create page entrypoint and existing API normalization tests. Add or update tests in place; do not create a parallel Create page or new backend submission route.

## Complexity Tracking

No constitution violations or complexity exceptions are required.
