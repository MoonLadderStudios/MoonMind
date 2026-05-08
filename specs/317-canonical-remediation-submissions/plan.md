# Implementation Plan: Canonical Remediation Submissions

**Branch**: `317-canonical-remediation-submissions` | **Date**: 2026-05-08 | **Spec**: `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/specs/317-canonical-remediation-submissions/spec.md`
**Input**: Single-story feature specification from `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/specs/317-canonical-remediation-submissions/spec.md`

## Summary

Implement MM-617 by validating the canonical remediation submission and durable target-link behavior in `spec.md`. Repo gap analysis found that the core runtime behavior is already present: task-shaped submissions preserve nested remediation metadata, create-time service validation resolves and pins the target run, the durable remediation link model stores directed relationship and compact lifecycle fields, inbound/outbound relationship read paths exist, invalid remediation inputs fail before workflow start, and remediation links do not create dependency prerequisites. Planned work is verification-first: preserve MM-617 traceability in generated artifacts, rerun focused router/service coverage, and only add code or tests if verification exposes a gap.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/executions.py` preserves `payload.task.remediation`; `tests/unit/api/routers/test_executions.py` covers task-shaped preservation. | No new implementation. | Focused router unit coverage and final verify. |
| FR-002 | implemented_verified | `moonmind/workflows/temporal/service.py` pins remediation target metadata into `initialParameters.task.remediation`; service test asserts stored/start payload. | No new implementation. | Focused service unit coverage and final verify. |
| FR-003 | implemented_verified | `TemporalExecutionRemediationLink` model and `TemporalExecutionService.create_execution()` persist a directed remediation link. | No new implementation. | Focused service unit coverage and final verify. |
| FR-004 | implemented_verified | `_validate_remediation_link()` resolves current target run ID and writes it into link and task parameters. | No new implementation. | Focused service unit coverage for omitted and supplied run IDs. |
| FR-005 | implemented_verified | Service validation rejects empty/missing target, run-id-as-workflow-id, missing target, non-run target, self-target, and unauthorized target cases. | No new implementation. | Focused service validation tests. |
| FR-006 | implemented_verified | Service validation rejects malformed/foreign taskRunIds, unsupported authorityMode, unsupported actionPolicyRef, and nested remediation targets. | No new implementation. | Focused service validation tests. |
| FR-007 | implemented_verified | `execution_remediation_links` model stores status, lock, latest action, outcome, timestamps; `GET /api/executions/{workflow_id}/remediations` returns compact inbound/outbound summaries. | No new implementation. | Focused router response tests plus service lookup tests. |
| FR-008 | implemented_verified | Service test asserts remediation creation leaves dependency prerequisites empty. | No new implementation. | Focused service unit coverage. |
| FR-009 | implemented_verified | Service raises structured `TemporalExecutionValidationError` before commit/start for invalid remediation payloads; router rejects malformed convenience-route remediation objects. | No new implementation. | Focused router/service validation tests. |
| FR-010 | partial | `spec.md` and this plan preserve MM-617; downstream tasks, verification, commit, PR, and Jira handoff do not exist yet. | Preserve MM-617 in tasks, verification evidence, commit/PR metadata, and Jira-visible handoff. | Final MoonSpec verification checks traceability. |
| SC-001 | implemented_verified | Service test asserts one link and preserved remediation payload. | No new implementation. | Focused service unit coverage. |
| SC-002 | implemented_verified | Service test asserts omitted runId is resolved and supplied matching runId is accepted. | No new implementation. | Focused service unit coverage. |
| SC-003 | implemented_verified | Service validation tests cover malformed target, visibility/ownership, self/nested, authority, policy, and taskRunIds failure paths. | No new implementation. | Focused service validation tests. |
| SC-004 | implemented_verified | Service lookup tests and router inbound/outbound response tests cover compact fields. | No new implementation. | Focused router/service tests. |
| SC-005 | implemented_verified | Service test asserts `list_prerequisites(remediation.workflow_id) == []`. | No new implementation. | Focused service unit coverage. |
| SC-006 | partial | Current spec/plan preserve MM-617; later verification/PR artifacts are not yet produced. | Carry MM-617 through tasks, verify, commit, PR, and Jira handoff. | Final verify and PR review checklist. |
| DESIGN-REQ-001 | implemented_verified | Normal MoonMind.Run create path with nested remediation metadata is used. | No new implementation. | Focused router/service tests. |
| DESIGN-REQ-002 | implemented_verified | Remediation is persisted in a separate directed link model and not dependency edges. | No new implementation. | Focused service tests. |
| DESIGN-REQ-003 | implemented_verified | Nested task remediation metadata is preserved into canonical parameters. | No new implementation. | Focused router/service tests. |
| DESIGN-REQ-004 | implemented_verified | Target run identity is resolved and pinned at create time. | No new implementation. | Focused service tests. |
| DESIGN-REQ-005 | implemented_verified | Create-time validation rejects invalid targets and policy fields before workflow start. | No new implementation. | Focused service validation tests. |
| DESIGN-REQ-006 | implemented_verified | Link model and router expose forward/reverse compact relationship fields. | No new implementation. | Focused router/service tests. |
| DESIGN-REQ-007 | implemented_verified | Link summaries expose artifact refs only; evidence retrieval is out of scope for this story and handled by separate remediation evidence surfaces. | No new implementation for this story. | Final verify confirms no raw access grants in link metadata. |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK  
**Storage**: Existing SQLAlchemy/Alembic database with `execution_remediation_links` and existing Temporal execution source records  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: FastAPI router and async service-boundary pytest coverage through the unit runner; no compose-backed integration is required unless implementation changes cross API/artifact/service boundaries  
**Target Platform**: Linux server / Docker Compose deployment  
**Project Type**: FastAPI control plane plus Temporal execution service  
**Performance Goals**: Create-time remediation validation remains bounded to target source lookup, task-run ownership scan of compact target metadata, and one link insert  
**Constraints**: Runtime mode; preserve canonical `task.remediation`; fail before workflow start for invalid submissions; do not create dependency gates; preserve MM-617 traceability  
**Scale/Scope**: One target relationship per remediation run for this submission/link story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story uses existing MoonMind.Run orchestration metadata and does not alter agent cognition.
- II. One-Click Agent Deployment: PASS. No new external service, secret, or deployment prerequisite is planned.
- III. Avoid Vendor Lock-In: PASS. Remediation metadata is provider-neutral execution data.
- IV. Own Your Data: PASS. Linkage data remains in operator-owned persistence and artifact refs remain identifiers.
- V. Skills Are First-Class and Easy to Add: PASS. No executable skill contract changes are planned.
- VI. Replaceable Scaffolding: PASS. Thin router/service/persistence boundaries are validated by focused tests.
- VII. Runtime Configurability: PASS. No new runtime configuration is required.
- VIII. Modular Architecture: PASS. Existing router, service, model, and migration boundaries are reused.
- IX. Resilient by Default: PASS. Validation occurs before workflow start and link persistence is transactional with execution creation.
- X. Continuous Improvement: PASS. Planning preserves explicit outcome and verification evidence requirements.
- XI. Spec-Driven Development: PASS. `spec.md`, `plan.md`, and design artifacts preserve MM-617 before downstream tasks.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. No canonical docs changes are planned; implementation planning remains in this feature directory.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases or legacy fallback layers are planned.

Post-design re-check: PASS. Generated design artifacts keep the same boundaries and introduce no new constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/317-canonical-remediation-submissions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-submissions.md
└── checklists/
    └── requirements.md
```

### Source Code

```text
api_service/
├── api/routers/executions.py
├── db/models.py
└── migrations/versions/
    ├── 219_remediation_create_links.py
    ├── 221_remediation_context_artifacts.py
    ├── 223_remediation_link_status_fields.py
    └── f2a3b4c5d6e7_remediation_guard_state.py

moonmind/workflows/temporal/
└── service.py

tests/unit/
├── api/routers/test_executions.py
└── workflows/temporal/test_temporal_service.py
```

**Structure Decision**: This is a backend control-plane and Temporal service story. Planning keeps work in existing FastAPI router, Temporal execution service, database model/migration, and focused unit/integration-boundary tests.

## Complexity Tracking

None.
