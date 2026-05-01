# Implementation Plan: Define Step Type Authoring Model

**Branch**: `288-define-step-type-authoring-model` | **Date**: 2026-05-01 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/288-define-step-type-authoring-model/spec.md`

## Summary

Verify and complete the Step Type authoring model for Create page steps and runtime executable payload validation. Existing related Step Type work appears to provide the UI selector, separated type-specific draft state, visible incompatible-data discard behavior, preset expansion, and runtime rejection for mixed/non-executable payloads; this plan focuses on MM-575 traceability and direct validation of the authoring-model contract.


## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` defines explicit `stepType` draft state and renders one Step Type radio group; `frontend/src/entrypoints/task-create-step-type.test.tsx` asserts Skill, Tool, and Preset choices. | No new implementation; preserve MM-575 traceability and final verification. | Focused UI integration + final unit verify |
| FR-002 | implemented_verified | `frontend/src/entrypoints/task-create.tsx` conditionally renders Tool, Skill, and Preset configuration panels from `step.stepType`; focused UI test verifies panel changes. | No new implementation; keep rendered UI coverage. | Focused UI integration + type validation |
| FR-003 | implemented_verified | `handleStepTypeChange` preserves instructions, clears incompatible Skill/Tool/Preset state, and sets a visible discard message; focused UI test covers Skill-to-Tool discard. | No new implementation; verify existing coverage remains passing. | Focused UI integration |
| FR-004 | implemented_verified | `tests/unit/workflows/tasks/test_task_contract.py` rejects `preset`, `activity`, `Activity`, Tool-with-Skill, and Skill-with-non-Skill-Tool payloads. | No new implementation; retain runtime contract verification. | Runtime contract unit + final unit verify |
| FR-005 | implemented_verified | Step Type selector labels and feature artifacts use Step Type, Tool, Skill, Preset, Expansion, Plan, and Activity terminology from `docs/Steps/StepTypes.md`. | No new implementation; final verification checks terminology. | Focused UI integration + final verify |
| FR-006 | implemented_verified | The primary selector legend is `Step Type`; options are Skill, Tool, and Preset; no umbrella capability/activity/invocation/command/script label is used. | No new implementation; final verification checks primary labels. | Focused UI integration |
| SCN-001 | implemented_verified | Focused UI test asserts exactly one `Step Type` group with Skill, Tool, and Preset labels. | No new implementation. | Focused UI integration |
| SCN-002 | implemented_verified | Conditional panels in `task-create.tsx` and focused UI test verify visible controls follow selected Step Type. | No new implementation. | Focused UI integration |
| SCN-003 | implemented_verified | `handleStepTypeChange` and focused UI test verify shared instructions are preserved and incompatible Skill state is visibly discarded. | No new implementation. | Focused UI integration |
| SCN-004 | implemented_verified | Runtime contract tests reject mixed Tool/Skill payloads before execution. | No new implementation. | Runtime contract unit |
| SCN-005 | implemented_verified | Step Type UI labels and artifacts preserve source design terminology. | No new implementation. | Focused UI integration + final verify |
| SC-001 | implemented_verified | Focused UI test verifies one Step Type selector with Tool, Skill, and Preset choices. | No new implementation. | Focused UI integration |
| SC-002 | implemented_verified | Focused UI test verifies Step Type switching changes controls while preserving instructions. | No new implementation. | Focused UI integration |
| SC-003 | implemented_verified | Focused UI test verifies incompatible type-specific data is visibly cleared/handled. | No new implementation. | Focused UI integration |
| SC-004 | implemented_verified | Python runtime contract tests verify non-executable and mixed payload rejection. | No new implementation. | Runtime contract unit |
| SC-005 | implemented_verified | `spec.md`, tasks, and verification artifacts preserve MM-575 and the original preset brief. | No new implementation; preserve traceability through final verification. | Final MoonSpec verify |
| DESIGN-REQ-001 | implemented_verified | Explicit Step Type state and rendered selector in `task-create.tsx`; UI test coverage. | No new implementation. | Focused UI integration |
| DESIGN-REQ-002 | implemented_verified | Tool/Skill/Preset selector and type-specific panels in `task-create.tsx`; UI test coverage. | No new implementation. | Focused UI integration |
| DESIGN-REQ-003 | implemented_verified | Source terminology is preserved in spec, contracts, and UI labels. | No new implementation. | Final verify |
| DESIGN-REQ-005 | implemented_verified | Runtime contract rejects unresolved preset execution; preset expansion behavior is covered by existing focused UI tests. | No new implementation. | Runtime contract unit + focused UI integration |
| DESIGN-REQ-006 | implemented_verified | `handleStepTypeChange` visibly handles incompatible fields and preserves shared instructions. | No new implementation. | Focused UI integration |
| DESIGN-REQ-014 | implemented_verified | Primary selector uses Step Type as umbrella label; capability/activity terms are not primary discriminator labels. | No new implementation. | Focused UI integration + final verify |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for runtime payload contract tests  
**Primary Dependencies**: React, Vitest, Testing Library, Pydantic v2, pytest  
**Storage**: No new persistent storage  
**Unit Testing**: `./tools/test_unit.sh` with targeted Python pytest paths; TypeScript type checking via frontend tsconfig  
**Integration Testing**: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx` as rendered Create page boundary  
**Target Platform**: MoonMind Mission Control and Temporal execution payload validation  
**Project Type**: Web application plus Python service contracts  
**Performance Goals**: Step Type switching remains immediate for ordinary task authoring  
**Constraints**: Do not introduce compatibility aliases for unsupported Step Type values; preserve MM-575 in artifacts and delivery metadata  
**Scale/Scope**: One independently testable story covering one authored step model and runtime validation boundary

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - feature preserves agent/tool/preset orchestration boundaries.
- II. One-Click Agent Deployment: PASS - no deployment or secret changes.
- III. Avoid Vendor Lock-In: PASS - Step Type model is MoonMind-owned and provider-neutral.
- IV. Own Your Data: PASS - no external storage changes.
- V. Skills Are First-Class and Easy to Add: PASS - Skill remains a first-class Step Type.
- VI. Replaceable Scaffolding: PASS - behavior is guarded by contract and UI tests.
- VII. Runtime Configurability: PASS - no hardcoded operator configuration changes.
- VIII. Modular and Extensible Architecture: PASS - validates existing UI/runtime boundaries.
- IX. Resilient by Default: PASS - runtime payload validation rejects unsupported shapes fail-fast.
- X. Facilitate Continuous Improvement: PASS - final verification records evidence and remaining risks.
- XI. Spec-Driven Development: PASS - spec, plan, tasks, and verification artifacts precede final closure.
- XII. Canonical Documentation Separation: PASS - canonical docs are read-only source requirements; execution notes stay feature-local.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases or hidden fallback semantics are added.

## Project Structure

### Documentation (this feature)

```text
specs/288-define-step-type-authoring-model/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   `-- step-type-authoring-model.md
|-- tasks.md
`-- verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create-step-type.test.tsx
tests/unit/workflows/tasks/test_task_contract.py
```

**Structure Decision**: Reuse the existing Create page authoring surface and Python runtime task contract boundary. No new modules are planned unless verification exposes a gap.

## Complexity Tracking

No constitution violations.
