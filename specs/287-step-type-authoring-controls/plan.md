# Implementation Plan: Add Step Type Authoring Controls

**Branch**: `287-step-type-authoring-controls` | **Date**: 2026-04-30 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/287-step-type-authoring-controls/spec.md`

## Summary

MM-568 is a single-story runtime UI feature for Create page step authoring. The current Create page already renders one Step Type selector and type-specific panels, but repo inspection found a gap in the incompatible-data acceptance criterion: changing Step Type hid previous type-specific values without visible discard feedback. The implementation updates the Step Type change path to clear meaningful incompatible type-specific state, preserve shared instructions, and show an explicit discard notice. Verification focuses on the existing Create page Vitest suite through the repo test wrapper.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` renders one `Step Type` fieldset per step; `task-create.test.tsx` covers one selector | preserve | targeted UI test |
| FR-002 | implemented_verified | `STEP_TYPE_OPTIONS` and `STEP_TYPE_HELP_TEXT`; existing helper-copy test | preserve | targeted UI test |
| FR-003 | implemented_verified | conditional Tool, Skill, and Preset panels in `task-create.tsx`; existing switch test | preserve | targeted UI test |
| FR-004 | implemented_verified | `handleStepTypeChange` clears incompatible state and surfaces a notice; `task-create-step-type.test.tsx` verifies discard feedback and preserved instructions | complete | targeted UI test |
| FR-005 | implemented_verified | visible labels use Step Type, Tool, Skill, Preset; existing test asserts absence of internal vocabulary | preserve | targeted UI test |
| FR-006 | implemented_verified | step state is keyed by local step id; existing Preset scoping test covers independent state | preserve | targeted UI test |
| SC-001 | implemented_verified | existing Step Type selector test | preserve | targeted UI test |
| SC-002 | implemented_verified | existing switch/preserve instructions test | preserve | targeted UI test |
| SC-003 | implemented_verified | `task-create-step-type.test.tsx` verifies incompatible Skill fields are cleared with visible feedback | complete | targeted UI test |
| SC-004 | implemented_verified | helper-copy test checks internal terms absent | preserve | targeted UI test |
| SC-005 | implemented_verified | existing independent Preset state test | preserve | targeted UI test |
| SC-006 | implemented_verified | `spec.md` preserves MM-568 and original brief | preserve through verification | final verify |
| DESIGN-REQ-001 | implemented_verified | Step Type selector and per-step state evidence | preserve | targeted UI test |
| DESIGN-REQ-002 | implemented_verified | type options and panels evidence | preserve | targeted UI test |
| DESIGN-REQ-008 | implemented_verified | `task-create.tsx` and `task-create-step-type.test.tsx` verify visible discard handling | complete | targeted UI test |
| DESIGN-REQ-017 | implemented_verified | labels/helper copy evidence | preserve | targeted UI test |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not touched for this story
**Primary Dependencies**: React, TanStack Query, existing Create page entrypoint, Vitest and Testing Library
**Storage**: No new persistent storage; existing in-memory draft step state only
**Unit Testing**: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx` for focused UI verification; `./tools/test_unit.sh` for full final unit verification when feasible
**Integration Testing**: Existing Create page component tests exercise the user-facing UI boundary without external services; no compose-backed integration change is required
**Target Platform**: Mission Control web UI
**Project Type**: Web application frontend
**Performance Goals**: Step Type switching remains immediate and local to the edited step
**Constraints**: Preserve MM-568 traceability; runtime implementation workflow; no internal Activity/Capability umbrella labels; preserve shared instructions
**Scale/Scope**: One Create page step editor behavior

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The change stays in task authoring UI and does not alter agent runtime design.
- IV. Own Your Data: PASS. Draft state remains local operator-owned UI state until submission.
- IX. Resilient by Default: PASS. No workflow/activity payload compatibility changes are introduced.
- XII. Documentation Separation: PASS. Jira orchestration input and execution notes remain feature-local under `specs/287-step-type-authoring-controls/` and artifacts; canonical docs are read-only requirements.
- Testing discipline: PASS. Focused UI tests cover the changed behavior; final unit verification is attempted through the repo wrapper.

## Project Structure

### Documentation (this feature)

```text
specs/287-step-type-authoring-controls/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── step-type-authoring-ui.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx
```

**Structure Decision**: This story is a Create page UI behavior change. The implementation remains in the existing entrypoint and its colocated Vitest suite.

## Complexity Tracking

No constitution violations or added complexity.
