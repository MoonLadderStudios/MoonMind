# Implementation Plan: Compile Step Type Payloads Into Runtime Plans and Promotable Proposals

**Branch**: `286-compile-step-type-payloads` | **Date**: 2026-04-29 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/286-compile-step-type-payloads/spec.md`

## Summary

MM-567 requires executable Step Type payloads to converge at runtime and proposal promotion boundaries. Repo inspection shows the behavior is already implemented by the current task contract, runtime planner, proposal service, and proposal API preview paths. The plan is verification-first: preserve the MM-567 Jira source in a dedicated MoonSpec artifact set, prove the existing implementation satisfies the runtime and proposal requirements, and write final verification evidence without adding a new storage model or hidden preset execution path.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/temporal/worker_runtime.py` runtime planner maps explicit Tool and Skill steps; `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` covers both. | No new implementation. | unit |
| FR-002 | implemented_verified | `TaskStepSource` preserves preset-derived metadata; runtime planner includes `source` in node inputs. | No new implementation. | unit |
| FR-003 | implemented_verified | Proposal preview computes preset provenance from `authoredPresets` and step `source` metadata in `api_service/api/routers/task_proposals.py`; tests cover preview. | No new implementation. | API unit |
| FR-004 | implemented_verified | `TaskProposalService.promote_proposal` validates stored `CanonicalTaskPayload` and uses the stored payload; no live preset lookup is performed. | No new implementation. | unit |
| FR-005 | implemented_verified | `TaskStepSpec._reject_forbidden_step_overrides` rejects `preset`, `activity`, and `Activity`; task contract tests cover this. | No new implementation. | unit |
| FR-006 | implemented_verified | Proposal promotion rejects invalid stored task payloads, including unresolved Preset steps. | No new implementation. | unit |
| FR-007 | implemented_verified | Runtime override path changes only runtime fields and preserves reviewed steps, instructions, and provenance. | No new implementation. | unit |
| FR-008 | implemented_verified | Step Type docs keep Activity internal; task contract rejects Activity as a user-facing Step Type. | No new implementation. | docs check + unit |
| SCN-001 | implemented_verified | Runtime planner explicit Tool test verifies typed tool node and source metadata. | No new implementation. | unit |
| SCN-002 | implemented_verified | Runtime planner explicit Skill test verifies agent runtime node. | No new implementation. | unit |
| SCN-003 | implemented_verified | Proposal service and API tests preserve and expose preset provenance metadata. | No new implementation. | unit + API unit |
| SCN-004 | implemented_verified | Promotion service validates stored flat payload and has no catalog expansion dependency. | No new implementation. | unit |
| SCN-005 | implemented_verified | Proposal service rejects unresolved Preset steps. | No new implementation. | unit |
| SCN-006 | implemented_verified | Task contract rejects Activity labels. | No new implementation. | unit |
| DESIGN-REQ-008 | implemented_verified | Executable task contract accepts Tool/Skill only by default. | No new implementation. | unit |
| DESIGN-REQ-013 | implemented_verified | Runtime planner maps Tool/Skill and does not map Preset. | No new implementation. | unit |
| DESIGN-REQ-016 | implemented_verified | Proposal promotion validates stored executable payloads. | No new implementation. | unit |
| DESIGN-REQ-018 | implemented_verified | Proposal, editing, and runtime reconstruction share canonical Step Type payload semantics from prior stories. | No new implementation. | final verify |
| DESIGN-REQ-019 | implemented_verified | Preset remains metadata/default authoring state; Activity remains rejected user-facing type. | No new implementation. | unit + docs check |
| SC-001 | implemented_verified | Runtime planner focused tests exist. | No new implementation. | unit |
| SC-002 | implemented_verified | Task contract focused tests exist. | No new implementation. | unit |
| SC-003 | implemented_verified | Proposal service focused tests exist. | No new implementation. | unit |
| SC-004 | implemented_verified | Proposal API preview focused test exists. | No new implementation. | API unit |
| SC-005 | implemented_verified | `docs/Steps/StepTypes.md` sections 7, 13, and 15 state flat promotion and Activity internality. | No new implementation. | docs check |
| SC-006 | implemented_verified | `specs/286-compile-step-type-payloads/verification.md` preserves MM-567 verification evidence. | No new implementation. | final verify |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React remains present but no frontend code change is planned for this story
**Primary Dependencies**: Pydantic v2 task contract models, Temporal runtime planner helpers, FastAPI proposal router, existing pytest suites
**Storage**: No new persistent storage; proposals use existing task proposal records and stored `taskCreateRequest` payloads
**Unit Testing**: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py`
**Integration Testing**: No compose-backed `integration_ci` test is required because this story verifies deterministic payload validation, runtime plan construction, and API serialization without new services or persistence schema
**Target Platform**: MoonMind task execution and task proposal promotion boundaries
**Project Type**: Python web service and Temporal runtime worker code
**Performance Goals**: Runtime plan construction and proposal promotion remain linear in task step count
**Constraints**: Preserve MM-567 traceability; do not make Preset a runtime node by default; do not use Activity as a user-facing Step Type; do not introduce live preset lookup during promotion
**Scale/Scope**: Existing task payload, runtime planning, and proposal promotion surfaces only

## Constitution Check

- Orchestrate, Don't Recreate: PASS. Step Types continue to compile into provider/runtime-agnostic execution boundaries.
- One-Click Agent Deployment: PASS. No new required service, secret, or deployment prerequisite.
- Avoid Vendor Lock-In: PASS. Tool, Skill, and Preset semantics remain provider-neutral.
- Own Your Data: PASS. Stored payloads remain local, inspectable task/proposal data.
- Skills Are First-Class and Easy to Add: PASS. Skill steps remain explicit first-class executable steps.
- Thin Scaffolding, Thick Contracts: PASS. The story verifies existing payload contracts rather than adding hidden orchestration scaffolding.
- Powerful Runtime Configurability: PASS. Runtime override remains explicit and bounded.
- Modular and Extensible Architecture: PASS. Behavior stays within task contract, runtime planner, and proposal service boundaries.
- Resilient by Default: PASS. Invalid payloads fail before runtime materialization or promotion.
- Facilitate Continuous Improvement: PASS. Verification artifacts preserve evidence and next-action status.
- Spec-Driven Development: PASS. MM-567 artifacts precede this verification pass.
- Canonical Documentation Separation: PASS. Canonical docs remain desired-state; execution notes live in this feature directory.
- Compatibility Policy: PASS. No compatibility alias or hidden semantic transform is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/286-compile-step-type-payloads/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── step-type-runtime-proposal.md
├── checklists/
│   └── requirements.md
├── quickstart.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/workflows/tasks/task_contract.py
moonmind/workflows/temporal/worker_runtime.py
moonmind/workflows/task_proposals/service.py
api_service/api/routers/task_proposals.py
tests/unit/workflows/tasks/test_task_contract.py
tests/unit/workflows/temporal/test_temporal_worker_runtime.py
tests/unit/workflows/task_proposals/test_service.py
tests/unit/api/routers/test_task_proposals.py
docs/Steps/StepTypes.md
```

**Structure Decision**: Treat current runtime/proposal code as implementation evidence, add no new source files, and verify the existing tests that cover the MM-567 story.

## Complexity Tracking

No constitution violations.
