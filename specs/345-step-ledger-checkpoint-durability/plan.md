# Implementation Plan: Step Ledger Checkpoint Durability

**Branch**: `change-jira-issue-mm-646-to-status-in-pr-886ebc67` | **Date**: 2026-05-13 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:017bbd8c-2454-4c77-ac2c-f8d42e1c7916/repo/specs/345-step-ledger-checkpoint-durability/spec.md`

## Summary

MM-646 requires `MoonMind.Run` to produce durable prepared-input refs, per-step semantic output refs, workspace or branch checkpoint refs, and explicit step-level Resume eligibility evidence. Current repo evidence shows prepared-input manifests, step ledger rows, output artifact projection, managed-session checkpoint artifacts, and Resume checkpoint validation already exist, but parent `MoonMind.Run` does not yet durably publish one canonical resume checkpoint at step boundaries or mark completed steps Resume-ineligible when refs/checkpoints are missing. The implementation should add tests first around the workflow/helper boundary, then fill the missing parent-owned checkpoint and eligibility projection behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/workflows/tasks/prepared_context.py`; `run.py` injects prepared context into step metadata but does not durably record prepared refs after prepare | Add parent-owned prepared-input evidence capture into resume checkpoint state | unit + integration |
| FR-002 | partial | `run.py::_record_step_result_evidence`; `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covers artifact projection | Extend semantic output refs into checkpoint evidence and preservation eligibility | unit + integration |
| FR-003 | missing | Managed session checkpoints exist, but parent step rows do not reliably carry `stateCheckpointRef` for completed steps | Add parent-owned state checkpoint ref emission around mutating step boundaries | unit + integration |
| FR-004 | missing | No parent-level idempotent checkpoint identity/helper found for repeated step-boundary writes | Add deterministic/idempotent checkpoint writer keyed by source run, logical step, and attempt | unit |
| FR-005 | partial | Existing artifact/ref patterns avoid inline large payloads; no checkpoint-specific guard test for parent evidence | Add compact checkpoint payload validation and tests that large/binary content stays behind refs | unit + integration |
| FR-006 | partial | `ResumeCheckpointModel` validates hydrated evidence; source production path remains incomplete | Produce eligibility evidence from durable refs/checkpoints and not logs/UI state | unit + integration |
| FR-007 | partial | Step ledger rows expose artifacts and preserved provenance, but not explicit eligibility marker/reason for source completed steps | Add step-level Resume preservation eligibility metadata | unit |
| FR-008 | missing | No explicit source ledger reason for completed steps missing recovery evidence | Add bounded ineligible reason when refs/checkpoints are missing | unit + integration |
| FR-009 | implemented_unverified | `materialize_preserved_steps()` and `run.py` keep parent step ledger provenance; delegated child refs exist | Add boundary tests proving parent remains source of truth when child/runtime returns checkpoint refs | unit + integration |
| FR-010 | implemented_verified | `spec.md` preserves `MM-646` and the original Jira preset brief | Preserve through plan, tasks, verification, commits, and PR metadata | final verify |
| SCN-001 | partial | Prepared manifest helper exists; durable post-prepare run evidence missing | Add successful prepare checkpoint scenario | integration |
| SCN-002 | partial | Step artifact projection exists; checkpoint-preservation evidence incomplete | Add successful step evidence scenario | integration |
| SCN-003 | missing | No parent checkpoint for workspace-mutating step boundary found | Add workspace/branch/commit checkpoint scenario | integration |
| SCN-004 | missing | No idempotent checkpoint retry proof | Add repeated boundary write scenario | unit |
| SCN-005 | partial | Ref-only artifact patterns exist; checkpoint-specific proof missing | Add no-inline-large-payload scenario | unit + integration |
| SCN-006 | missing | Source completed step ineligibility reason missing | Add missing evidence ineligibility scenario | unit + integration |
| SCN-007 | implemented_unverified | Child workflow refs and preserved provenance exist | Add delegated-step checkpoint ownership scenario | integration |
| SC-001 | partial | Prepared context unit coverage exists | Extend to durable prepared refs in checkpoint | unit + integration |
| SC-002 | partial | Existing step ledger output refs tests cover some output artifacts | Extend to semantic output refs required for preservation | unit |
| SC-003 | missing | No parent state checkpoint row/ref emission evidence found | Add code and tests | unit + integration |
| SC-004 | missing | No idempotent checkpoint identity tests found | Add helper and tests | unit |
| SC-005 | partial | Bounded artifact conventions exist | Add checkpoint no-inline tests | unit + integration |
| SC-006 | missing | No explicit ineligible marker/reason found | Add code and tests | unit + integration |
| SC-007 | implemented_unverified | Managed/child outputs include checkpoint refs, parent projection needs proof | Add parent-owned delegated-step evidence tests | integration |
| SC-008 | implemented_verified | `spec.md` preserves Jira and source IDs | Preserve through all downstream artifacts | final verify |
| DESIGN-REQ-001 | partial | `run.py` owns live step ledger; durable Resume refs incomplete | Add durable checkpoint evidence into parent-owned ledger/checkpoint flow | unit + integration |
| DESIGN-REQ-002 | partial | Prepared refs can be built but are not durably recorded after prepare | Persist prepared refs into checkpoint evidence | unit + integration |
| DESIGN-REQ-003 | partial | Step output artifact projection exists | Bind semantic output refs to checkpoint/preservation eligibility | unit + integration |
| DESIGN-REQ-004 | missing | No parent idempotent state checkpoint emission found | Add idempotent checkpoint emission and compact ref-only payloads | unit + integration |
| DESIGN-REQ-005 | missing | No completed-step ineligible reason found | Mark completed steps without refs/checkpoint as Resume-ineligible | unit |
| DESIGN-REQ-006 | implemented_unverified | Parent ledger and child refs exist; checkpoint ownership not fully proven | Add delegated child boundary coverage | integration |
| DESIGN-REQ-007 | missing | Eligibility consumes checkpoint model but source production not complete | Produce evidence required before Resume can be offered | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, FastAPI execution service models, existing Temporal artifact service/helpers  
**Storage**: Existing Temporal workflow state/history, Temporal artifact metadata/content store, execution source records; no new persistent database table planned  
**Unit Testing**: pytest via `./tools/test_unit.sh`  
**Integration Testing**: pytest hermetic Temporal/API suites via `./tools/test_integration.sh` where marked `integration_ci`; focused local Temporal workflow tests where required by runtime boundary coverage  
**Target Platform**: MoonMind server/workers on Linux containers  
**Project Type**: Python service and Temporal workflow system  
**Performance Goals**: checkpoint evidence stays bounded and ref-based; no large or binary payloads embedded inline in workflow histories or eligibility summaries  
**Constraints**: workflow/activity payload changes are compatibility-sensitive; checkpoint writes must be retry-safe and idempotent; no raw logs/UI reconstruction may be required for Resume eligibility  
**Scale/Scope**: one `MoonMind.Run` story covering prepared refs, completed-step refs, state checkpoints, and delegated-step parent ownership

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The plan preserves provider/runtime delegation and adds parent orchestration evidence rather than rebuilding agent behavior.
- II. One-Click Agent Deployment: PASS. Uses existing local Temporal/artifact infrastructure and test runners; no new mandatory external service.
- III. Avoid Vendor Lock-In: PASS. Evidence is parent workflow and artifact-ref based, not provider-specific.
- IV. Own Your Data: PASS. Durable refs and checkpoint artifacts stay in MoonMind/operator-owned stores.
- V. Skills Are First-Class: PASS. Step evidence stays runtime-neutral and compatible with skill and agent-runtime steps.
- VI. Scientific Method: PASS. Unit and integration tests are explicit before implementation.
- VII. Runtime Configurability: PASS. No hardcoded operator settings planned; behavior derives from task/run evidence.
- VIII. Modular Architecture: PASS. Work should stay in workflow/helper/model boundaries already owning step ledger, prepared context, and resume checkpoint validation.
- IX. Resilient by Default: PASS with required coverage. Retry-safe checkpoint writes and workflow-boundary tests are central requirements.
- X. Continuous Improvement: PASS. Final verification will produce structured evidence and residual risk.
- XI. Spec-Driven Development: PASS. This plan follows the MM-646 spec and preserves traceability.
- XII. Documentation Separation: PASS. Planning and rollout details stay in this feature directory, not canonical docs.
- XIII. Pre-Release Delete, Don't Deprecate: PASS. Do not add compatibility aliases for internal names; preserve Temporal worker-bound invocation compatibility through additive/versioned checkpoint payload evolution only.

## Project Structure

### Documentation (this feature)

```text
specs/345-step-ledger-checkpoint-durability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── step-ledger-checkpoint-evidence.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    ├── tasks/
    │   └── prepared_context.py
    └── temporal/
        ├── step_ledger.py
        ├── service.py
        └── workflows/
            └── run.py

tests/
├── unit/
│   └── workflows/
│       ├── tasks/
│       │   └── test_prepared_context.py
│       └── temporal/
│           ├── test_step_ledger.py
│           ├── test_temporal_service.py
│           └── workflows/
│               ├── test_run_resume_from_failed_step.py
│               └── test_run_step_ledger.py
└── integration/
    ├── temporal/
    │   └── test_backend_resume_eligibility.py
    └── workflows/
        └── temporal/
            └── workflows/
                └── test_run_resume_from_failed_step.py
```

**Structure Decision**: Keep the implementation in existing Temporal workflow, schema, and helper modules. Add tests at the helper/model layer and at the real `MoonMind.Run` or Temporal execution service boundary.

## Complexity Tracking

No constitution violations are planned.
