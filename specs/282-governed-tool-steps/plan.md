# Implementation Plan: Governed Tool Step Authoring

**Branch**: `282-governed-tool-steps` | **Date**: 2026-04-29 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/282-governed-tool-steps/spec.md`

## Summary

Implement the MM-563 runtime slice by extending Mission Control Create-page Tool step drafts with tool id, optional version, and JSON object inputs, submitting those fields as executable Tool step payloads, and tightening task contract validation so shell/script/command fields cannot enter executable steps. Unit tests cover backend task contract guardrails; frontend tests cover the authoring path and client-side validation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Create page has Step Type Tool panel but the Tool field is read-only and empty | add editable Tool id/version/inputs fields | frontend integration |
| FR-002 | partial | preset-generated Tool steps submit correctly; manual Tool drafts do not | submit manual Tool payloads without Skill payloads | frontend integration |
| FR-003 | partial | missing Tool id is blocked; input JSON is not authorable yet | add JSON object validation | frontend integration |
| FR-004 | missing | no manual Tool fields are preserved | include id/version/inputs in submitted step | frontend integration |
| FR-005 | partial | task template service rejects forbidden shell keys; task execution contract does not block command/script keys on steps | add task contract forbidden-key coverage | unit |
| FR-006 | implemented_unverified | Step Type UI already uses Tool/Skill/Preset labels | add/keep frontend assertions for Tool terminology and no Script option | frontend integration |
| DESIGN-REQ-003 | partial | executable tool payload shape exists for preset-generated steps | complete manual authoring path | frontend integration |
| DESIGN-REQ-004 | partial | backend and preset validation support Tool payloads | add manual form and validation for id/version/inputs | frontend integration |
| DESIGN-REQ-015 | implemented_unverified | Step Type choices omit Script | keep assertions and backend shell/script rejection | unit + frontend integration |
| SC-001 | missing | no manual Tool submit evidence | add frontend submit test | frontend integration |
| SC-002 | partial | missing Tool id test exists | add invalid JSON test | frontend integration |
| SC-003 | missing | no task contract test for shell/script/command step fields | add unit test and validator change | unit |
| SC-004 | implemented_unverified | existing Step Type tests assert Tool label | extend Tool panel assertions | frontend integration |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for task contract validation  
**Primary Dependencies**: React, TanStack Query, Vitest/Testing Library, Pydantic v2  
**Storage**: Existing task submission payload only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh` and targeted `pytest` for Python contract tests
**Integration Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` for Create-page behavior; hermetic integration not required for this UI/client contract slice
**Target Platform**: Mission Control web UI and MoonMind task execution API boundary  
**Project Type**: Web app plus Python service contracts  
**Performance Goals**: No material performance impact; validation runs synchronously on small draft payloads  
**Constraints**: Preserve MM-563, avoid arbitrary shell Step Type authoring, keep Tool terminology, do not add new storage or raw external credentials  
**Scale/Scope**: One Create-page step editor path and one backend task contract guardrail

## Constitution Check

- I Orchestrate, Don't Recreate: PASS - the change preserves agent/tool orchestration and typed Tool boundaries.
- II One-Click Agent Deployment: PASS - no new deployment dependency.
- III Avoid Vendor Lock-In: PASS - Tool id/version/input contract is provider-neutral.
- IV Own Your Data: PASS - no external storage or data export.
- V Skills Are First-Class and Easy to Add: PASS - does not alter agent skill bundle semantics.
- VI Scaffolds Evolve: PASS - thin UI/client validation over existing contracts.
- VII Runtime Configurability: PASS - no hardcoded operator config.
- VIII Modular Architecture: PASS - UI draft handling and task contract validation remain in existing modules.
- IX Resilient by Default: PASS - invalid payloads fail before execution.
- X Continuous Improvement: PASS - final verification will report evidence.
- XI Spec-Driven Development: PASS - this plan follows `spec.md`.
- XII Canonical Docs vs Migration Backlog: PASS - no canonical docs migration notes.
- XIII Pre-Release Compatibility: PASS - no compatibility alias or hidden fallback is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/282-governed-tool-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── governed-tool-step-authoring.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
moonmind/workflows/tasks/task_contract.py
tests/unit/workflows/tasks/test_task_contract.py
```

**Structure Decision**: Use the existing Create-page entrypoint and existing task execution contract module because the story changes manual task authoring and submission validation only.

## Complexity Tracking

No constitution violations.
