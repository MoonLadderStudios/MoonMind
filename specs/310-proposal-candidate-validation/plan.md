# Implementation Plan: Proposal Candidate Validation

**Branch**: `310-proposal-candidate-validation` | **Date**: 2026-05-07 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/310-proposal-candidate-validation/spec.md`

## Summary

Implement `MM-596` by tightening proposal candidate generation and submission validation so generated follow-up tasks are side-effect-free until the trusted submission boundary, validate against the canonical task contract, preserve explicit skill selector and reliable preset provenance metadata, reject `tool.type=agent_runtime`, and report redacted validation errors before any delivery side effect. Current code already separates `proposal.generate` and `proposal.submit`, stores proposal payloads through `TaskProposalService`, and has baseline proposal-stage tests, but validation is incomplete for candidate-level unsafe tool types, skill/provenance preservation, and no-service structural validation. The work will add red-first unit and workflow-boundary tests, then implement focused validation helpers in proposal activity/service boundaries.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `TemporalProposalActivities.proposal_generate()` builds candidates from workflow parameters; current tests cover idea extraction and empty input. | Add explicit no-side-effect boundary evidence and durable evidence/provenance input handling. | unit + workflow boundary |
| FR-002 | partial | `TaskProposalService._prepare_task_create_request()` validates through `CanonicalTaskPayload` when service is wired; `proposal_submit()` counts candidates without service and skips malformed candidates shallowly. | Ensure `proposal_submit()` validates every submitted candidate shape before counting or service calls. | unit |
| FR-003 | missing | `CanonicalTaskPayload` has no observed top-level `tool` rejection; step skill payload logic rejects non-skill tool payloads only for skill steps. | Reject candidate payloads with `tool.type=agent_runtime` and accept `tool.type=skill` executable selectors. | unit + API/service |
| FR-004 | partial | Task skill selector models exist in `task_contract.py`; proposal preview exposes `taskSkills`; no proposal generation coverage preserves explicit task/step skills. | Preserve task/step skill selectors by selector/ref and ensure no skill body/materialization fields are embedded. | unit |
| FR-005 | partial | `TaskProposalTaskPreview` exposes authored preset and step source metadata; existing service tests cover promotion rejection for unresolved preset steps. | Preserve reliable `authoredPresets` and `steps[].source` when supplied by trusted parent-run evidence. | unit |
| FR-006 | missing | No test found proving proposal generation avoids fabricated authored preset or step source provenance when evidence is absent. | Add non-fabrication guards and tests. | unit |
| FR-007 | implemented_unverified | `proposal_generate()` returns dictionaries only; side effects are currently in `proposal_submit()`, but no assertion guards service/repository/external side effects during generation. | Add explicit unit coverage proving generation has no proposal service dependency or submission call. | unit |
| FR-008 | implemented_unverified | `run.py` schedules `proposal.generate` then `proposal.submit`; `activity_catalog.py` has separate activity definitions. | Add workflow-boundary assertions for distinct activity names and routes/capability queues. | workflow boundary unit |
| FR-009 | partial | `proposal_submit()` collects redacted truncated errors from service exceptions and skips malformed candidates. | Ensure unsafe tool type, malformed skill selectors, and ambiguous payloads are rejected with redacted visible errors before side effects. | unit + service |
| FR-010 | implemented_verified | `spec.md`, this plan, and handoff artifacts preserve `MM-596`. | Preserve through tasks, verification, commit, and PR metadata. | final verify |
| SC-001 | implemented_unverified | Generation is pure in current code but lacks explicit side-effect tests. | Add no-side-effect tests. | unit |
| SC-002 | partial | Service validation exists when service is wired. | Validate candidates before no-service counting and before service create calls. | unit |
| SC-003 | missing | No focused test found for accepted `tool.type=skill` and rejected `tool.type=agent_runtime` in proposal candidates. | Add tests and validation. | unit |
| SC-004 | partial | Skill selector schema exists; generation does not currently preserve explicit selectors from parent task/steps. | Add preservation tests and implementation. | unit |
| SC-005 | partial | Preview/provenance surfaces exist; generation lacks explicit preserve/non-fabricate coverage. | Add provenance preservation/non-fabrication tests and implementation. | unit |
| SC-006 | implemented_unverified | Workflow uses separate activity names. | Add route/boundary assertion coverage. | workflow boundary unit |
| SC-007 | implemented_verified | `MM-596` and all design IDs are visible in `spec.md` and this plan. | Preserve in downstream artifacts. | final verify |
| DESIGN-REQ-007 | partial | `proposal_generate()` avoids explicit writes but lacks artifact-ref and no-side-effect tests. | Add no-side-effect and unsafe input handling evidence. | unit |
| DESIGN-REQ-008 | partial | `proposal_submit()` validates via service only when wired; shallow skip path exists. | Validate all submitted candidates and keep validation before create/delivery. | unit |
| DESIGN-REQ-017 | partial | `TaskProposalService` normalizes task envelopes and payloads. | Reuse canonical validation for proposal candidate submission. | unit |
| DESIGN-REQ-018 | missing | No current top-level candidate `tool.type` validation evidence. | Add executable tool type validation. | unit |
| DESIGN-REQ-019 | partial | Skill selector models exist; proposal generation lacks preservation coverage. | Preserve explicit selector intent without embedding bodies/materialization. | unit |
| DESIGN-REQ-032 | implemented_unverified | Separate Temporal activity definitions exist for `proposal.generate` and `proposal.submit`. | Add workflow-boundary proof and final verification. | workflow boundary unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, SQLAlchemy async ORM, FastAPI task/proposal routers, existing `CanonicalTaskPayload` and `TaskProposalService`  
**Storage**: Existing `task_proposals` and proposal notification tables only; no new persistent storage  
**Unit Testing**: pytest through `./tools/test_unit.sh`; focused iteration with targeted pytest paths  
**Integration Testing**: pytest integration markers through `./tools/test_integration.sh` when Docker is available; workflow-boundary tests in unit suite for activity scheduling/route shape  
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
- **XIII. Pre-Release Compatibility Policy**: PASS. Internal contract changes will update all direct call/test sites without adding compatibility aliases, except existing workflow replay compatibility already present in current code.

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
└── tasks.md
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
├── unit/
│   ├── workflows/
│   │   ├── temporal/test_proposal_activities.py
│   │   ├── temporal/workflows/test_run_proposals.py
│   │   └── task_proposals/test_service.py
│   └── api/routers/test_task_proposals.py
└── integration/
    └── workflows/temporal/workflows/test_run.py
```

**Structure Decision**: Use the existing proposal workflow/activity/service boundaries. Unit coverage will target the proposal activity and service validation helpers; workflow-boundary coverage will target `MoonMindRunWorkflow._run_proposals_stage()` activity scheduling shape. No new storage or UI structure is planned.

## Complexity Tracking

No constitution violations or justified complexity exceptions.
