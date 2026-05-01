# Implementation Plan: Author Agentic Skill Steps

**Branch**: `290-author-agentic-skill-steps` | **Date**: 2026-05-01 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `/specs/290-author-agentic-skill-steps/spec.md`

## Summary

MM-577 requires task authors to configure agentic Skill steps with clear runtime boundaries, supported Skill controls, and validation before execution. Repo gap analysis shows the Create page, task contract, and task-template catalog already implement most Skill authoring and validation behavior from earlier Step Type work. This slice adds MM-577-specific traceability and regression evidence while preserving the existing Tool, Skill, and Preset separation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Create page exposes Skill selector, instructions, advanced Skill Args JSON, and Skill Required Capabilities fields in `frontend/src/entrypoints/task-create.tsx`. | Add MM-577-focused Create-page regression for authored Skill payload. | frontend integration |
| FR-002 | implemented_unverified | Create page maps Skill steps to submitted Skill payloads and adjacent Tool/Preset authoring tests exist. | Verify payload shape and visible Skill distinction under MM-577. | frontend integration |
| FR-003 | implemented_unverified | Existing Create-page tests reject invalid Skill Args JSON; task contract validates capability list shape. | Verify invalid Skill args and contract coverage remain passing. | frontend integration + unit |
| FR-004 | partial | Create page preserves Skill id, args, and required capabilities; task-template catalog preserves broader Skill metadata when present. | Add MM-577 traceable evidence and avoid unsupported hidden fields. | frontend integration + service unit |
| FR-005 | implemented_unverified | Submission-time validation and backend contracts cover known Skill shape and capability constraints. | Verify existing validation surfaces; unsupported runtime/provider checks remain runtime concerns unless exposed. | frontend integration + unit |
| FR-006 | implemented_unverified | Task contract and template catalog reject non-skill Tool payloads on Skill steps. | Verify existing rejection tests. | unit |
| FR-007 | implemented_unverified | Step Type helper text and Skill labels distinguish Skill as agent work. | Verify in Create-page target and preserve evidence. | frontend integration |
| FR-008 | partial | MM-577 canonical input artifact exists; no spec artifact existed before this run. | Preserve MM-577 in spec, tasks, verification, and test evidence. | traceability review |
| DESIGN-REQ-009 | partial | Skill authoring and labels exist; MM-577-specific traceability was missing. | Add regression and verification evidence. | frontend integration |
| DESIGN-REQ-010 | partial | Skill selector, instructions, args/context, and required capabilities exist; template metadata tests cover extended controls. | Add MM-577 evidence. | frontend integration + unit |
| DESIGN-REQ-019 | implemented_unverified | Skill args and mixed payload validation exist in frontend and backend tests. | Verify validation evidence. | frontend integration + unit |
| SC-001 | partial | Existing Skill submission mechanics preserve Skill payloads; MM-577 evidence was missing. | Add focused Create-page regression for MM-577. | frontend integration |
| SC-002 | implemented_unverified | Existing invalid Skill Args and mixed payload tests cover rejection paths. | Run focused unit and UI tests. | frontend integration + unit |
| SC-003 | implemented_unverified | Tool and Preset authoring tests run in the same Create-page target. | Run setup-aware Create-page target. | frontend integration |
| SC-004 | partial | Spec maps MM-577 and design requirements; downstream verification evidence was missing. | Preserve traceability in tasks and verification. | traceability review |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for task contract and template validation  
**Primary Dependencies**: React, TanStack Query, Vitest/Testing Library, Pydantic v2, pytest  
**Storage**: Existing task submission payloads and task template rows only; no new persistent storage  
**Unit Testing**: focused `pytest` for task contract and template service tests; `./tools/test_unit.sh` for final suite when feasible  
**Integration Testing**: setup-aware Vitest Create-page target via `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`; compose integration is not required for this UI/API payload slice  
**Target Platform**: Mission Control Create page and MoonMind task submission/template validation boundaries  
**Project Type**: Web app plus Python service contracts  
**Performance Goals**: Validation remains synchronous and bounded to draft step count; no network calls added  
**Constraints**: Preserve MM-577 traceability, keep Skill distinct from Tool, do not introduce arbitrary script authoring, and do not add new storage  
**Scale/Scope**: One Create-page Skill authoring path and existing backend executable-step validation surfaces

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - Skill steps route agentic work to existing agent/runtime surfaces.
- II. One-Click Agent Deployment: PASS - no new deployment dependency.
- III. Avoid Vendor Lock-In: PASS - Skill payloads remain provider-neutral.
- IV. Own Your Data: PASS - no external storage or data export.
- V. Skills Are First-Class and Easy to Add: PASS - the story keeps Skill as a first-class Step Type.
- VI. Scaffolds Evolve: PASS - validation stays in thin UI/service contracts.
- VII. Runtime Configurability: PASS - no hardcoded operator configuration.
- VIII. Modular Architecture: PASS - work stays in existing Create-page and task contract modules.
- IX. Resilient by Default: PASS - malformed Skill configuration fails before execution.
- X. Continuous Improvement: PASS - verification artifacts record evidence and gaps.
- XI. Spec-Driven Development: PASS - spec, plan, tasks, and verification trace MM-577.
- XII. Canonical Docs vs Migration Backlog: PASS - no canonical docs migration narrative is added.
- XIII. Pre-Release Compatibility: PASS - unsupported Step Type values fail fast; no compatibility alias is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/290-author-agentic-skill-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── agentic-skill-step-authoring.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
moonmind/workflows/tasks/task_contract.py
tests/unit/workflows/tasks/test_task_contract.py
api_service/services/task_templates/catalog.py
tests/unit/api/test_task_step_templates_service.py
```

**Structure Decision**: Use existing Create-page state/submission code for direct task authoring, task contract validation for submitted executable steps, and task-template catalog tests for broader Skill metadata preservation.

## Complexity Tracking

No constitution violations.
