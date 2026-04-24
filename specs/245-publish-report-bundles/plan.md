# Implementation Plan: Publish Report Bundles

**Branch**: `245-publish-report-bundles` | **Date**: 2026-04-23 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/245-publish-report-bundles/spec.md`

## Summary

Plan MM-493 as a verification-first runtime story. The current repository already contains the core report bundle publication path in `moonmind/workflows/temporal/artifacts.py`, compact bundle validation in `moonmind/workflows/temporal/report_artifacts.py`, and server-driven canonical report consumption in `tests/contract/test_temporal_artifact_api.py` plus `frontend/src/entrypoints/task-detail.tsx`. The implementation plan is therefore to preserve MM-493 in feature-local design artifacts, keep unit and integration verification strategies explicit, and generate downstream tasks that verify the current runtime against the MM-493 story before introducing any production-code changes.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `publish_report_bundle` in `moonmind/workflows/temporal/artifacts.py`; activity coverage in `tests/unit/workflows/temporal/test_artifacts_activities.py` | preserve and re-verify | unit |
| FR-002 | implemented_verified | `build_report_bundle_result` and `validate_report_bundle_result` in `moonmind/workflows/temporal/report_artifacts.py`; bundle validation tests in `tests/unit/workflows/temporal/test_artifacts.py` | preserve and re-verify | unit |
| FR-003 | implemented_verified | execution linkage covered by `publish_report_bundle` and `TemporalArtifactService.list_for_execution`; assertions in `tests/unit/workflows/temporal/test_artifacts.py` | preserve and re-verify | unit + contract |
| FR-004 | implemented_verified | step metadata propagation in `publish_report_bundle`; assertions for `step_id` and `attempt` in `tests/unit/workflows/temporal/test_artifacts.py` | preserve and re-verify | unit |
| FR-005 | implemented_verified | final-bundle validation in `publish_report_bundle`; final-report tests in `tests/unit/workflows/temporal/test_artifacts.py` and `tests/unit/workflows/temporal/test_report_workflow_rollout.py` | preserve and re-verify | unit |
| FR-006 | implemented_verified | server-driven latest `report.primary` query in `tests/contract/test_temporal_artifact_api.py`; Mission Control consumer in `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/entrypoints/task-detail.test.tsx` | preserve and re-verify | contract + frontend unit |
| FR-007 | implemented_verified | workflow-family rollout validation in `moonmind/workflows/temporal/report_artifacts.py` and `tests/unit/workflows/temporal/test_report_workflow_rollout.py` | preserve and re-verify | unit |
| FR-008 | implemented_verified | coexistence and non-mutation are now covered by `test_latest_report_primary_coexists_with_intermediate_report_without_mutation` in `tests/unit/workflows/temporal/test_artifacts.py` alongside latest-link behavior | preserve and re-verify | unit + contract |
| FR-009 | partial | `spec.md` preserves MM-493 and the original Jira brief; downstream feature-local plan, tasks, and verification artifacts do not yet exist | preserve MM-493 through all downstream artifacts | traceability review |
| DESIGN-REQ-005 | implemented_verified | compact bundle validation in `moonmind/workflows/temporal/report_artifacts.py` | preserve and re-verify | unit |
| DESIGN-REQ-006 | implemented_verified | activity-owned publication path in `moonmind/workflows/temporal/artifacts.py` | preserve and re-verify | unit |
| DESIGN-REQ-012 | implemented_verified | execution and step linkage behavior in `publish_report_bundle` and artifact query tests | preserve and re-verify | unit + contract |
| DESIGN-REQ-013 | implemented_verified | report artifacts remain artifact-backed and bundle payloads stay compact | preserve and re-verify | unit |
| DESIGN-REQ-019 | implemented_verified | rollout mapping behavior covered in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` | preserve and re-verify | unit |
| DESIGN-REQ-020 | implemented_verified | final marker enforcement in `publish_report_bundle` | preserve and re-verify | unit |
| DESIGN-REQ-021 | implemented_verified | latest `report.primary` resolution in contract and Mission Control tests | preserve and re-verify | contract + frontend unit |

## Technical Context

**Language/Version**: Python 3.12 and TypeScript/React for existing Mission Control verification surfaces  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, SQLAlchemy async ORM, existing temporal artifact service/helpers, React, Vitest  
**Storage**: Existing temporal artifact metadata tables and configured artifact store; no new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` with focused pytest and UI targets during verification  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` escalation when persistence, activity publication, or API linkage changes are required  
**Target Platform**: MoonMind API service, Temporal worker runtime, and Mission Control UI  
**Project Type**: Full-stack runtime verification story with backend artifact publication and frontend canonical report consumption  
**Performance Goals**: Keep workflow-visible bundle payloads bounded to refs and metadata only; latest report lookup remains server-driven and inexpensive  
**Constraints**: No inline report/evidence/log payloads in workflow history, no browser-side latest-report heuristics, no new storage system, preserve MM-493 traceability  
**Scale/Scope**: One story covering immutable report-bundle publication and canonical final/latest report behavior across existing workflow families

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - reuses existing activity/service boundaries instead of introducing workflow-local report assembly.
- II. One-Click Agent Deployment: PASS - no new services, dependencies, or operator setup are introduced.
- III. Avoid Vendor Lock-In: PASS - report bundle semantics are artifact and API contracts, not vendor-specific logic.
- IV. Own Your Data: PASS - reports and evidence remain in MoonMind-managed artifact storage.
- V. Skills Are First-Class and Easy to Add: PASS - no agent-skill runtime changes are required.
- VI. Replaceable AI Scaffolding: PASS - the work centers on durable runtime contracts and verification evidence.
- VII. Runtime Configurability: PASS - existing artifact and API configuration remain unchanged.
- VIII. Modular and Extensible Architecture: PASS - verification focuses on `report_artifacts`, artifact publication, and UI consumption boundaries already in place.
- IX. Resilient by Default: PASS - durable artifact publication and latest-report queries remain compact and deterministic.
- X. Facilitate Continuous Improvement: PASS - downstream verification will record concrete evidence and remaining drift if any exists.
- XI. Spec-Driven Development: PASS - MM-493 is preserved through spec, plan, and later tasks/verification.
- XII. Canonical Documentation Separation: PASS - the Jira brief remains under `docs/tmp`, while feature-local artifacts capture the desired state for this story.
- XIII. Pre-release Compatibility Policy: PASS - verification-first planning does not introduce compatibility shims or aliasing.

## Project Structure

### Documentation (this feature)

```text
specs/245-publish-report-bundles/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── report-bundle-publication-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── artifacts.py
└── report_artifacts.py

tests/unit/workflows/temporal/
├── test_artifacts.py
├── test_artifacts_activities.py
└── test_report_workflow_rollout.py

tests/contract/
└── test_temporal_artifact_api.py

frontend/src/entrypoints/
├── task-detail.tsx
└── task-detail.test.tsx
```

**Structure Decision**: Keep MM-493 scoped to the existing artifact publication helper, report bundle validation helpers, API contract path, and Mission Control report consumer. The story is planned as verification-first because the repository already contains the required runtime behavior and focused test coverage.

## Complexity Tracking

No constitution violations.
