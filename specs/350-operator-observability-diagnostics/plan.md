# Implementation Plan: Operator Observability for Attachments, Recovery, and Resume Diagnostics

**Branch**: `350-operator-observability-diagnostics` | **Date**: 2026-05-13 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:65075e25-154f-4b9d-ada1-8cf187f002c9/repo/specs/350-operator-observability-diagnostics/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` initially failed because the checked-out Git branch is `change-jira-issue-mm-651-to-status-in-pr-18f8d199`, which is not numerically prefixed. The setup was rerun with `SPECIFY_FEATURE=350-operator-observability-diagnostics`, producing this plan path.

## Summary

MM-651 requires operator-visible task detail diagnostics for target-aware attachments, prepared context references, attachment failure phases, resumed execution provenance, preserved prior steps, and failed Resume phases. The repository already has a backend execution-detail `targetDiagnostics` projection, generated schema contract, frontend `TargetDiagnosticsPanel`, backend unit coverage, frontend unit coverage, and schema-boundary integration coverage. Planning therefore focuses on test-first tightening of the remaining gaps: empty target distinction, compatibility semantic non-drift, and complete failed Resume phase coverage including `failed_step_execution`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/executions.py` builds objective/step target diagnostics; `tests/unit/api/routers/test_executions.py::test_describe_execution_exposes_target_attachment_and_recovery_diagnostics`; `frontend/src/entrypoints/task-detail.test.tsx` renders target diagnostics | preserve behavior, add regression only if touched | final verify |
| FR-002 | partial | Frontend displays "No attachments recorded for this target" when a target is present, but backend does not consistently include empty task/step targets without diagnostics | add focused tests and implementation contingency for empty-target distinction | unit + frontend |
| FR-003 | implemented_verified | Backend normalizes refs; frontend renders Evidence refs; backend/frontend tests assert `artifact://diagnostics/input-manifest` | preserve behavior | final verify |
| FR-004 | implemented_unverified | Schema and frontend can render arbitrary refs including generated context; integration schema test uses `generated_context`, but execution-detail route lacks explicit generated-context scenario coverage | add route/frontend verification for generated context refs | unit + frontend |
| FR-005 | implemented_verified | Target failures are nested under objective/step targets; backend test asserts failing step target and failure message | preserve behavior | final verify |
| FR-006 | implemented_verified | `_normalize_attachment_failure_phase` bounds upload/validation/materialization/context_generation/degraded; backend test covers materialization and degraded fallback | preserve behavior | final verify |
| FR-007 | implemented_verified | Step targets include `stepId` and labels; frontend renders step-specific cards; backend/frontend tests cover step context separately | preserve behavior | final verify |
| FR-008 | implemented_verified | `_build_target_diagnostics` builds recovery source workflow/run from `resumeSource`; backend/frontend tests assert resumed source display | preserve behavior | final verify |
| FR-009 | implemented_verified | `_preserved_steps_from_resume_source` and UI render preserved steps; backend/frontend tests assert preserved step | preserve behavior | final verify |
| FR-010 | partial | Target diagnostics reduce raw-history parsing for covered data, but generated context and failed-step execution phase need stronger proof | add missing verification and implementation contingency | unit + frontend + integration |
| FR-011 | implemented_unverified | Backend accepts camel/snake aliases and multiple attachment field names; schema boundary integration covers aliases, but no direct semantic non-drift test for objective vs step retargeting | add compatibility non-drift tests | unit + integration |
| FR-012 | implemented_unverified | Current merge logic keys target overlays by target kind and step ID, but explicit no-retarget/no-merge tests are limited | add regression tests for objective and step preservation under alias-shaped input | unit + integration |
| FR-013 | partial | Schema and frontend support four Resume phases; backend maps checkpoint/workspace/preserved-output gaps but does not visibly derive `failed_step_execution` | add tests first; implement mapping/source support if tests fail | unit + frontend + integration |
| FR-014 | implemented_verified | `spec.md` preserves `MM-651` and the original preset brief; this plan preserves traceability | preserve traceability in downstream artifacts | final verify |
| SC-001 | implemented_verified | Existing backend/frontend target attachment tests cover objective and step targets | preserve behavior | final verify |
| SC-002 | implemented_verified | Existing backend test covers failure target and bounded materialization/degraded phases | preserve behavior | final verify |
| SC-003 | implemented_unverified | Manifest ref covered; generated context ref only has schema-boundary coverage | add generated context route/frontend verification | unit + frontend |
| SC-004 | implemented_verified | Existing backend/frontend tests cover source run and preserved prior step | preserve behavior | final verify |
| SC-005 | partial | Three failed Resume phases are mapped; `failed_step_execution` is not proven | add tests and implementation contingency | unit + frontend + integration |
| SC-006 | implemented_verified | `spec.md`, `plan.md`, and design artifacts preserve `MM-651`, the preset brief, and source IDs | preserve traceability | final verify |
| DESIGN-REQ-012 | implemented_unverified | Alias handling exists, but semantic non-drift needs explicit regression coverage | add compatibility target non-drift tests | unit + integration |
| DESIGN-REQ-030 | partial | Existing target diagnostics cover most attachment metadata/ref/failure behavior; empty targets and generated context route coverage remain gaps | add verification and implementation contingency | unit + frontend |
| DESIGN-REQ-031 | partial | Resumed execution and preserved steps are covered; failed-step execution phase remains incomplete | add tests and implementation contingency | unit + frontend + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control task detail UI  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, Temporal Python SDK, React, TanStack Query, Zod, generated OpenAPI types  
**Storage**: Existing Temporal execution records, execution parameters/memo/search attributes, task input snapshot artifacts, and Temporal artifact refs; no new persistent storage planned  
**Unit Testing**: `./tools/test_unit.sh` for final unit suite; focused Python pytest and `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` for iteration  
**Integration Testing**: `./tools/test_integration.sh` for `integration_ci`; focused `tests/integration/schemas/test_execution_target_diagnostics_boundary.py` and route/workflow boundary additions as needed  
**Target Platform**: MoonMind API service, Temporal worker/control plane, and Mission Control browser UI  
**Project Type**: Full-stack web application with Temporal-backed orchestration services  
**Performance Goals**: Execution detail payloads remain compact and derived from existing bounded metadata; operators can identify target ownership and failure phase from task detail without raw history inspection  
**Constraints**: Do not add new persistent tables; keep large diagnostics in artifacts and expose compact refs; preserve canonical objective versus step target semantics; avoid raw secrets or binary payloads in detail responses  
**Scale/Scope**: One operator-facing diagnostics story for MoonMind.Run task detail and failed-step Resume observability

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Work stays in MoonMind orchestration/detail surfaces and does not reimplement agent cognition.
- **II. One-Click Agent Deployment**: PASS. No new external service or required secret is planned.
- **III. Avoid Vendor Lock-In**: PASS. Diagnostics are MoonMind task/run concepts, not provider-specific behavior.
- **IV. Own Your Data**: PASS. Data is derived from existing operator-owned execution records and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime mutation or checked-in skill source changes are planned.
- **VI. Scientific Method / Tests as Anchor**: PASS. Remaining gaps use test-first verification before implementation.
- **VII. Powerful Runtime Configurability**: PASS. No hardcoded runtime configuration change is planned.
- **VIII. Modular and Extensible Architecture**: PASS. Planned work stays at execution-detail projection, schema, and UI boundaries.
- **IX. Resilient by Default**: PASS. Resume diagnostics remain explicit and do not silently infer recovery behavior.
- **X. Facilitate Continuous Improvement**: PASS. Plan preserves structured outcome and next-step traceability.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan derives from `spec.md` and preserves all FR/SC/source mappings.
- **XII. Canonical Documentation Separation**: PASS. Planning details remain in `specs/350-operator-observability-diagnostics/`, not canonical docs.
- **XIII. Pre-release Compatibility Policy**: PASS. No compatibility alias expansion is planned beyond preserving current input-shape tolerance; unsupported semantic drift should fail or degrade visibly.

Post-design re-check: PASS. Generated design artifacts preserve the same constraints and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/350-operator-observability-diagnostics/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── execution-target-diagnostics-contract.md
└── tasks.md             # Created later by /speckit.tasks
```

### Source Code (repository root)

```text
api_service/
├── api/routers/executions.py
└── tests via tests/unit/api/routers/test_executions.py

frontend/src/
├── entrypoints/task-detail.tsx
├── entrypoints/task-detail.test.tsx
└── generated/openapi.ts

moonmind/
├── schemas/temporal_models.py
├── workflows/tasks/prepared_context.py
├── workflows/tasks/task_contract.py
└── workflows/temporal/service.py

tests/
├── integration/schemas/test_execution_target_diagnostics_boundary.py
├── integration/temporal/
└── unit/workflows/
```

**Structure Decision**: Use the existing full-stack execution detail structure: backend projection and schema in `api_service`/`moonmind`, frontend rendering in `frontend/src/entrypoints/task-detail.tsx`, and focused unit plus hermetic integration tests under existing test folders.

## Complexity Tracking

No constitution violations or complexity exceptions are planned.
