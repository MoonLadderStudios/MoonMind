# Implementation Plan: Present Step Type Authoring

**Branch**: `281-step-type-authoring` | **Date**: 2026-04-29 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/281-step-type-authoring/spec.md`

## Summary

MM-562 requires the Create page step editor to present Tool, Skill, and Preset through one Step Type selector, show type-specific controls, preserve compatible authoring data, and avoid internal runtime vocabulary in the primary selector. Current repo evidence from MM-556/MM-558 already implements the selector, switching behavior, preset scoping, preview/apply, and hidden Skill-field submission safeguards in `frontend/src/entrypoints/task-create.tsx` with Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx`. The remaining MM-562-specific gap is concise helper copy for the Step Type choices, so the implementation will add focused frontend coverage and a small UI helper text addition.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `task-create.tsx` renders one `Step Type` select per step; `task-create.test.tsx` verifies Tool/Skill/Preset options | no code work | focused frontend unit |
| FR-002 | partial | Options exist, but no concise helper copy is rendered with the Step Type selector | add helper copy for Tool, Skill, and Preset choices | focused frontend unit |
| FR-003 | implemented_verified | Conditional Tool/Skill/Preset panels render from `step.stepType`; tests cover switching | no code work | focused frontend unit |
| FR-004 | implemented_verified | Instructions persist across switching and hidden Skill fields are blocked for Tool submission; tests cover both | no code work | focused frontend unit |
| FR-005 | implemented_unverified | Selector label/options use Step Type, Tool, Skill, Preset; helper copy must avoid internal discriminator terms | verify in helper-copy test | focused frontend unit |
| FR-006 | implemented_verified | Preset selections are scoped to each step; tests cover independent step state | no code work | focused frontend unit |
| SCN-001 | implemented_verified | Existing Step Type selector test | no code work | focused frontend unit |
| SCN-002 | partial | Helper copy absent | add test and UI copy | focused frontend unit |
| SCN-003 | implemented_verified | Existing switching test | no code work | focused frontend unit |
| SCN-004 | implemented_verified | Existing hidden Skill field submission test | no code work | focused frontend unit |
| SCN-005 | implemented_unverified | Existing copy likely passes; helper copy must be checked | add assertions to focused test | focused frontend unit |
| DESIGN-REQ-001 | implemented_verified | Step Type selector and conditional panels exist | no code work | focused frontend unit |
| DESIGN-REQ-002 | partial | Step Type picker exists; helper copy gap remains | add helper copy | focused frontend unit |
| DESIGN-REQ-009 | implemented_verified | Switching preserves instructions and hidden fields are not submitted | no code work | focused frontend unit |
| DESIGN-REQ-018 | implemented_unverified | Primary selector avoids internal labels; new helper copy must preserve that | add assertions | focused frontend unit |
| SC-001 | implemented_verified | Existing options test | no code work | focused frontend unit |
| SC-002 | missing | No helper-copy test | add test | focused frontend unit |
| SC-003 | implemented_verified | Existing switching test | no code work | focused frontend unit |
| SC-004 | implemented_verified | Existing hidden Skill submission test | no code work | focused frontend unit |
| SC-005 | implemented_verified | Existing per-step Preset scoping test | no code work | focused frontend unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected for this UI story  
**Primary Dependencies**: React, TanStack Query, existing Create page state helpers, Vitest, Testing Library  
**Storage**: Existing task draft state only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`  
**Integration Testing**: Focused Create page render/submission Vitest coverage is the integration boundary for this frontend-only story; compose-backed integration is not required because backend behavior does not change  
**Target Platform**: Mission Control web UI  
**Project Type**: Web application frontend  
**Performance Goals**: Step Type switching and helper rendering remain synchronous with no new network requests  
**Constraints**: Preserve MM-562 traceability; do not expose Temporal Activity or capability terminology as the primary Step Type discriminator; do not change executable submission semantics  
**Scale/Scope**: One Create page Step Type authoring presentation story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses existing Create page and preset expansion surfaces.
- II. One-Click Agent Deployment: PASS. No deployment prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. Step Type labels are provider-neutral.
- IV. Own Your Data: PASS. Uses existing local draft state only.
- V. Skills Are First-Class and Easy to Add: PASS. Skill remains a first-class Step Type.
- VI. Scientific Method: PASS. The remaining gap is covered test-first.
- VII. Runtime Configurability: PASS. No hardcoded provider runtime behavior is added.
- VIII. Modular and Extensible Architecture: PASS. Changes stay within the Create page authoring boundary.
- IX. Resilient by Default: PASS. No workflow or activity contract changes.
- X. Facilitate Continuous Improvement: PASS. Verification records exact evidence.
- XI. Spec-Driven Development: PASS. Spec, plan, and tasks preserve MM-562 before implementation.
- XII. Canonical Documentation Separation: PASS. Runtime work is captured in feature artifacts and code.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden semantic transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/281-step-type-authoring/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-step-type-presentation.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
```

**Structure Decision**: This is a frontend Create page story. Existing backend task submission, preset expansion, and runtime contracts are reused without changes.

## Complexity Tracking

No constitution violations.
