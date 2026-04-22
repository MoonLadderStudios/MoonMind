# Implementation Plan: Canonical Remediation Submissions

**Branch**: `226-canonical-remediation-submissions` | **Date**: 2026-04-22 | **Spec**: `specs/226-canonical-remediation-submissions/spec.md`
**Input**: Single-story feature specification from `/specs/226-canonical-remediation-submissions/spec.md`

## Summary

Implement MM-451 by validating the canonical remediation submission and pinned target linkage behavior described in `docs/tmp/jira-orchestration-inputs/MM-451-moonspec-orchestration-input.md`. Repo gap analysis found the required runtime behavior already delivered by the remediation create/link slice: task-shaped submissions preserve `task.remediation`, create-time service validation pins target run identity, durable remediation links support inbound/outbound lookups, invalid submissions fail before workflow start, and the convenience route expands into the same canonical create contract. Planned work is artifact traceability and focused verification, not duplicate implementation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/executions.py` preserves `payload.task.remediation`; `tests/unit/api/routers/test_executions.py` covers task-shaped preservation | no new implementation | focused unit |
| FR-002 | implemented_verified | `moonmind/workflows/temporal/service.py` pins `initialParameters.task.remediation.target.runId`; service tests assert persisted payload | no new implementation | focused unit |
| FR-003 | implemented_verified | `_validate_remediation_link` resolves target source record and target run; tests cover omitted and supplied run IDs | no new implementation | focused unit |
| FR-004 | implemented_verified | `TemporalExecutionRemediationLink` model and migrations persist directed relationship metadata; service tests assert link fields | no new implementation | focused unit |
| FR-005 | implemented_verified | `list_remediation_targets` and `list_remediations_for_target` expose outbound/inbound lookups with compact fields | no new implementation | focused unit |
| FR-006 | implemented_verified | service validation rejects missing, run-ID, missing target, non-run target, mismatched run, unsupported authority/action policy, malformed task run IDs, and nested remediation | no new implementation | focused unit |
| FR-007 | implemented_verified | `POST /api/executions/{workflowId}/remediation` route expands into task-shaped create; router tests cover expansion and malformed payloads | no new implementation | focused unit |
| FR-008 | implemented_verified | service tests assert remediation creation leaves dependency prerequisites empty | no new implementation | focused unit |
| SC-001 | implemented_verified | router and service tests cover payload preservation and exactly one link | rerun focused tests | focused unit |
| SC-002 | implemented_verified | service tests cover omitted run ID resolution and supplied matching run ID | rerun focused tests | focused unit |
| SC-003 | implemented_verified | service/router tests cover structured rejection cases | rerun focused tests | focused unit |
| SC-004 | implemented_verified | service tests cover inbound and outbound lookup methods | rerun focused tests | focused unit |
| SC-005 | implemented_verified | service tests cover no dependency prerequisites for remediation | rerun focused tests | focused unit |
| SC-006 | implemented_verified | router tests cover remediation convenience route expansion | rerun focused tests | focused unit |
| DESIGN-REQ-001 | implemented_verified | canonical task-shaped create route and service behavior preserve `MoonMind.Run` remediation semantics | no new implementation | focused unit |
| DESIGN-REQ-002 | implemented_verified | durable payload is nested under `task.remediation` | no new implementation | focused unit |
| DESIGN-REQ-003 | implemented_verified | create-time target run pinning is implemented and tested | no new implementation | focused unit |
| DESIGN-REQ-004 | implemented_verified | remediation uses separate link model and no dependency edge | no new implementation | focused unit |
| DESIGN-REQ-005 | implemented_verified | validation and convenience route expansion are implemented and tested | no new implementation | focused unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK  
**Storage**: Existing SQLAlchemy/Alembic database with `execution_remediation_links` already present  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: Existing router/service boundary tests; no new compose-backed integration seam for this already implemented slice  
**Target Platform**: Linux server / Docker Compose deployment  
**Project Type**: FastAPI control plane plus Temporal execution service  
**Performance Goals**: Create-time remediation validation remains bounded to one target source lookup and one link insert  
**Constraints**: Runtime mode; preserve canonical `task.remediation`; do not change dependency semantics; preserve MM-451 traceability  
**Scale/Scope**: One remediation relationship per remediation execution for this submission/linkage slice

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Existing orchestration metadata is used without altering agent cognition.
- II. One-Click Agent Deployment: PASS. No new runtime prerequisite or external service is introduced.
- III. Avoid Vendor Lock-In: PASS. Behavior is provider-neutral execution metadata.
- IV. Own Your Data: PASS. Linkage data is stored locally in existing persistence.
- V. Skills Are First-Class and Easy to Add: PASS. No skill contract changes.
- VI. Replaceable Scaffolding: PASS. Thin service/router boundaries are verified by tests.
- VII. Runtime Configurability: PASS. No new hardcoded environment or provider behavior.
- VIII. Modular Architecture: PASS. Existing router, service, and persistence boundaries remain intact.
- IX. Resilient by Default: PASS. Validation happens before workflow start and link creation is transactional with execution creation.
- X. Continuous Improvement: PASS. Verification records the existing implementation evidence for MM-451.
- XI. Spec-Driven Development: PASS. This spec/plan/tasks set preserves the Jira preset brief and source mappings.
- XII. Canonical Docs vs Tmp: PASS. No canonical docs are changed; orchestration input remains under `docs/tmp`.
- XIII. Pre-Release Compatibility: PASS. No compatibility alias or internal fallback layer is added.

## Project Structure

### Documentation (this feature)

```text
specs/226-canonical-remediation-submissions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── canonical-remediation-submissions.md
└── tasks.md
```

### Source Code

```text
api_service/
├── api/routers/executions.py
├── db/models.py
└── migrations/versions/

moonmind/
└── workflows/temporal/service.py

tests/
└── unit/
    ├── api/routers/test_executions.py
    └── workflows/temporal/test_temporal_service.py
```

## Complexity Tracking

None.
