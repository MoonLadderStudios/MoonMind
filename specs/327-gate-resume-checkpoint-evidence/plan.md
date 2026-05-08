# Implementation Plan: Gate Resume on Durable Checkpoint Evidence

**Branch**: `run-jira-orchestrate-for-mm-633-gate-res-1e30fa1a` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/327-gate-resume-checkpoint-evidence/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed job branch name is not in the script's expected `NNN-feature-name` form. Planning proceeds from the active feature directory `specs/327-gate-resume-checkpoint-evidence`.

## Summary

MM-633 requires failed-step Resume to be offered and accepted only when backend-owned durable evidence proves the original task input snapshot, pinned source workflow/run, failed-step identity, completed-step refs, workspace or branch checkpoint, and plan identity are recoverable and consistent. The repository already contains Resume request/response models, a `/resume-from-failed-step` route, Task Detail UI controls, checkpoint hydration, and service validation for several source/checkpoint mismatches. The main gap is that availability is currently gated mostly by snapshot and checkpoint-ref presence, while checkpoint contents allow optional plan and workspace evidence and do not yet prove ledger-derived completed-step recoverability before Resume is exposed. Implementation should add verification-first API/service/model tests, tighten checkpoint and eligibility validation, then add hermetic integration coverage proving invalid evidence blocks before execution and valid evidence remains ref-backed.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `_build_action_capabilities()` in `api_service/api/routers/executions.py` computes `canResumeFromFailedStep`, but currently uses failed state, snapshot ref, and checkpoint ref presence rather than full checkpoint evidence validation | Move or add backend eligibility validation that evaluates hydrated/checkpoint metadata and returns disabled reasons for missing evidence | unit + integration |
| FR-002 | implemented_unverified | `_build_action_capabilities()` requires task input snapshot; `TemporalExecutionService.create_failed_step_resume_execution()` rejects missing source snapshot | Add focused tests proving missing snapshot blocks both availability and submission | unit |
| FR-003 | implemented_unverified | `ResumeCheckpointSourceModel` requires `workflowId` and `runId`; service validates both against the source record before creating a resumed execution | Add tests for missing and mismatched source identity with operator-readable reasons | unit + integration |
| FR-004 | partial | `ResumeCheckpointFailedStepModel` requires logical step ID/order/attempt, but availability can be true before validating checkpoint contents and no step-ledger cross-check is present | Validate checkpoint failed-step identity against source ledger or canonical recovery metadata before exposing/starting Resume | unit + integration |
| FR-005 | partial | `ResumeCheckpointPreservedStepModel` requires artifact refs when preserved steps are present; no evidence proves every completed prior step has refs before eligibility | Compare source completed-step ledger state to preserved step refs and block when a completed prior step lacks recoverable refs | unit + integration |
| FR-006 | missing | `ResumeCheckpointModel.resume_workspace` currently defaults to `{}` and tests allow empty optional resume sections | Require workspace/branch/commit/equivalent checkpoint evidence for eligibility and request acceptance | unit + integration |
| FR-007 | missing | `planRef` and `planDigest` are optional in `ResumeCheckpointModel`; service passes `record.plan_ref` and checkpoint digest when present | Require plan identity or digest and validate it against source execution plan metadata | unit + integration |
| FR-008 | missing | Resume execution creation uses an idempotency key, but checkpoint creation/write idempotency for step-boundary evidence is not implemented or verified | Define checkpoint write idempotency key/semantics and add tests for repeated checkpoint writes resolving to the same evidence | unit + integration |
| FR-009 | partial | Temporal payload policy discourages large inline bodies; checkpoint model still allows arbitrary `resumeWorkspace` dict and artifact dict values | Enforce compact refs for large/binary checkpoint content and reject inline checkpoint payload bodies where applicable | unit |
| FR-010 | partial | Preserved-step model rejects preserved steps without artifacts, but empty preserved steps and lack of ledger comparison can still pass | Validate that every completed step intended for preservation has recoverable output refs and state checkpoint evidence | unit + integration |
| FR-011 | partial | Route hydrates checkpoint artifacts and service rejects invalid payload, checkpoint ref mismatch, run mismatch, workflow mismatch, and snapshot mismatch | Expand missing/stale/unauthorized/corrupted/inconsistent evidence coverage and preserve distinct disabled/failure reasons before execution | unit + integration |
| FR-012 | implemented_unverified | Service raises validation errors before creating a resumed execution; tests cover selected mismatch cases and no full-rerun fallback is present | Add explicit assertions that invalid Resume creates no new execution and never triggers full rerun behavior | unit + integration |
| FR-013 | implemented_unverified | `spec.md` preserves MM-633 and the canonical Jira preset brief | Preserve traceability through plan, research, data model, contract, quickstart, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | partial | Availability can be true with only snapshot and checkpoint-ref presence | Add valid-evidence availability test matrix with full required evidence | unit + integration |
| SCN-002 | partial | Missing checkpoint and missing snapshot reasons exist; other missing evidence reasons do not | Add one disabled reason per required evidence category | unit |
| SCN-003 | partial | Route/service fail before creating execution for malformed or mismatched checkpoint payloads | Add stale, unauthorized, corrupted, inconsistent plan, and missing workspace cases | unit + integration |
| SCN-004 | partial | Preserved steps can carry artifact refs, but no source-ledger completeness check exists | Add source ledger comparison and preserved-step ref tests | unit + integration |
| SCN-005 | partial | Payload policy tests cover general large-body refs; checkpoint-specific inline payload guard is absent | Add checkpoint-specific compact-ref validation | unit |
| SCN-006 | missing | Checkpoint write idempotency is not represented for Resume checkpoint evidence | Add checkpoint write/create idempotency design and tests | unit + integration |
| SCN-007 | missing | Plan identity/digest is optional | Require and test plan identity/digest mismatch blocking | unit + integration |
| SC-001 | partial | Backend computes a boolean but from insufficient evidence | Expand backend evidence matrix | unit + integration |
| SC-002 | partial | Valid resume path includes snapshot, workflow/run, failed step, and checkpoint ref; workspace and plan are optional | Tighten model/service requirements and test complete valid evidence | unit + integration |
| SC-003 | partial | Some invalid evidence cases block; many required cases are generic or untested | Add complete invalid evidence matrix with operator-readable reasons | unit + integration |
| SC-004 | implemented_unverified | Resume service creates linked execution only on success and errors otherwise | Add explicit no-created-execution/no-full-rerun assertions | unit + integration |
| SC-005 | missing | No checkpoint-write retry semantics are implemented for this evidence | Add idempotent write behavior and tests | unit + integration |
| SC-006 | partial | General payload policy exists; checkpoint-specific large/binary inline prevention is incomplete | Add checkpoint ref-only validation and tests | unit |
| SC-007 | implemented_unverified | `spec.md` and this plan preserve MM-633 and source IDs | Preserve traceability through all artifacts and final verification | final verify |
| DESIGN-REQ-001 | partial | Snapshot/checkpoint-ref gating exists; full evidence gating is incomplete | Implement complete backend eligibility evidence evaluation | unit + integration |
| DESIGN-REQ-002 | partial | Resume source pinning and checkpoint mismatch validation exist; completed refs/workspace/plan evidence are incomplete | Tighten checkpoint model and service validation; add ledger comparison | unit + integration |
| DESIGN-REQ-003 | partial | Prepared refs and preserved refs models exist; workspace and idempotent checkpoint writes are incomplete | Add checkpoint write semantics and compact evidence validation | unit + integration |
| DESIGN-REQ-004 | partial | Resume intent and source pinning exist; checkpointed progress and no silent preserved-step behavior need proof | Add pre-execution failure and no-fallback integration coverage | unit + integration |

Status summary: 4 missing, 20 partial, 6 implemented_unverified, 0 implemented_verified.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI where availability display is affected
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, Zod, Vitest, pytest
**Storage**: Existing Temporal execution records, memo/search attributes, Temporal artifact metadata/content store, task input snapshot artifacts, step ledger/checkpoint artifacts; no new persistent database table planned
**Unit Testing**: `./tools/test_unit.sh` for full unit verification; focused Python tests under `tests/unit/api/routers/test_executions.py` and `tests/unit/workflows/temporal/test_temporal_service.py`; focused UI tests through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` if display copy changes
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage; targeted workflow/step-ledger tests under `tests/integration/workflows/temporal/**` where no external credentials are required
**Target Platform**: MoonMind API service, Temporal execution service, and Mission Control task detail surface in the existing containerized deployment
**Project Type**: Web service plus frontend dashboard with Temporal-backed orchestration
**Performance Goals**: Eligibility computation remains bounded enough for task detail polling and recovery submission; checkpoint payloads stay compact by keeping large/binary data behind refs
**Constraints**: Preserve in-flight Temporal payload compatibility or explicitly version any breaking change; fail before execution on invalid evidence; do not add hidden full-rerun fallback; do not embed large checkpoint content in workflow history; preserve source execution immutability
**Scale/Scope**: One runtime story for `MoonMind.Run` failed-step Resume evidence gating; excludes implementing the full resumed step execution path beyond eligibility, checkpoint validation, and pre-execution failure guarantees

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The work strengthens MoonMind orchestration around existing agents rather than adding a new agent engine.
- **II. One-Click Agent Deployment**: PASS. No new external service, secret, or deployment prerequisite is planned.
- **III. Avoid Vendor Lock-In**: PASS. Resume evidence is MoonMind task orchestration state, not provider-specific behavior.
- **IV. Own Your Data**: PASS. Evidence remains in MoonMind-owned execution records and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime contract changes are planned.
- **VI. Scaffolds Are Replaceable, Tests Are the Anchor**: PASS. The plan requires tests first around evidence and failure contracts.
- **VII. Powerful Runtime Configurability**: PASS. Existing action capability and feature flag surfaces remain observable.
- **VIII. Modular and Extensible Architecture**: PASS. Planned changes stay inside existing schema, API route, Temporal service, step ledger/checkpoint, and UI boundaries.
- **IX. Resilient by Default**: PASS. The story directly improves deterministic recovery failure behavior and requires boundary coverage.
- **X. Facilitate Continuous Improvement**: PASS. Artifacts preserve outcome and traceability for later verification.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Planning derives from the single-story spec and keeps all requirements visible.
- **XII. Documentation Separation**: PASS. Planning artifacts stay under `specs/327-gate-resume-checkpoint-evidence/`; canonical docs remain desired-state references.
- **XIII. Pre-Release Compatibility Policy**: PASS. The plan tightens internal evidence contracts and requires explicit handling for compatibility-sensitive payload changes instead of hidden aliases.

Re-check after Phase 1 design: PASS. `research.md`, `data-model.md`, `contracts/resume-evidence.md`, and `quickstart.md` introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/327-gate-resume-checkpoint-evidence/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── resume-evidence.md
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── executions.py

moonmind/
├── schemas/
│   ├── temporal_models.py
│   └── temporal_payload_policy.py
└── workflows/
    └── temporal/
        ├── service.py
        ├── step_ledger.py
        └── workflows/
            └── run.py

frontend/
└── src/
    ├── entrypoints/
    │   ├── task-detail.tsx
    │   └── task-detail.test.tsx
    └── generated/
        └── openapi.ts

tests/
├── unit/
│   ├── api/routers/test_executions.py
│   ├── workflows/temporal/test_temporal_service.py
│   └── schemas/test_temporal_payload_policy.py
└── integration/
    └── workflows/temporal/workflows/test_run_resume_from_failed_step.py
```

**Structure Decision**: Use the existing API route, schema, Temporal service, step ledger, and Task Detail UI surfaces. Add verification-first unit tests around evidence validation and add hermetic integration coverage for the Resume boundary before implementation changes.

## Complexity Tracking

No constitution violations require justification.
