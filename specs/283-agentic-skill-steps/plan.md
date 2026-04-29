# Implementation Plan: Agentic Skill Step Authoring

**Branch**: `283-agentic-skill-steps` | **Date**: 2026-04-29 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/283-agentic-skill-steps/spec.md`

## Summary

MM-564 requires task authors to configure Skill steps as agentic executable work with a skill selector or documented auto behavior, instructions, optional JSON args, required capabilities, and clear separation from deterministic Tool steps. Repo gap analysis shows earlier Step Type slices already implemented the core runtime and UI mechanics in the Create page, task execution contract, and task-template catalog. This slice adds MM-564-specific verification artifacts and focused regression coverage so the existing behavior is traceable to the Jira story.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Create page exposes Step Type `Skill`, Skill selector, advanced Skill Args JSON, and Skill Required Capabilities fields in `frontend/src/entrypoints/task-create.tsx`. | Add MM-564-focused Create-page regression for authored Skill payload. | frontend integration |
| FR-002 | implemented_unverified | Create page maps Skill steps to submitted `type: skill` entries and Skill payloads; task contract accepts explicit Skill discriminators. | Verify payload shape under MM-564. | frontend integration + unit |
| FR-003 | implemented_unverified | Create page rejects invalid Skill Args JSON; task contract validates `skill.requiredCapabilities` list shape. | Verify invalid Skill Args and contract coverage. | frontend integration + unit |
| FR-004 | partial | Create page preserves Skill id, args, and required capabilities; task-template catalog preserves context/permissions/autonomy metadata when present. | Add traceable evidence and avoid adding unsupported hidden fields to direct task submissions. | frontend integration + service unit |
| FR-005 | implemented_unverified | Submitted Skill steps can carry legacy `tool.type: skill` metadata for internal tools while remaining `type: skill`. | Verify existing contract and template tests. | unit |
| FR-006 | implemented_unverified | Task contract and template catalog reject mixed non-skill Tool payloads on Skill steps. | Verify existing rejection tests. | unit |
| FR-007 | partial | Step Type help text distinguishes Skill as agent work; Skill panel labels are distinct from Tool fields. | Preserve UI evidence in focused test and quickstart. | frontend integration |
| DESIGN-REQ-005 | partial | Skill authoring fields and backend Skill payload models exist. | Add MM-564 traceability and focused test. | frontend integration + unit |
| DESIGN-REQ-015 | partial | Skill picker/validation and Jira agentic distinction exist across Create page, task contract, and template catalog. | Verify with MM-564 evidence. | frontend integration + unit |
| SC-001..004 | partial | Existing tests cover the mechanics but not MM-564 traceability. | Add focused test, run targeted suites, and write verification. | frontend integration + unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for task contract and template validation  
**Primary Dependencies**: React, TanStack Query, Vitest/Testing Library, Pydantic v2, pytest  
**Storage**: Existing task submission payloads and task template rows only; no new persistent storage  
**Unit Testing**: focused `pytest` for task contract and template service tests; `./tools/test_unit.sh` for final suite when feasible
**Integration Testing**: focused Vitest Create-page tests via `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`; compose integration not required for this UI/API payload slice
**Target Platform**: Mission Control Create page and MoonMind task submission/template validation boundaries  
**Project Type**: Web app plus Python service contracts  
**Performance Goals**: Validation remains synchronous and bounded to draft step count; no network calls added  
**Constraints**: Preserve MM-564 traceability, keep Skill distinct from Tool, do not introduce arbitrary script authoring, and do not add new storage  
**Scale/Scope**: One Create-page Skill authoring path and existing backend executable-step validation surfaces

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - Skill steps route agentic work to existing agent/runtime surfaces.
- II. One-Click Agent Deployment: PASS - no new deployment dependency.
- III. Avoid Vendor Lock-In: PASS - Skill payloads remain provider-neutral.
- IV. Own Your Data: PASS - no external storage or data export.
- V. Skills Are First-Class and Easy to Add: PASS - the story makes Skill a first-class Step Type.
- VI. Scaffolds Evolve: PASS - validation stays in thin UI/service contracts.
- VII. Runtime Configurability: PASS - no hardcoded operator configuration.
- VIII. Modular Architecture: PASS - work stays in existing Create-page and task contract modules.
- IX. Resilient by Default: PASS - malformed Skill configuration fails before execution.
- X. Continuous Improvement: PASS - verification artifacts record evidence and gaps.
- XI. Spec-Driven Development: PASS - spec, plan, tasks, and verification trace MM-564.
- XII. Canonical Docs vs Migration Backlog: PASS - no canonical docs migration narrative is added.
- XIII. Pre-Release Compatibility: PASS - unsupported Step Type values fail fast; no new compatibility alias is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/283-agentic-skill-steps/
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
