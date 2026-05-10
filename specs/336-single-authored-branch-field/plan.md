# Implementation Plan: Single Authored Branch Field

**Branch**: `change-jira-issue-mm-668-to-status-in-pr-d6e0f381` | **Date**: 2026-05-10 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/336-single-authored-branch-field/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not complete because the current branch name does not match the Speckit numeric branch convention. The active feature directory is resolved by `.specify/feature.json`, so the plan artifacts are generated directly in `specs/336-single-authored-branch-field/`.

## Summary

MM-668 requires MoonMind to finish the transition from legacy two-branch task authoring to one authored `git.branch` field while keeping legacy snapshots reconstructable and auditable. Current repo evidence shows the Create page already submits `task.git.branch` and strips `startingBranch`/`targetBranch` for direct authored submissions, and the API rejects some task-shaped `targetBranch` aliases. Gaps remain in legacy reconstruction and runtime planning: `frontend/src/lib/temporalTaskEditing.ts` still converts target-only legacy input into an active branch, and runtime worker preparation still accepts `task.git.targetBranch` as a branch input. The implementation should add tests first, then tighten reconstruction and runtime-preparation behavior without adding compatibility aliases.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` submits `git.branch`; `frontend/src/entrypoints/task-create.test.tsx` covers "loads branches through MoonMind and submits one authored branch" | preserve behavior | final verify |
| FR-002 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` deletes legacy branch fields during edit patch creation; create-page test asserts submitted task JSON excludes `targetBranch` and `startingBranch` | preserve behavior | final verify |
| FR-003 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` submits `publish.mode` and top-level `publishMode`; existing UI tests cover publish mode submission | preserve behavior | final verify |
| FR-004 | partial | API rejects task-shaped `targetBranch` aliases, but canonical task contract tests still normalize `task.git.targetBranch` to `branch`, and worker preparation reads `git.targetBranch` | remove active `targetBranch` acceptance from new authored/runtime planning paths; keep only historical/audit handling for legacy reconstruction | unit + integration |
| FR-005 | implemented_unverified | Create page validates publish mode and branch selection UI state, but no MM-668-specific proof that required branch intent cannot be derived from legacy fields | add focused validation coverage; implementation contingency if missing branch can still be masked by legacy fields | unit + integration |
| FR-006 | implemented_verified | `frontend/src/lib/temporalTaskEditing.ts` maps `startingBranch` to reconstructed `branch` when safe; existing tests cover legacy reconstruction warning and branch normalization | preserve behavior while adjusting target-only cases | unit regression |
| FR-007 | partial | legacy `targetBranch` exists in execution and artifact contracts, but current frontend reconstruction treats target-only values as active branch | represent `targetBranch` as historical metadata or warning context only; do not expose it as authored branch | unit + integration |
| FR-008 | partial | `frontend/src/lib/temporalTaskEditing.ts` and `moonmind/agents/codex_worker/worker.py` still allow `targetBranch` to influence active branch/work-branch decisions | remove active target-branch fallback from edit/rerun submission and runtime planning input; retain runtime-owned generated target/head branch metadata separately | unit + workflow-boundary/integration |
| FR-009 | implemented_unverified | two-branch branch-publish frontend reconstruction warning exists; no backend/runtime-boundary proof that equivalent legacy input warns instead of silently submitting | add focused tests for branch-publish legacy warning and runtime submission output | unit + integration |
| FR-010 | partial | frontend patch strips legacy fields, but task contract and runtime preparation still contain target-branch normalization/acceptance paths | fail fast or ignore as historical metadata for new active submissions; do not round-trip targetBranch into active task contract | unit + integration |
| FR-011 | implemented_unverified | frontend exposes `legacyBranchWarning`; no end-to-end proof that normalized vs unreconstructable legacy snapshots remain distinguishable through task editing | add UI reconstruction and API/rerun verification coverage | unit + integration |
| DESIGN-REQ-009 | partial | new Create-page path mostly satisfies single branch, but runtime planning still accepts `targetBranch` as input | complete runtime and contract cleanup | unit + integration |
| DESIGN-REQ-010 | partial | legacy `startingBranch` handling is present; target-only and runtime target-branch semantics need tightening | complete legacy metadata/warning behavior and runtime no-active-target behavior | unit + integration |
| SC-001 | partial | direct Create-page test covers one path; runtime-prepared inputs still need proof | add backend/runtime preparation assertions | integration |
| SC-002 | implemented_unverified | publish mode is present in UI payloads; needs MM-668-specific regression coverage during branch cleanup | add focused regression test | unit |
| SC-003 | implemented_verified | `startingBranch` normalization is covered in frontend reconstruction tests | preserve behavior | final verify |
| SC-004 | partial | target-only legacy case currently becomes active branch in frontend reconstruction | change behavior and prove historical-only handling | unit + integration |
| SC-005 | implemented_unverified | two-branch warning exists for frontend reconstruction; needs end-to-end evidence before task equivalence | add verification tests | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control task create/edit/rerun UI  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM where route persistence is involved, Temporal Python SDK activity/runtime boundaries, React, TanStack Query, Vitest/Testing Library  
**Storage**: Existing Temporal execution records, artifact-backed original task input snapshots, and task submission payloads only; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; focused frontend iteration via `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` or `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` after JS dependencies are prepared  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` tests; focused integration candidates under `tests/integration/api/` and `tests/integration/temporal/`  
**Target Platform**: MoonMind local/managed runtime on Linux containers, Mission Control browser UI  
**Project Type**: Full-stack web application plus Temporal-managed runtime worker  
**Performance Goals**: No measurable latency change; branch normalization and warning logic should remain synchronous and bounded to task payload size  
**Constraints**: No raw credentials in logs/artifacts; no new storage; pre-release compatibility policy requires removing superseded internal aliases rather than adding hidden fallback behavior; Temporal-facing payload changes need boundary coverage or explicit cutover notes  
**Scale/Scope**: One independently testable story covering branch authoring, legacy reconstruction, and runtime planning seams for task submissions

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Changes stay in task contract, UI, API, and runtime adapter boundaries; no new agent cognition layer. |
| II. One-Click Agent Deployment | PASS | No new external services or mandatory cloud dependencies. |
| III. Avoid Vendor Lock-In | PASS | Branch semantics remain provider-neutral; provider-specific head branch handling stays behind runtime boundaries. |
| IV. Own Your Data | PASS | Legacy branch metadata remains in local artifacts/execution records only; no external storage added. |
| V. Skills Are First-Class and Easy to Add | PASS | No skill runtime or skill source mutation is planned. |
| VI. Replaceable Scaffolding | PASS | Tightens contracts and tests so old branch scaffolding can be removed cleanly. |
| VII. Runtime Configurability | PASS | No new config required; existing task payload precedence remains explicit. |
| VIII. Modular and Extensible Architecture | PASS | Work is scoped to existing create/edit helpers, task contract normalization, and runtime preparation boundaries. |
| IX. Resilient by Default | PASS | Runtime boundary tests are required for branch payload compatibility and deterministic warning/fail-fast behavior. |
| X. Continuous Improvement | PASS | Plan preserves traceability and verification evidence for later publish/learn stages. |
| XI. Spec-Driven Development | PASS | `spec.md` exists and this plan captures requirement status before tasks. |
| XII. Canonical Documentation Separation | PASS | Migration/backlog details live in this feature plan; canonical docs remain declarative source requirements. |
| XIII. Pre-Release Velocity | PASS | Planned work removes superseded internal `targetBranch` active-input behavior rather than adding compatibility aliases. |

## Project Structure

### Documentation (this feature)

```text
specs/336-single-authored-branch-field/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── single-authored-branch-contract.md
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
frontend/src/
├── entrypoints/
│   ├── task-create.tsx
│   └── task-create.test.tsx
└── lib/
    └── temporalTaskEditing.ts

api_service/api/routers/
└── executions.py

moonmind/
├── agents/codex_worker/worker.py
├── schemas/
│   ├── temporal_models.py
│   └── temporal_activity_models.py
└── workflows/tasks/
    └── task_contract.py

tests/
├── integration/
│   ├── api/test_task_contract_normalization.py
│   └── temporal/test_task_shaped_submission_normalization.py
└── unit/
    ├── api/routers/test_executions.py
    ├── workflows/tasks/test_task_contract.py
    └── agents/codex_worker/test_worker.py
```

**Structure Decision**: Use existing frontend task-create tests for authored and reconstructed draft behavior, existing API/task-contract tests for task-shaped submission normalization, and existing worker/runtime tests for runtime preparation semantics.

## Complexity Tracking

No constitution violations require justification.
