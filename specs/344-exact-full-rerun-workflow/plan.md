# Implementation Plan: Exact Full Rerun Workflow

**Branch**: `344-exact-full-rerun-workflow` | **Date**: 2026-05-13 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:5918c7c6-d387-42cb-b505-bee2d80f5a2e/repo/specs/344-exact-full-rerun-workflow/spec.md`

## Summary

MM-645 requires Rerun on an eligible failed execution to be an exact full rerun: no authoring form, unchanged reuse of the original task input snapshot, explicit `exact_full_rerun` recovery provenance with pinned source workflow/run IDs, full from-beginning execution, and no imported Resume progress. Repo inspection found related foundations already present: terminal action capability gating, task input snapshot descriptors, `RequestRerun`, full-rerun parameter sanitization, snapshot persistence, and UI tests for exact no-mutation payloads. The remaining delivery work is to remove the authoring-form detour for exact Rerun, ensure the backend creates the new execution from the authoritative snapshot with `exact_full_rerun` provenance, and add verification-first unit and hermetic integration coverage proving unchanged snapshot reuse and no progress import.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/entrypoints/task-detail.tsx` links Rerun to `/tasks/new?rerunExecutionId=...`; `frontend/src/entrypoints/task-create.tsx` renders a Rerun Task form | Route exact Rerun through a direct recovery action that does not open task authoring | unit + integration |
| FR-002 | partial | `moonmind/workflows/temporal/service.py` builds rerun parameters from canonical parameters; `api_service/api/routers/executions.py` persists a new rerun snapshot | Reuse the source authoritative task input snapshot unchanged for exact rerun input and prove no mutation payload is required | unit + integration |
| FR-003 | missing | No-mutation `RequestRerun` path calls `_full_rerun_parameters(record.parameters)` without recovery provenance | Add `exact_full_rerun` recovery provenance to exact rerun submissions | unit + integration |
| FR-004 | missing | `_full_retry_recovery_from_patch()` validates source IDs only when a patch provides recovery; exact no-mutation rerun sends no patch | Derive and pin source workflow/run IDs for exact rerun provenance server-side | unit + integration |
| FR-005 | implemented_unverified | `_full_rerun_parameters()` strips task run IDs and rerun creation creates a new execution; no MM-645 end-to-end proof found | Add verification tests first; implementation contingency if full execution path is not clearly from-beginning | integration |
| FR-006 | partial | Current exact Rerun still opens `/tasks/new` Rerun Task form before submit | Replace exact Rerun authoring navigation with direct request flow while keeping editable retry separate | unit + integration |
| FR-007 | implemented_unverified | `_strip_resume_reference_parameters()` removes resume/progress fields; related tests cover resume cleanup in update paths | Add exact rerun verification tests proving no preserved progress, resume refs, or checkpoints are imported | unit + integration |
| FR-008 | partial | Snapshot build/persistence preserves rich authored fields, but exact rerun does not yet prove unchanged source snapshot reuse | Ensure exact rerun uses source authoritative snapshot as the input source and preserves authored details unchanged | unit + integration |
| FR-009 | implemented_verified | `api_service/api/routers/executions.py` disables edit/rerun when `task_input_snapshot_ref` is missing; tests cover `original_task_input_snapshot_missing` | No new implementation; preserve behavior while changing Rerun route | final verify |
| FR-010 | implemented_unverified | `spec.md` preserves MM-645 and original preset brief; downstream artifacts not yet generated | Preserve MM-645 through plan, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | partial | Rerun action exists but opens authoring form | Direct action test from failed execution creates new execution without authoring route | integration |
| SCN-002 | partial | Snapshot descriptors exist; unchanged reuse not proven | Verify exact rerun input identity/content equals source snapshot | integration |
| SCN-003 | missing | Exact no-mutation path lacks explicit recovery provenance | Verify `exact_full_rerun` with source workflow/run IDs | unit + integration |
| SCN-004 | implemented_unverified | Parameter stripping suggests from-beginning execution; no focused scenario proof | Verify full from-beginning execution parameters omit task run/progress carryover | unit + integration |
| SCN-005 | implemented_unverified | Resume cleanup helpers exist; exact rerun scenario coverage missing | Verify no completed progress, preserved outputs, or resume checkpoint refs in exact rerun | unit + integration |
| SC-001 | partial | Rerun creates a request but currently through authoring page | Add UI/API evidence for 100% direct exact rerun action in validation scenarios | integration |
| SC-002 | partial | Snapshot creation exists; unchanged source snapshot reuse not proven | Add exact snapshot identity/content validation | integration |
| SC-003 | missing | Exact provenance absent | Add provenance validation | unit + integration |
| SC-004 | implemented_unverified | Sanitization exists but no focused MM-645 test | Add no-progress-import validation | unit + integration |
| SC-005 | implemented_unverified | `spec.md` preserves MM-645 | Continue traceability through remaining artifacts | final verify |
| DESIGN-REQ-001 | missing | Exact no-mutation rerun lacks recovery provenance | Add exact provenance contract and server-side derivation | unit + integration |
| DESIGN-REQ-002 | implemented_unverified | `_strip_resume_reference_parameters()` removes resume fields | Add exact rerun no-progress verification | unit + integration |
| DESIGN-REQ-003 | partial | Authoritative snapshot model exists; exact source snapshot reuse remains unproven | Reuse/preserve source snapshot for exact rerun and test rich authored input | unit + integration |
| DESIGN-REQ-004 | partial | Existing RequestRerun can rerun, but authoring form and provenance gaps remain | Implement exact full rerun contract end to end | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Temporal Python SDK service boundaries, Pydantic v2, React, TanStack Query, Zod, generated OpenAPI types  
**Storage**: Existing Temporal execution records, canonical execution parameters/memo/search attributes, and Temporal artifact metadata/content store; no new persistent database tables planned  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; focused Python pytest and Vitest commands during iteration  
**Integration Testing**: `./tools/test_integration.sh` for hermetic integration_ci; targeted `pytest tests/integration/temporal ... -m integration_ci` equivalents during iteration when available  
**Target Platform**: MoonMind local/server deployment with Mission Control web UI and Temporal-backed workflow execution  
**Project Type**: Full-stack web control plane with Temporal workflow orchestration  
**Performance Goals**: Exact rerun action should complete normal request validation without additional user-edit round trips; no new long-running synchronous work beyond existing execution creation and artifact lookup  
**Constraints**: Do not create new persistence; keep recovery intents distinct; preserve in-flight Temporal payload compatibility for existing update names; do not import resume progress into exact rerun; keep Jira issue MM-645 traceable  
**Scale/Scope**: One runtime story covering eligible failed-execution Rerun from Mission Control through API/service execution creation and verification evidence

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Uses existing Temporal/Mission Control orchestration surfaces; no new agent cognition layer. |
| II. One-Click Agent Deployment | PASS | No new required external services or secrets. |
| III. Avoid Vendor Lock-In | PASS | Recovery semantics are internal workflow contracts, not vendor-specific agent behavior. |
| IV. Own Your Data | PASS | Reuses operator-controlled task input snapshot artifacts and execution records. |
| V. Skills Are First-Class | PASS | Planning artifacts preserve MoonSpec workflow expectations without changing skill runtime. |
| VI. Replaceable Scaffolding | PASS | Keeps recovery behavior behind existing request/update contracts and tests. |
| VII. Runtime Configurability | PASS | No new hardcoded deployment config planned. |
| VIII. Modular Architecture | PASS | Work stays within task detail/create UI, execution router, and Temporal execution service boundaries. |
| IX. Resilient by Default | PASS | Requires retry-safe exact rerun creation and no ambiguous progress carryover. |
| X. Continuous Improvement | PASS | Verification evidence and traceability are explicit. |
| XI. Spec-Driven Development | PASS | Work proceeds from `spec.md`; tests are planned before implementation. |
| XII. Canonical Docs vs Migration Backlog | PASS | Planning artifacts live under `specs/344-exact-full-rerun-workflow`; canonical docs remain source requirements only. |
| XIII. Pre-Release Compatibility Policy | PASS | No compatibility aliases planned; existing `RequestRerun` contract remains, with stricter exact-rerun semantics. |

## Project Structure

### Documentation (this feature)

```text
specs/344-exact-full-rerun-workflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── exact-full-rerun-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output, not created by this plan step
```

### Source Code (repository root)

```text
api_service/
├── api/routers/executions.py
└── db/models.py

moonmind/
├── schemas/temporal_models.py
├── workflows/tasks/task_contract.py
└── workflows/temporal/service.py

frontend/src/
├── entrypoints/task-detail.tsx
├── entrypoints/task-detail.test.tsx
├── entrypoints/task-create.tsx
├── entrypoints/task-create.test.tsx
└── lib/temporalTaskEditing.ts

tests/
├── unit/api/routers/test_executions.py
├── unit/workflows/temporal/test_temporal_service.py
├── unit/workflows/tasks/test_task_contract.py
└── integration/temporal/
```

**Structure Decision**: Use the existing full-stack MoonMind control-plane layout. The story crosses Mission Control UI, API route serialization/update handling, Temporal execution service rerun creation, task recovery contracts, and unit/hermetic integration tests.

## Complexity Tracking

No constitution violations are planned.
