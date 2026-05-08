# Implementation Plan: Normalize Task-Shaped Submissions

**Branch**: `run-jira-orchestrate-for-mm-627-normaliz-0f1ed32a` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/320-normalize-task-shaped-submissions/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but failed because the managed branch name is not a numeric feature branch. `.specify/feature.json` already points to this feature directory, so planning proceeded manually with the same resolved paths.

## Summary

MM-627 requires create, edit, and rerun task submissions to normalize into one canonical task-shaped contract that preserves objective text, steps, attachment targets, runtime/publish choices, dependencies, Jira provenance, branch intent, and preset metadata. Current code already supports structured objective/step attachments and several frontend/API validations, but backend normalization still accepts legacy branch fields and does not consistently preserve preset/provenance metadata in the canonical task payload. The plan is to tighten the Mission Control submit payload and API normalization boundary, add verification-first coverage where behavior appears present, add code where gaps are confirmed, and validate with focused unit tests plus hermetic integration coverage.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/entrypoints/task-create.tsx` builds `payload.task`; `api_service/api/routers/executions.py` normalizes task-shaped requests | add end-to-end create/edit/rerun verification first; implement only if gaps appear | unit + integration |
| FR-002 | implemented_verified | `task-create.test.tsx` covers objective and step attachment arrays; `test_executions.py` covers attachment validation | no new implementation beyond final traceability | none beyond final verify |
| FR-003 | partial | create and some edit/rerun attachment tests exist; prepare/prompt/detail preservation is not proven as one contract | add boundary tests across create/edit/rerun and patch normalization if any attachment target is lost | unit + integration |
| FR-004 | partial | runtime, dependency, publish, and attachment checks exist; repository and target-binding rejection need explicit coverage | add validation tests, then tighten repository/target-binding failures if tests expose gaps | unit + integration |
| FR-005 | partial | frontend preserves many fields; backend normalization preserves many fields but needs full proof for Jira provenance and preset metadata | add canonical task payload tests and preserve any dropped fields | unit + integration |
| FR-006 | partial | frontend emits `task.git.branch` and has no `targetBranch` in create payload; backend still accepts and normalizes `targetBranch` fields | remove legacy target branch emission from new task-shaped normalization and add rejection/absence tests | unit + integration |
| FR-007 | partial | frontend template identity/provenance tests exist; backend snapshot code recognizes `appliedStepTemplates` but canonical task normalization needs preservation coverage | add backend preservation tests and implement missing provenance carry-through | unit + integration |
| FR-008 | partial | frontend includes `appliedStepTemplates`; API serialization can read them from records, but create normalization does not clearly preserve authored preset binding metadata | add canonical payload tests and implement missing `authoredPresets`/template metadata preservation | unit + integration |
| FR-009 | partial | frontend blocks ambiguous preset retargeting and keeps step attachments after reorder; backend boundary lacks equivalent no-retargeting proof | add backend and integration retargeting tests, then tighten validation if needed | unit + integration |
| FR-010 | partial | `test_executions.py` covers attachment policy and dependency errors; runtime/publish validation exists | add missing repository/target-binding/publish edge tests and implement explicit failures where absent | unit + integration |
| FR-011 | implemented_verified | UI tests prove instructions are not rewritten with attachment text; API validates structured refs | no new implementation beyond final traceability | none beyond final verify |
| FR-012 | implemented_verified | `spec.md` preserves MM-627 and the original preset brief | preserve through plan, tasks, verification, commit, and PR metadata | final traceability |
| SC-001 | partial | objective/step create coverage exists, but create/edit/rerun all-field preservation is not proven | add focused preservation matrix tests | unit + integration |
| SC-002 | partial | field-specific coverage exists for dependencies, branch, templates, and attachments, but not as one canonical task contract | add canonical task contract test and fill preservation gaps | unit + integration |
| SC-003 | partial | frontend proves no `targetBranch` in create payload; backend still carries legacy branch fields if supplied | enforce canonical new-output branch semantics | unit + integration |
| SC-004 | partial | several invalid cases fail explicitly; missing cases remain for repository and target binding | add negative tests and implement missing validation | unit + integration |
| SC-005 | partial | UI retargeting tests exist; backend/workflow-boundary proof is missing | add boundary tests and fix validation if needed | unit + integration |
| SC-006 | implemented_verified | `spec.md` and this plan preserve MM-627 and all listed design IDs | preserve through downstream artifacts | final traceability |
| DESIGN-REQ-001 | implemented_unverified | task-shaped API and frontend path exist | verify create/edit/rerun task intent handoff | unit + integration |
| DESIGN-REQ-003 | partial | explicit objective/step arrays exist; all flow preservation needs proof | add cross-flow attachment target tests | unit + integration |
| DESIGN-REQ-006 | partial | authoring and backend validation exist for several fields | cover missing validation cases | unit + integration |
| DESIGN-REQ-008 | partial | normalization preserves many fields but not all preset/provenance cases | implement full canonical preservation | unit + integration |
| DESIGN-REQ-011 | partial | canonical shape is partially represented in frontend and API models | align normalized task output with canonical shape | unit + integration |
| DESIGN-REQ-025 | partial | several invariants are implemented and tested; target retargeting and legacy branch semantics need boundary proof | add invariant tests and fix gaps | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control create/edit/rerun UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, Vitest/Testing Library, pytest  
**Storage**: Existing Temporal execution records, artifact-backed original task input snapshots, Temporal artifact metadata/content store; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` for Python; `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` or `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` for focused UI iteration  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; add or update compose-backed API/Temporal boundary coverage where the normalized task payload crosses execution creation  
**Target Platform**: MoonMind Mission Control web app and FastAPI/Temporal control plane on Linux/Docker Compose  
**Project Type**: Full-stack web application with Temporal-backed execution orchestration  
**Performance Goals**: Task normalization remains linear in submitted steps and attachments; attachment validation keeps a single metadata lookup for unique artifact refs  
**Constraints**: No binary payloads in inline instructions or workflow history; no hidden compatibility aliases for canonical branch semantics; fail explicitly for invalid task-shaped input; keep workflow payloads compact  
**Scale/Scope**: One independently testable MM-627 story covering create, edit, and rerun task submission normalization

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - work stays in MoonMind control-plane normalization and does not rebuild agent behavior.
- II. One-Click Agent Deployment: PASS - no new external service or required secret is introduced.
- III. Avoid Vendor Lock-In: PASS - Jira provenance is preserved as data where supported; normalization remains provider-neutral.
- IV. Own Your Data: PASS - attachments remain MoonMind artifacts/refs and task snapshots remain operator-owned.
- V. Skills Are First-Class and Easy to Add: PASS - preset/skill provenance is preserved without mutating skill sources.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - work tightens task contracts and tests rather than adding brittle workflow scaffolding.
- VII. Runtime Configurability: PASS - attachment policy and runtime/publish options continue to flow through existing configuration and request payloads.
- VIII. Modular Architecture: PASS - planned edits stay at Mission Control submit helpers and API normalization boundaries.
- IX. Resilient by Default: PASS - invalid inputs fail before execution and attachment-aware rerun/edit behavior remains snapshot-based.
- X. Continuous Improvement: PASS - planning artifacts include evidence, tests, and next action traceability.
- XI. Spec-Driven Development: PASS - `spec.md`, `plan.md`, and design artifacts define the work before tasks/implementation.
- XII. Canonical Documentation Separation: PASS - rollout and implementation tracking remain under `specs/320-normalize-task-shaped-submissions/`.

No constitution violations are currently justified.

## Project Structure

### Documentation (this feature)

```text
specs/320-normalize-task-shaped-submissions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-shaped-submission-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

api_service/api/routers/
├── executions.py
└── task_dashboard_view_model.py

moonmind/schemas/
└── temporal_models.py

tests/unit/api/routers/
└── test_executions.py

tests/integration/
├── services/temporal/
└── temporal/
```

**Structure Decision**: This is a full-stack control-plane story. Frontend work shapes authored task submissions, FastAPI work normalizes and validates canonical task payloads, schema work preserves execution-visible fields, and tests span focused UI/API units plus a hermetic execution boundary.

## Complexity Tracking

No constitution violations require complexity exceptions.
