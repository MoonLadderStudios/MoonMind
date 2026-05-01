# Implementation Plan: Define Step Type Authoring Model

**Branch**: `288-define-step-type-authoring-model` | **Date**: 2026-05-01 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/288-define-step-type-authoring-model/spec.md`

## Summary

Verify and complete the Step Type authoring model for Create page steps and runtime executable payload validation. Existing related Step Type work appears to provide the UI selector, separated type-specific draft state, visible incompatible-data discard behavior, preset expansion, and runtime rejection for mixed/non-executable payloads; this plan focuses on MM-575 traceability and direct validation of the authoring-model contract.

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
