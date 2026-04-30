# Implementation Plan: Add Step Type Authoring Controls

**Branch**: `287-add-step-type-authoring-controls` | **Date**: 2026-04-30 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `/specs/287-add-step-type-authoring-controls/spec.md`

## Summary

MM-568 requires the Create page step editor to expose one Step Type selector with Tool, Skill, and Preset, render the matching type-specific controls, preserve compatible instructions, prevent hidden incompatible data from being silently submitted, and keep primary UI labels on Step Type terminology. Current repo evidence from earlier Step Type work already implements the selector, helper copy, switching behavior, preset scoping, governed Tool validation, and hidden Skill-field safeguards in `frontend/src/entrypoints/task-create.tsx`. This run added active MM-568 Vitest coverage in `frontend/src/entrypoints/task-create-step-type.test.tsx` because the older broad Create page test file is currently skipped. No production code changes were required after the active tests passed.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` renders a `Step Type` radio group per step; `frontend/src/entrypoints/task-create-step-type.test.tsx` verifies Tool, Skill, and Preset choices. | complete | focused frontend unit passed |
| FR-002 | implemented_verified | `STEP_TYPE_HELP_TEXT` provides source-consistent Tool, Skill, and Preset helper copy; active focused test verifies the strings. | complete | focused frontend unit passed |
| FR-003 | implemented_verified | Conditional configuration areas render from `step.stepType`; active focused test verifies switching Tool/Skill/Preset. | complete | focused frontend unit passed |
| FR-004 | implemented_verified | Active focused switching test verifies instructions survive Step Type changes. | complete | focused frontend unit passed |
| FR-005 | implemented_verified | Selector label/options/helper copy use Step Type, Tool, Skill, and Preset; active focused tests assert internal labels are absent from the selector area. | complete | focused frontend unit passed |
| FR-006 | implemented_verified | Active focused hidden Skill-field test verifies non-Skill submission does not silently submit hidden Skill configuration and Tool validation blocks missing Tool selection. | complete | focused frontend unit passed |
| FR-007 | implemented_verified | Active per-step Preset selection test verifies independent step-scoped state. | complete | focused frontend unit passed |
| SCN-001 | implemented_verified | Existing accessible Step Type selector test. | no code work | focused frontend unit |
| SCN-002 | implemented_verified | Existing helper-copy test. | no code work | focused frontend unit |
| SCN-003 | implemented_verified | Existing conditional configuration switching test. | no code work | focused frontend unit |
| SCN-004 | implemented_verified | Existing instruction preservation test. | no code work | focused frontend unit |
| SCN-005 | implemented_verified | Existing hidden Skill-field submission test. | no code work | focused frontend unit |
| SCN-006 | implemented_verified | Existing terminology assertions cover absence of Temporal Activity and Capability in selector area; Tool submission test asserts Script is absent from Tool panel. | no code work | focused frontend unit |
| DESIGN-REQ-001 | implemented_verified | Step Type selector and draft state are present in Create page. | no code work | focused frontend unit |
| DESIGN-REQ-002 | implemented_verified | Tool, Skill, and Preset appear from one Step Type control with type-specific panels. | no code work | focused frontend unit |
| DESIGN-REQ-008 | implemented_verified | Switching preserves instructions and hidden incompatible Skill fields are blocked from active submission. | no code work | focused frontend unit |
| DESIGN-REQ-017 | implemented_verified | Primary authoring copy uses Step Type terminology and avoids internal umbrella labels. | no code work | focused frontend unit |
| SC-001 | implemented_verified | Existing options test. | no code work | focused frontend unit |
| SC-002 | implemented_verified | Existing helper-copy test. | no code work | focused frontend unit |
| SC-003 | implemented_verified | Existing switching/instruction preservation test. | no code work | focused frontend unit |
| SC-004 | implemented_verified | Existing hidden Skill submission test. | no code work | focused frontend unit |
| SC-005 | implemented_verified | Existing per-step Preset scoping test. | no code work | focused frontend unit |
| SC-006 | implemented_verified | Traceability check preserves MM-568 and source design IDs across this artifact set. | complete | shell + focused frontend unit passed |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected for this UI story  
**Primary Dependencies**: React, TanStack Query, existing Create page state helpers, Vitest, Testing Library  
**Storage**: Existing task draft state only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx`  
**Integration Testing**: Focused Create page render/submission Vitest coverage is the integration boundary for this frontend-only story; compose-backed integration is not required because backend behavior does not change  
**Target Platform**: Mission Control web UI  
**Project Type**: Web application frontend  
**Performance Goals**: Step Type switching and helper rendering remain synchronous with no new network requests  
**Constraints**: Preserve MM-568 traceability; do not expose Capability, Activity, Invocation, Command, or Script as primary Step Type umbrella labels; do not change executable submission semantics unless tests expose a gap  
**Scale/Scope**: One Create page Step Type authoring controls story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses existing Create page and preset expansion surfaces.
- II. One-Click Agent Deployment: PASS. No deployment prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. Step Type labels are provider-neutral.
- IV. Own Your Data: PASS. Uses existing local draft state only.
- V. Skills Are First-Class and Easy to Add: PASS. Skill remains a first-class Step Type.
- VI. Scientific Method: PASS. Verification is evidence-based with focused tests and contingency implementation if a gap appears.
- VII. Runtime Configurability: PASS. No hardcoded provider runtime behavior is added.
- VIII. Modular and Extensible Architecture: PASS. Work stays within the Create page authoring boundary.
- IX. Resilient by Default: PASS. No workflow or activity contract changes.
- X. Facilitate Continuous Improvement: PASS. Final verification records exact evidence and residual risk.
- XI. Spec-Driven Development: PASS. Spec, plan, and tasks preserve MM-568 before verification.
- XII. Canonical Documentation Separation: PASS. Runtime work is captured in feature artifacts and code, not canonical docs migration notes.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden semantic transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/287-add-step-type-authoring-controls/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-step-type-authoring-controls.md
├── checklists/
│   └── requirements.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create-step-type.test.tsx
frontend/src/styles/mission-control.css
```

**Structure Decision**: This is a frontend Create page story. Existing backend task submission, preset expansion, and runtime contracts are reused without changes.

## Complexity Tracking

No constitution violations.
