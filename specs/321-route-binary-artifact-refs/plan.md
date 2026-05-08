# Implementation Plan: Route Binary Inputs Through Authorized Artifact Refs

**Branch**: `321-route-binary-artifact-refs` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/321-route-binary-artifact-refs/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` initially failed because the managed job branch is `run-jira-orchestrate-for-mm-628-route-bi-6bcc12c5`, not a numeric feature branch. It was rerun with `SPECIFY_FEATURE=321-route-binary-artifact-refs`, resolving this feature directory and creating `plan.md`.

## Summary

MM-628 requires browser-selected binary inputs to be uploaded through MoonMind artifact APIs, finalized before task submission, submitted to execution only as structured refs, previewed/downloaded through authorized MoonMind APIs, and materialized by workers through service-authorized paths. Current code already has artifact create/upload/complete/download endpoints, frontend attachment upload ordering, task-shaped attachment validation, and worker materialization. Planning finds remaining delivery work around authorization at execution submission/linking, execution-scoped reuse, and explicit proof that worker materialization uses service authorization rather than browser-visible credentials. The implementation should add verification-first unit and integration coverage, then tighten the API/service boundaries where tests expose gaps.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-create.test.tsx` verifies `/api/artifacts` calls for objective and step attachments; `api_service/api/routers/temporal_artifacts.py` exposes `POST /api/artifacts` | no new implementation beyond preserving behavior | final traceability |
| FR-002 | implemented_verified | `moonmind/workflows/temporal/artifacts.py` stores bytes in artifact storage; UI tests assert instructions are not rewritten with attachment text | no new implementation beyond preserving behavior | final traceability |
| FR-003 | implemented_verified | `task-create.test.tsx` asserts presigned upload and `/complete` happen before `/api/executions` | no new implementation beyond preserving behavior | final traceability |
| FR-004 | implemented_unverified | `api_service/api/routers/executions.py` rejects non-`COMPLETE` artifact status before execution, but MM-628 needs focused coverage for finalized binary refs | add targeted API unit/integration tests for pending/failed/deleted artifact refs; implement only if coverage exposes gaps | unit + integration |
| FR-005 | implemented_verified | `task-create.test.tsx` and `tests/unit/api/routers/test_executions.py` verify structured attachment refs in `payload.task` and `initial_parameters` | no new implementation beyond preserving behavior | final traceability |
| FR-006 | partial | existing validation rejects missing, invalid, duplicate, oversized, wrong content-type, and unfinalized refs; authorization of submitted refs by principal/execution scope is not proven | add authorization and execution-scope validation at submission/linking boundary if missing | unit + integration |
| FR-007 | implemented_verified | `api_service/api/routers/temporal_artifacts.py` exposes metadata, presign-download, and download endpoints; frontend tests use artifact endpoint templates | no new implementation beyond preserving behavior | final traceability |
| FR-008 | partial | `TemporalArtifactService` enforces owner/service raw read access; tests cover restricted previews, but execution ownership/view permission behavior needs focused proof for task input artifacts | add preview/download authorization tests for linked input attachments and patch service/router if execution viewer permissions are incomplete | unit + integration |
| FR-009 | implemented_unverified | `moonmind/agents/codex_worker/worker.py` materializes input attachments via worker queue client; tests cover materialization and failure diagnostics but not service-principal authorization | add worker/runtime boundary test proving service-authorized artifact reads and no browser credential leakage | unit + integration |
| FR-010 | partial | execution creation links input artifacts with `link_type="input.attachment"` and stores artifact IDs on the execution record; enforcement that refs cannot be reused outside the authorized execution context is not fully proven | add execution-scoped link/reuse tests and tighten linking/read policy if needed | unit + integration |
| FR-011 | implemented_verified | `docs/Tasks/TaskArchitecture.md` target semantics are represented by task `inputAttachments`/step refs; worker materialization tests prove paths derive from target fields, not storage paths | no new implementation beyond preserving behavior | final traceability |
| FR-012 | implemented_verified | `spec.md` preserves MM-628 and the original preset brief; this plan preserves MM-628 and source design IDs | preserve through tasks, verification, commit, and PR metadata | final traceability |
| SC-001 | implemented_verified | UI tests cover upload/complete before execution submission | keep covered in final verification | final traceability |
| SC-002 | implemented_verified | UI/API tests show structured refs and no instruction rewriting | keep covered in final verification | final traceability |
| SC-003 | partial | invalid/unfinalized validation exists; unauthorized binary refs are not proven | add negative authorization and status tests | unit + integration |
| SC-004 | partial | artifact APIs enforce owner/service access; execution viewer permission coverage needs focused proof | add preview/download authorization tests | unit + integration |
| SC-005 | implemented_unverified | worker materialization tests cover download and manifest behavior, but service credential use is not proven | add service-principal materialization proof | unit + integration |
| SC-006 | implemented_verified | `spec.md`, this plan, and design artifacts preserve MM-628 and all listed design IDs | preserve through downstream artifacts | final traceability |
| DESIGN-REQ-002 | implemented_verified | artifact storage and structured refs exist; tests verify instructions are not rewritten with binary content | no new implementation beyond final verify | final traceability |
| DESIGN-REQ-007 | implemented_unverified | frontend upload orchestration exists and is tested; API finalization checks need focused binary-ref coverage | add focused API tests for finalized refs before execution | unit + integration |
| DESIGN-REQ-020 | partial | artifact service has read policies and execution links; execution ownership/view permission and execution-scoped reuse require stronger proof | add authorization/link-scope tests and implement gaps | unit + integration |
| DESIGN-REQ-022 | partial | task invariants are mostly implemented; unauthorized refs and service-authorized worker materialization remain unproven | add boundary tests and patch service/API/runtime if needed | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control attachment upload UX  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, Vitest/Testing Library, pytest, existing Temporal artifact service/router  
**Storage**: Existing Temporal artifact metadata/content store, Temporal execution records, artifact links; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; focused Python tests in `tests/unit/api/routers/test_executions.py`, `tests/unit/api/routers/test_temporal_artifacts.py`, `tests/unit/workflows/temporal/test_artifacts.py`, and `tests/unit/agents/codex_worker/test_attachment_materialization.py`; focused UI tests via `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci`; focused additions in `tests/integration/temporal/test_task_shaped_submission_normalization.py`, `tests/integration/temporal/test_temporal_artifact_authorization.py`, and/or `tests/contract/test_temporal_execution_api.py`  
**Target Platform**: MoonMind Mission Control web app and FastAPI/Temporal control plane on Linux/Docker Compose  
**Project Type**: Full-stack web application with Temporal-backed execution orchestration and managed worker materialization  
**Performance Goals**: Attachment submission validation remains linear in unique attachment refs and uses one metadata lookup for all refs; preview/download authorization adds no extra object-store round trips before permission checks  
**Constraints**: No binary payloads in workflow history or inline instructions; browser never receives long-lived object-store credentials; unauthorized or unfinalized refs fail before execution; worker materialization uses service authorization; no new persistent storage  
**Scale/Scope**: One independently testable MM-628 story covering binary artifact upload, finalized structured refs, authorized preview/download, and worker materialization boundaries

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - work keeps binary handling in MoonMind control-plane and runtime boundaries without rebuilding agent behavior.
- II. One-Click Agent Deployment: PASS - no new external service or required secret is introduced.
- III. Avoid Vendor Lock-In: PASS - artifacts and refs remain provider-neutral; browser/provider file endpoints are explicitly excluded.
- IV. Own Your Data: PASS - binary inputs stay in MoonMind-owned artifact storage and refs.
- V. Skills Are First-Class and Easy to Add: PASS - no skill source mutation or runtime skill contract changes.
- VI. Replaceable Scaffolding, Thick Contracts: PASS - plan tightens artifact contracts and tests at service/API/runtime boundaries.
- VII. Runtime Configurability: PASS - attachment policy remains controlled by existing settings.
- VIII. Modular Architecture: PASS - planned work stays in artifact service/router, task execution submission validation, frontend upload flow tests, and worker materialization boundaries.
- IX. Resilient by Default: PASS - invalid or unauthorized input refs fail before execution and worker materialization fails explicitly.
- X. Continuous Improvement: PASS - artifacts preserve evidence, planned tests, and next actions.
- XI. Spec-Driven Development: PASS - `spec.md`, `plan.md`, and design artifacts define the work before tasks or implementation.
- XII. Canonical Documentation Separation: PASS - implementation and rollout details stay under `specs/321-route-binary-artifact-refs/`.

No constitution violations are currently justified.

## Project Structure

### Documentation (this feature)

```text
specs/321-route-binary-artifact-refs/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── binary-artifact-ref-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

api_service/api/routers/
├── executions.py
└── temporal_artifacts.py

moonmind/workflows/temporal/
└── artifacts.py

moonmind/agents/codex_worker/
└── worker.py

moonmind/schemas/
└── temporal_artifact_models.py

tests/unit/api/routers/
├── test_executions.py
└── test_temporal_artifacts.py

tests/unit/agents/codex_worker/
└── test_attachment_materialization.py

tests/integration/temporal/
├── test_task_shaped_submission_normalization.py
├── test_temporal_artifact_authorization.py
└── test_temporal_artifact_auth_preview.py

tests/contract/
├── test_temporal_artifact_api.py
└── test_temporal_execution_api.py
```

**Structure Decision**: This is a full-stack artifact-boundary story. Mission Control owns browser upload orchestration, FastAPI owns artifact and execution submission validation, `TemporalArtifactService` owns authorization/read policies, the execution record owns links to input artifacts, and managed workers own service-authorized materialization.

## Complexity Tracking

No constitution violations require complexity exceptions.
