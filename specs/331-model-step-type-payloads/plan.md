# Implementation Plan: Model Explicit Step Type Payloads and Validation

**Branch**: `run-jira-orchestrate-for-mm-569-model-ex-42b254e1` | **Date**: 2026-05-09 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/331-model-step-type-payloads/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but is blocked in this managed branch by the helper's feature-branch naming guard. The active feature directory is resolved from `.specify/feature.json` as `specs/331-model-step-type-payloads`, so planning continues against that explicit feature path.

## Summary

MM-569 requires MoonMind to converge draft, submission, and executable task steps on an explicit Step Type model with field-addressable validation before execution. Existing repository evidence shows partial support: the Create page has Step Type authoring tests, the template catalog normalizes Tool and Skill steps and validates preset expansion inputs, and the canonical task contract rejects unresolved Preset/runtime-invalid step types. The implementation plan is test-first: add focused unit coverage for model and validator behavior, add hermetic integration coverage at the execution-submission boundary, then close gaps in shared normalization/validation so Tool, Skill, Preset, legacy-reader, and traceability requirements are covered coherently.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/entrypoints/task-create-step-type.test.tsx`; `moonmind/workflows/tasks/task_contract.py`; `api_service/services/task_templates/catalog.py` | Add/extend shared Step Type validation so authored steps consistently require stable identity, display label/title, discriminator, and exactly one matching sub-payload across draft/template/submission boundaries. | unit + integration |
| FR-002 | implemented_unverified | `tests/unit/workflows/tasks/test_task_contract.py`; `tests/unit/api/test_task_step_templates_service.py` cover several mixed-type failures | Add MM-569-specific regression coverage and fill any validator gaps found by red-first tests. | unit |
| FR-003 | partial | `api_service/services/task_templates/catalog.py` emits `preset.inputs.*` errors; task contract mostly raises string errors | Standardize field-addressable validation output for Step Type errors where the boundary exposes structured details. | unit + integration |
| FR-004 | partial | `_normalize_tool_payload()` validates tool id/name, inputs, requiredCapabilities, command-like tool policy metadata | Add tests and implementation for remaining Tool validation expectations where local services exist; document unavailable service checks as explicit degraded validations. | unit |
| FR-005 | partial | `_normalize_skill_payload()` validates skill payload shape, args, capabilities, context, permissions, autonomy objects | Add tests and implementation for skill resolution/runtime compatibility/context/autonomy validation surfaces available locally. | unit |
| FR-006 | partial | `TaskTemplateCatalogService.expand_template()` validates preset inputs, recursive expansion, generated steps, and limits in existing tests | Add MM-569 coverage for active version, generated-step validation, visible warnings, deterministic expansion, and policy limits. | unit + integration |
| FR-007 | implemented_unverified | `TaskStepSpec._reject_forbidden_step_overrides()` rejects `type: preset`; `tests/integration/temporal/test_task_shaped_submission_normalization.py` asserts expanded task steps are not preset | Add explicit execution-submission regression for unresolved Preset rejection unless linked-preset mode is explicitly present. | integration |
| FR-008 | partial | Create-page tests cover stale async expansion and in-place expansion; catalog emits recoverable field errors for preset inputs | Add coverage that failed apply/submit expansion preserves entered Preset inputs and visible errors. | unit + integration |
| FR-009 | partial | Template catalog defaults missing `type` to Skill for legacy steps while rejecting unsupported explicit types | Add coverage that legacy readers remain accepted while new authoring/submission output emits normalized explicit Step Type shapes. | unit + integration |
| FR-010 | partial | Runtime task contract preserves preset provenance and rejects unresolved Preset execution; proposal/preset paths still need convergence checks | Align draft, submission, and executable validation so accepted executable steps are flat Tool/Skill payloads with provenance only. | integration |
| FR-011 | implemented_unverified | `spec.md` preserves `MM-569`, `manual-mm-569-mm-574`, and original preset brief | Preserve traceability through plan, research, data model, contract, quickstart, tasks, implementation notes, verification, commit, and PR metadata. | final traceability |
| SCN-001 | partial | Existing TaskCreate UI and task/template model tests cover some valid Tool/Skill/Preset examples | Add end-to-end validation examples for each Step Type. | unit + integration |
| SCN-002 | implemented_unverified | Existing task contract/template tests reject several mixed payloads | Add MM-569 named tests covering all mixed/missing payload classes. | unit |
| SCN-003 | partial | Tool payload validation is present but not complete against all expected service-backed checks | Add local validator coverage and degraded-check behavior where services are unavailable. | unit |
| SCN-004 | partial | Skill payload shape validation is present but not complete against runtime compatibility and context requirements | Add local validator coverage and degraded-check behavior. | unit |
| SCN-005 | partial | Preset expansion validation exists in catalog service | Add generated-step and warning/preservation coverage. | unit + integration |
| SCN-006 | implemented_unverified | Runtime task contract rejects `type: preset` | Add explicit boundary coverage for unresolved Preset rejection. | integration |
| SC-001 | partial | Some valid examples covered by current tests | Expand example matrix to include Tool, Skill, and Preset draft contexts. | unit |
| SC-002 | implemented_unverified | Mixed-type failures exist in current tests | Add complete MM-569 invalid matrix. | unit |
| SC-003 | partial | Structured preset errors exist; task contract errors are not uniformly structured | Add structured/field-addressable validation assertions. | unit + integration |
| SC-004 | implemented_unverified | Runtime rejects non-executable step types | Add explicit unresolved Preset submission test. | integration |
| SC-005 | partial | Tool, Skill, mixed-type, and legacy cases exist; Preset and migration matrix incomplete | Add required matrix coverage. | unit + integration |
| SC-006 | implemented_unverified | `spec.md` currently preserves source traceability | Preserve traceability in every downstream artifact and final evidence. | final traceability |
| DESIGN-REQ-012 | partial | Explicit Tool/Skill discriminators exist in task contract and template catalog | Complete normalized Step Type model coverage across draft/submission/executable contexts. | unit + integration |
| DESIGN-REQ-013 | partial | Common validation exists but field-addressable output is inconsistent | Add stable identity/title/type/sub-payload and field-addressable error coverage. | unit + integration |
| DESIGN-REQ-014 | partial | Tool validation exists for id, inputs, caps, command policy | Close available Tool validation checks and document unavailable checks. | unit |
| DESIGN-REQ-015 | partial | Skill payload validation exists for id/args/caps/context/permissions/autonomy shapes | Close available Skill validation checks and document unavailable checks. | unit |
| DESIGN-REQ-018 | partial | Preset expansion and runtime non-preset enforcement exist | Add unresolved Preset rejection, generated-step, warning, limit, and input-preservation coverage. | unit + integration |
| DESIGN-REQ-021 | partial | Legacy defaulting exists; normalized authoring output needs stronger proof | Add migration tests that legacy reads work while new emits are explicit. | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Create-page behavior.  
**Primary Dependencies**: Pydantic v2, FastAPI, SQLAlchemy async ORM, Temporal Python SDK activity/service boundaries, React, TanStack Query, Vitest, Testing Library.  
**Storage**: Existing task-template catalog tables and execution payload/artifact records only; no new persistent storage planned.  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; focused Python tests through `./tools/test_unit.sh <pytest-target>` and focused UI tests through `./tools/test_unit.sh --ui-args <vitest-target>` or `npm run ui:test -- <path>` after dependencies are prepared.  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; focused integration tests under `tests/integration/api/` and `tests/integration/temporal/`.  
**Target Platform**: MoonMind API service, Mission Control Create page, and Temporal-backed task submission runtime.  
**Project Type**: Web application plus Python orchestration/control-plane services.  
**Performance Goals**: Validation must complete synchronously during draft/apply/submit paths without adding externally visible latency to ordinary task submission; preset expansion retains existing step-count limit enforcement.  
**Constraints**: No raw credentials in payloads or artifacts; no unresolved Preset runtime execution by default; no new persistent storage; maintain pre-release clean-break policy for internal contracts while preserving explicit worker-bound compatibility where required.  
**Scale/Scope**: One independently testable story covering Step Type payload validation for Tool, Skill, and Preset across authoring, template expansion, and executable task submission.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The plan strengthens task/preset/skill/tool contracts without building a competing agent layer.
- II. One-Click Agent Deployment: PASS. No new required external service or secret is introduced.
- III. Avoid Vendor Lock-In: PASS. Jira remains traceability source only; Step Type validation is provider-neutral.
- IV. Own Your Data: PASS. Validation and artifacts remain local/operator-owned.
- V. Skills Are First-Class and Easy to Add: PASS. Skill steps remain explicit first-class payloads with contract validation.
- VI. Scientific Method / Tests Anchor: PASS. Plan is TDD-first with unit and integration verification before implementation.
- VII. Runtime Configurability: PASS. No hardcoded provider/runtime selection is added.
- VIII. Modular and Extensible Architecture: PASS. Planned changes stay in existing task contract, template catalog, and Create-page boundaries.
- IX. Resilient by Default: PASS. Runtime execution rejects ambiguous or unresolved payloads before workflow creation.
- X. Facilitate Continuous Improvement: PASS. Final verification and traceability are required.
- XI. Spec-Driven Development: PASS. `spec.md`, this plan, and design artifacts drive the change.
- XII. Documentation Separation: PASS. Execution planning remains in feature-local artifacts; canonical docs are read-only source requirements.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases or hidden fallbacks are planned; legacy-reader behavior is explicit migration input only.

Post-design re-check: PASS. `research.md`, `data-model.md`, `contracts/step-type-validation-contract.md`, and `quickstart.md` keep the same boundaries, storage, and test-first constraints.

## Project Structure

### Documentation (this feature)

```text
specs/331-model-step-type-payloads/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── step-type-validation-contract.md
└── tasks.md             # Phase 2 output, not created by this planning step
```

### Source Code (repository root)

```text
api_service/
└── services/task_templates/catalog.py

frontend/
└── src/entrypoints/
    ├── task-create.tsx
    ├── task-create.test.tsx
    └── task-create-step-type.test.tsx

moonmind/
└── workflows/tasks/
    ├── payload.py
    └── task_contract.py

tests/
├── unit/
│   ├── api/test_task_step_templates_service.py
│   └── workflows/tasks/test_task_contract.py
└── integration/
    ├── api/test_task_contract_normalization.py
    └── temporal/test_task_shaped_submission_normalization.py
```

**Structure Decision**: Use existing API service, task contract, template catalog, Create-page, and test directories. No new top-level module or persistent storage is required.

## Complexity Tracking

No constitution violations require justification.
