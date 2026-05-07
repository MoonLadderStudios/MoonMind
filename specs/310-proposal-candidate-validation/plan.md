# Implementation Plan: Proposal Candidate Validation

**Branch**: `310-proposal-candidate-validation` | **Date**: 2026-05-07 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:178269a2-85d8-40b9-a55f-ae8a6ca25e2d/repo/specs/310-proposal-candidate-validation/spec.md`

## Summary

Implement `MM-596` by tightening proposal candidate generation and submission validation so generated follow-up tasks are side-effect-free until the trusted submission boundary, validate against the canonical task contract, preserve explicit skill selector and reliable preset provenance metadata, reject `tool.type=agent_runtime`, and report redacted validation errors before any delivery side effect. Current implementation evidence now shows the required behavior in `TemporalProposalActivities` and `TaskProposalService`, with focused activity/service unit tests and workflow-boundary unit tests covering the proposal activity sequence. No further implementation work is planned from the plan stage; downstream verification should keep the focused unit suite and hermetic integration strategy explicit.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `TemporalProposalActivities.proposal_generate()` derives candidates from run parameters and evidence; `tests/unit/workflows/temporal/test_proposal_activities.py` covers proposal generation behavior. | No new implementation. | final unit verify |
| FR-002 | implemented_verified | `proposal_submit()` validates stamped candidate task requests before service calls; `TaskProposalService._prepare_task_create_request()` validates canonical task payloads. | No new implementation. | final unit verify |
| FR-003 | implemented_verified | `TaskProposalService._reject_agent_runtime_tool_selectors()` and activity validation reject `agent_runtime`; tests accept `tool.type=skill` and reject `tool.type=agent_runtime`. | No new implementation. | final unit verify |
| FR-004 | implemented_verified | `proposal_generate()` preserves task and step skill selectors while stripping body/materialization-like fields; focused tests cover preservation and non-embedding. | No new implementation. | final unit verify |
| FR-005 | implemented_verified | `proposal_generate()` preserves reliable `authoredPresets` and `steps[].source`; service tests cover preview/promotion provenance behavior. | No new implementation. | final unit verify |
| FR-006 | implemented_verified | Generation tests cover absent provenance and assert no fabricated `authoredPresets` or step source data. | No new implementation. | final unit verify |
| FR-007 | implemented_verified | `proposal_generate()` returns candidate dictionaries only; focused tests exercise generation without service/repository delivery calls. | No new implementation. | final unit verify |
| FR-008 | implemented_verified | `moonmind/workflows/temporal/activity_runtime.py` maps `proposal.generate` and `proposal.submit` separately; workflow tests assert distinct scheduled activity types and order. | No new implementation. | workflow-boundary unit verify |
| FR-009 | implemented_verified | `proposal_submit()` returns bounded errors for invalid candidates and skips service calls for rejected payloads; tests cover malformed skill selectors and unsafe tool types. | No new implementation. | final unit verify |
| FR-010 | implemented_verified | `spec.md`, this plan, `research.md`, `quickstart.md`, `tasks.md`, and verification evidence preserve `MM-596`. | Preserve in PR metadata. | final traceability verify |
| SC-001 | implemented_verified | Focused proposal generation tests demonstrate no repository, task-creation, proposal-delivery, or external-tracker calls during generation. | No new implementation. | final unit verify |
| SC-002 | implemented_verified | Activity and service tests prove candidate validation runs before proposal service/repository calls. | No new implementation. | final unit verify |
| SC-003 | implemented_verified | `tests/unit/workflows/temporal/test_proposal_activities.py` and `tests/unit/workflows/task_proposals/test_service.py` cover accepted `tool.type=skill` and rejected `tool.type=agent_runtime`. | No new implementation. | final unit verify |
| SC-004 | implemented_verified | Skill selector preservation tests assert selectors remain compact and no skill bodies/runtime materialization state are embedded. | No new implementation. | final unit verify |
| SC-005 | implemented_verified | Provenance tests cover both preservation from reliable evidence and non-fabrication when evidence is absent. | No new implementation. | final unit verify |
| SC-006 | implemented_verified | `tests/unit/workflows/temporal/workflows/test_run_proposals.py` asserts distinct `proposal.generate` and `proposal.submit` workflow activity scheduling. | No new implementation. | workflow-boundary unit verify |
| SC-007 | implemented_verified | `MM-596` and DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, and DESIGN-REQ-032 remain visible in MoonSpec artifacts and verification notes. | Preserve in PR metadata. | final traceability verify |
| DESIGN-REQ-007 | implemented_verified | Generation uses bounded candidate payloads and keeps large/untrusted context behind existing run evidence; generation tests cover no side effects and skill-body exclusion. | No new implementation. | final unit verify |
| DESIGN-REQ-008 | implemented_verified | Submission validates candidates, preserves explicit skill intent, rejects malformed skill fields, and calls trusted service only after validation. | No new implementation. | final unit verify |
| DESIGN-REQ-017 | implemented_verified | Service validation normalizes candidate `taskCreateRequest` through the canonical task payload contract. | No new implementation. | final unit verify |
| DESIGN-REQ-018 | implemented_verified | Activity and service validation reject `tool.type=agent_runtime` while accepting `tool.type=skill`. | No new implementation. | final unit verify |
| DESIGN-REQ-019 | implemented_verified | Proposal generation preserves explicit task/step skill selectors without embedding skill bodies or materialization state. | No new implementation. | final unit verify |
| DESIGN-REQ-032 | implemented_verified | Activity catalog/runtime mapping and workflow tests prove separate generation and trusted submission boundaries. | No new implementation. | workflow-boundary unit verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, SQLAlchemy async ORM, FastAPI task/proposal routers, existing `CanonicalTaskPayload` and `TaskProposalService`  
**Storage**: Existing `task_proposals` and proposal notification tables only; no new persistent storage  
**Unit Testing**: pytest through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused iteration through `tests/unit/workflows/temporal/test_proposal_activities.py`, `tests/unit/workflows/task_proposals/test_service.py`, and `tests/unit/workflows/temporal/workflows/test_run_proposals.py`
**Integration Testing**: hermetic integration tests through `./tools/test_integration.sh` when Docker is available; workflow-boundary proposal coverage remains in the unit suite for managed-agent environments without Docker
**Target Platform**: MoonMind server and Temporal worker runtime  
**Project Type**: Python service and Temporal workflow/activity implementation  
**Performance Goals**: Candidate validation remains bounded per generated candidate and must not materially increase proposal-stage latency for normal proposal counts.  
**Constraints**: Generation is best-effort and must not fail the parent run; validation errors must be redacted and visible; no raw credentials or large skill bodies may enter workflow history or proposal payloads.  
**Scale/Scope**: One proposal-generation story covering the generated candidate contract, submission boundary validation, skill/provenance preservation, and generation/submission boundary separation.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Work stays in Temporal activity/service boundaries and uses existing agent/provider orchestration surfaces.
- **II. One-Click Agent Deployment**: PASS. No new external dependency or required cloud service is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Validation targets MoonMind's canonical task contract, not a vendor-specific provider API.
- **IV. Own Your Data**: PASS. Generated candidates come from durable run evidence and avoid embedding large skill bodies or runtime materialization state.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill selectors remain first-class references/selectors, not embedded runtime snapshots.
- **VI. Replaceable Scaffolding, Thick Contracts**: PASS. The story hardens contracts at proposal generation/submission boundaries without adding cognitive scaffolding.
- **VII. Runtime Configurability**: PASS. Existing proposal enablement and policy settings remain authoritative.
- **VIII. Modular and Extensible Architecture**: PASS. Changes are scoped to proposal activity/service validation and tests.
- **IX. Resilient by Default**: PASS. Proposal generation remains best-effort; validation failures become visible redacted errors without compromising parent run completion.
- **X. Facilitate Continuous Improvement**: PASS. Proposal candidates remain reviewable follow-up work, not silent new executions.
- **XI. Spec-Driven Development**: PASS. This spec/plan/tasks set is the implementation source of truth.
- **XII. Canonical Documentation Separation**: PASS. Runtime implementation notes remain in MoonSpec artifacts; canonical docs are used as source requirements, not migration logs.
- **XIII. Pre-Release Compatibility Policy**: PASS. Internal contract changes update direct call/test sites without compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/310-proposal-candidate-validation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── proposal-candidate-contract.md
├── checklists/
│   └── requirements.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── temporal/
│   │   ├── activity_runtime.py
│   │   ├── activity_catalog.py
│   │   └── workflows/run.py
│   ├── task_proposals/
│   │   └── service.py
│   └── tasks/
│       └── task_contract.py
└── schemas/
    └── task_proposal_models.py

tests/
└── unit/
    └── workflows/
        ├── temporal/test_proposal_activities.py
        ├── temporal/workflows/test_run_proposals.py
        └── task_proposals/test_service.py
```

**Structure Decision**: Use the existing proposal workflow/activity/service boundaries. Unit coverage targets the proposal activity and service validation helpers; workflow-boundary coverage targets `MoonMindRunWorkflow._run_proposals_stage()` activity scheduling shape. No new storage or UI structure is planned.

## Complexity Tracking

No constitution violations or justified complexity exceptions.
