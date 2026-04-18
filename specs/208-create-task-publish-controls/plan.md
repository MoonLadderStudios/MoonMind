# Implementation Plan: Create Task Publish Controls

**Branch**: `208-create-task-publish-controls` | **Date**: 2026-04-18 | **Spec**: `specs/208-create-task-publish-controls/spec.md`  
**Input**: Single-story feature specification from `specs/208-create-task-publish-controls/spec.md`

## Summary

Implement MM-412 by converting merge automation from a separate Execution context checkbox into a PR-specific Publish Mode choice inside the existing Steps-card publish control group. The code already has repository, Branch, and Publish Mode in the Steps card, so the remaining runtime work is to introduce a UI-layer combined publish selection, preserve existing submission semantics, update edit/rerun hydration, remove the standalone checkbox, and update focused Create-page tests plus the desired-state Create Page doc.

## Initial Requirement Gap Analysis

This table records the pre-implementation repository state used to generate `tasks.md`. Current implementation verification is recorded in `verification.md`.

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/entrypoints/task-create.tsx` renders GitHub Repo, Branch, and Publish Mode in Steps card | keep behavior and add MM-412-specific placement test | unit/integration-style UI |
| FR-002 | partial | `frontend/src/entrypoints/task-create.tsx` still renders `Enable merge automation` in Execution context | remove standalone checkbox and test absence | unit/integration-style UI |
| FR-003 | implemented_unverified | `frontend/src/entrypoints/task-create.tsx` places Publish Mode after Branch in `.queue-inline-selector-row` | preserve responsive grouping and update test coverage | unit/integration-style UI |
| FR-004 | missing | Publish Mode options are only `pr`, `branch`, `none` | add PR-with-merge visible choice for ordinary eligible tasks | unit/integration-style UI |
| FR-005 | partial | submission already sends `mergeAutomation.enabled=true` from checkbox | map combined selection to existing PR + merge payload | integration-style request-shape UI |
| FR-006 | implemented_unverified | submission already preserves none/branch/pr publish modes | add coverage proving non-merge choices omit merge automation | integration-style request-shape UI |
| FR-007 | partial | resolver constraints clear checkbox state through `mergeAutomationAvailable` | apply constraints to combined selection and clear invalid PR+merge visibly | unit/integration-style UI |
| FR-008 | implemented_unverified | direct resolver skill tests exist for checkbox hiding | update coverage to prove PR+merge option is unavailable or cleared | unit/integration-style UI |
| FR-009 | implemented_unverified | Branch and Publish Mode selects have `aria-label` values | preserve accessible names and add combined option coverage | unit UI |
| FR-010 | implemented_verified | Existing copy says uses pr-resolver after PR readiness and not bypassing resolver handling | move/retain copy near combined option if needed | unit UI |
| FR-011 | implemented_verified | Jira Orchestrate behavior is separate from Create-page publish option | no implementation change unless tests expose coupling | final verify |
| FR-012 | partial | edit/rerun hydration maps stored publish mode, but merge automation state is not represented in Publish Mode | add stored-state normalization for PR-with-merge | unit/integration-style UI |
| FR-013 | missing | MM-412-specific combined-option tests absent | add focused tests and preserve MM-412 traceability | unit/integration-style UI |
| DESIGN-REQ-001 | implemented_unverified | Steps-card publish placement exists | preserve and verify | unit UI |
| DESIGN-REQ-002 | partial | compact controls exist, combined PR+merge option absent | add combined option while preserving compact controls | unit UI |
| DESIGN-REQ-003 | implemented_unverified | existing publish submit contract maps none/branch/pr | preserve contract while adding combined UI state | integration-style UI |
| DESIGN-REQ-004 | partial | merge automation eligibility exists as checkbox gating | move eligibility into Publish Mode choices | unit/integration-style UI |
| DESIGN-REQ-005 | implemented_verified | merge automation copy already references PR readiness and pr-resolver | keep or relocate copy with no direct auto-merge implication | unit UI |
| DESIGN-REQ-006 | partial | publish mode hydrates; merge automation combined state does not | normalize stored PR+merge state | unit UI |
| DESIGN-REQ-007 | implemented_unverified | submit payload preserves publish fields | verify through request-shape tests | integration-style UI |
| DESIGN-REQ-008 | partial | invalid merge checkbox is cleared; combined value needs equivalent clearing | clear invalid combined selection and preserve draft state | unit UI |
| DESIGN-REQ-009 | implemented_unverified | compact controls have aria labels | preserve in tests | unit UI |
| DESIGN-REQ-010 | partial | existing tests cover placement and branch mapping, but old checkbox tests remain | update tests for combined Publish Mode semantics | unit/integration-style UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present for backend but not expected in this story  
**Primary Dependencies**: React, TanStack Query, existing Create page entrypoint, existing MoonMind REST execution create/edit/rerun surfaces, Vitest and Testing Library  
**Storage**: Existing execution payload snapshots only; no new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`  
**Integration Testing**: Focused Create page request-shape tests in `frontend/src/entrypoints/task-create.test.tsx`; no compose-backed service integration is required for this UI state story  
**Target Platform**: Mission Control browser UI served by FastAPI  
**Project Type**: Web UI  
**Performance Goals**: Publish selection changes remain immediate and do not add network calls or submission latency  
**Constraints**: Preserve existing backend/runtime publish contract, preserve resolver-style restrictions, keep Jira Orchestrate behavior unchanged, and preserve MM-412 traceability  
**Scale/Scope**: One Create page entrypoint, focused Create page tests, and desired-state Create Page documentation

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story preserves existing MoonMind task submission and pr-resolver behavior.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. Publish controls remain MoonMind-native UI state and do not introduce provider-specific browser calls.
- IV. Own Your Data: PASS. Existing task payload snapshots remain the data boundary.
- V. Skills Are First-Class and Easy to Add: PASS. Resolver-style skill restrictions remain explicit.
- VI. Replaceable Scaffolding: PASS. Adds focused UI state and tests without new orchestration scaffolding.
- VII. Runtime Configurability: PASS. Runtime and skill constraints continue to drive availability.
- VIII. Modular Architecture: PASS. Work stays in existing Create page surfaces.
- IX. Resilient by Default: PASS. Invalid combined selections are cleared instead of silently submitting stale merge automation.
- X. Continuous Improvement: PASS. Verification evidence will be recorded in feature artifacts.
- XI. Spec-Driven Development: PASS. Runtime changes follow this one-story Moon Spec.
- XII. Canonical Documentation Separation: PASS. Desired-state docs stay declarative; implementation notes remain in `specs/` and `docs/tmp/`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility alias or backend enum is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/208-create-task-publish-controls/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-publish-controls.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

docs/UI/
└── CreatePage.md

docs/tmp/jira-orchestration-inputs/
└── MM-412-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing Create page implementation surface. Add a small UI-layer publish selection mapping in `task-create.tsx`, update focused tests in the existing test file, and update the declarative Create Page doc to remove the standalone checkbox contract.

## Complexity Tracking

No constitution violations.
