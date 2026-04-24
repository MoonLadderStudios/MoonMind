# Implementation Plan: Edit Task Shows All Steps

**Branch**: `192-edit-task-all-steps` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/192-edit-task-all-steps/spec.md`

## Summary

Fix MM-340 by preserving ordered task step data in the Temporal edit/rerun draft model and applying that step array to the shared `/tasks/new` form. The implementation will extend the existing frontend reconstruction helper and edit-mode form initialization, with focused Vitest coverage proving multi-step rendering and payload preservation while keeping existing single-step behavior intact.

## Technical Context

**Language/Version**: TypeScript/React frontend plus Python 3.12 backend context 
**Primary Dependencies**: React, TanStack Query, Vitest, Testing Library, existing Temporal task editing helpers 
**Storage**: Existing execution detail and input artifact contracts; no new persistent storage 
**Unit Testing**: Vitest through `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` for focused iteration and `./tools/test_unit.sh` for final verification 
**Integration Testing**: Frontend component integration tests in Vitest/Testing Library for edit-form load and submit behavior; required project integration suite remains `./tools/test_integration.sh` when backend/compose boundaries change 
**Target Platform**: Mission Control web UI served by FastAPI 
**Project Type**: Web application frontend with existing API contracts 
**Performance Goals**: Reconstruct and render typical multi-step task drafts without extra network calls beyond existing execution/artifact fetches 
**Constraints**: Preserve MM-340 traceability; do not mutate historical artifacts; do not drop user-authored steps; keep compatibility-sensitive Temporal/backend payloads unchanged unless proven necessary 
**Scale/Scope**: One user-facing edit/rerun reconstruction path for supported `MoonMind.Run` tasks

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The change stays inside MoonMind's orchestration UI and does not add agent behavior.
- II. One-Click Agent Deployment: PASS. No deployment prerequisites or new services are introduced.
- III. Avoid Vendor Lock-In: PASS. The behavior is provider-neutral and applies to existing task data.
- IV. Own Your Data: PASS. Existing locally stored execution/input data remains the source.
- V. Skills Are First-Class and Easy to Add: PASS. Step-level skill selections are preserved when present.
- VI. Scientific Method/Test Anchor: PASS. The plan uses failing focused frontend tests before production changes.
- VII. Runtime Configurability: PASS. No hardcoded operator config is added.
- VIII. Modular and Extensible Architecture: PASS. The fix extends existing reconstruction helpers and form state mapping.
- IX. Resilient by Default: PASS. The update preserves existing steps rather than silently truncating them.
- X. Facilitate Continuous Improvement: PASS. Verification evidence will identify MM-340 and test coverage.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, and verification are part of the change.
- XII. Canonical Documentation Separation: PASS. Implementation notes remain under `specs/` and `local-only handoffs`; no canonical docs are rewritten.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden transforms are added.

## Project Structure

### Documentation (this feature)

```text
specs/192-edit-task-all-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── edit-task-steps-ui.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/
├── lib/
│ └── temporalTaskEditing.ts
└── entrypoints/
 ├── task-create.tsx
 └── task-create.test.tsx
```

**Structure Decision**: Use the existing Mission Control frontend task-editing modules. Backend files are not planned unless tests show execution detail lacks step data.

## Complexity Tracking

No constitution violations.
