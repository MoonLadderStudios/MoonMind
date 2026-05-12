# Implementation Plan: Authoritative Task Snapshot Durability

**Branch**: `339-authoritative-task-snapshot-durability` | **Date**: 2026-05-11 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:e2bc69b6-9268-42f5-8b6d-deec1caaeb08/repo/specs/339-authoritative-task-snapshot-durability/spec.md`

## Summary

MM-639 requires every submitted `MoonMind.Run` task to retain an authoritative task input snapshot that can drive edit, exact full rerun, edited full retry, and failed-step Resume without depending on live preset catalog state or lossy projections. Current repository inspection shows the core snapshot artifact, action gating, rerun cleanup, Jira-Orchestrate child-run snapshot persistence, and failed-step Resume source checks already exist. The implementation plan is therefore focused on closing explicit schema/field coverage and degraded attachment-aware reconstruction gaps, then adding boundary-level unit and hermetic integration coverage that proves snapshot completeness, catalog independence, and recovery intent separation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `api_service/api/routers/executions.py` persists snapshots for task create, direct create, edit/rerun updates, and rerun; `moonmind/workflows/temporal/worker_runtime.py` persists Jira-Orchestrate child-run snapshots | Add verification-first coverage across all submitted execution entrypoints; add implementation only if a path lacks a snapshot | unit + integration |
| FR-002 | partial | Snapshot payload copies `task` and compact metadata, but no explicit schema/contract verifies all Section 7 fields, dependency declarations, final order, pinned preset bindings, include-tree summary, per-step provenance, and detachment state | Add/strengthen snapshot schema validation and persistence coverage for all required authored fields | unit + integration |
| FR-003 | implemented_unverified | Frontend draft reconstruction can hydrate from `taskInputSnapshot` artifacts in `frontend/src/lib/temporalTaskEditing.ts`; rerun snapshot hydration reads input artifacts before preserving stripped instructions | Add tests proving reconstruction succeeds after live preset/template definitions diverge from the submitted snapshot | unit + integration |
| FR-004 | implemented_verified | `tests/integration/temporal/test_full_retry_recovery_actions.py`; `tests/unit/workflows/temporal/test_temporal_service.py` assert rerun strips resume progress and starts from original input refs | Preserve coverage; no new implementation expected | final verify |
| FR-005 | implemented_unverified | Update route persists `source_kind="edit"` for edit-for-rerun and `source_kind="rerun"` for rerun in `api_service/api/routers/executions.py` | Add verification-first tests that edited full retry creates a new snapshot and leaves source snapshot/evidence unchanged | unit + integration |
| FR-006 | implemented_verified | `api_service/api/routers/executions.py` rejects edited resume payload fields; `moonmind/workflows/temporal/service.py` validates checkpoint snapshot ref against source snapshot; tests cover rejection and source identity | Preserve coverage; no new implementation expected | final verify |
| FR-007 | partial | Missing snapshots disable edit/rerun actions and descriptor reports degraded read-only state; frontend rejects unreconstructible attachment bindings, but end-to-end degraded handling for attachment-aware executions needs stronger proof | Add degraded-state tests and implementation adjustments if any recovery path can silently drop or synthesize attachment state | unit + integration |
| FR-008 | implemented_unverified | `TaskInputSnapshotDescriptorModel` is serialized on execution details and action disabled reasons include `original_task_input_snapshot_missing` | Add verification-first tests for snapshot availability, missing snapshot, and degraded reconstruction exposure | unit + integration |
| FR-009 | implemented_verified | `spec.md` preserves MM-639 and the canonical Jira preset brief; this plan preserves the same traceability | Preserve MM-639 through downstream artifacts, commits, PR, and verification | final verify |
| SCN-001 | implemented_unverified | Snapshot persisted during task create paths, but field-complete coverage is partial | Verify all authored fields are present for a representative task-shaped submission | unit + integration |
| SCN-002 | partial | Attachment refs are persisted and metadata stores `attachment_refs`; frontend validates compact binding keys | Add end-to-end target-binding reconstruction and degraded-path coverage | unit + integration |
| SCN-003 | implemented_unverified | Snapshot-based frontend reconstruction and snapshot hydration paths exist | Add changed-preset-catalog tests proving no live lookup is needed | unit + integration |
| SCN-004 | implemented_verified | Full rerun tests prove no resume progress import | No new work beyond final verification | final verify |
| SCN-005 | implemented_unverified | Edit/rerun update route persists snapshots for new execution records | Add tests proving new edited retry snapshot and immutable source evidence | unit + integration |
| SCN-006 | implemented_verified | Resume route rejects task/runtime edits and service validates checkpoint snapshot identity | No new work beyond final verification | final verify |
| SCN-007 | partial | Missing snapshots disable actions; frontend throws on invalid attachment binding reconstruction | Add explicit degraded-state integration coverage | unit + integration |
| SC-001 | implemented_unverified | Snapshot refs are emitted in execution responses for covered create paths | Verify 100% of covered submitted execution paths associate a retrievable snapshot before action evaluation | integration |
| SC-002 | implemented_unverified | Snapshot reconstruction code exists | Add live-catalog-divergence tests for edit/rerun/resume reconstruction | unit + integration |
| SC-003 | partial | Attachment refs persist; degraded handling exists in pieces | Add target-binding and degraded-blocking tests | unit + integration |
| SC-004 | implemented_unverified | Full rerun and Resume are covered; edited full retry needs stronger proof | Add edited full retry snapshot evidence test | unit + integration |
| SC-005 | partial | Missing snapshot action disablement exists; attachment-aware degraded path needs stronger proof | Add unreconstructible snapshot and attachment loss prevention tests | unit + integration |
| SC-006 | implemented_verified | `spec.md` and `plan.md` preserve MM-639 and source mappings | Preserve through generated artifacts and final verification | final verify |
| DESIGN-REQ-004 | partial | Authoritative snapshot and degraded descriptor exist, but attachment-aware degraded behavior needs end-to-end proof | Add attachment-aware reconstruction and degraded-path tests; patch if any silent loss is found | unit + integration |
| DESIGN-REQ-011 | partial | Snapshot copies task payload but lacks explicit Section 7 field contract coverage | Add explicit schema/contract coverage and fill missing fields if discovered | unit + integration |
| DESIGN-REQ-012 | implemented_unverified | Full rerun and Resume are strongly covered; edited full retry requires additional proof | Add edited full retry verification and preserve existing rerun/resume behavior | unit + integration |
| DESIGN-REQ-013 | implemented_unverified | Action gating and recovery intent separation exist across API/service tests | Add final cross-flow verification of invariant coverage | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control edit/rerun UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, existing Temporal artifact service/helpers  
**Storage**: Existing Temporal execution records, canonical execution memo/search/projection records, Temporal artifact metadata/content store; no new persistent database tables planned  
**Unit Testing**: `./tools/test_unit.sh` for final verification; focused Python pytest targets and `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` for iteration when JS deps are ready  
**Integration Testing**: `./tools/test_integration.sh` for required hermetic `integration_ci`; focused pytest targets under `tests/integration/temporal`, `tests/integration/api`, and `tests/contract` during development  
**Target Platform**: MoonMind API service and Mission Control frontend running in Docker Compose-managed local deployment  
**Project Type**: Web service plus frontend dashboard and Temporal orchestration runtime  
**Performance Goals**: Snapshot reads and writes should add no user-visible delay beyond normal task submission/edit/rerun flow; task detail and Create-page reconstruction should remain responsive for typical task payloads  
**Constraints**: No raw binary content in workflow history; no live preset catalog dependency for already submitted tasks; no secret material in artifacts/logs; no semantic compatibility aliases for internal contracts  
**Scale/Scope**: One runtime story for `MoonMind.Run` task-shaped submissions and recovery actions; covers direct task create, Jira-Orchestrate child runs, exact full rerun, edited full retry, and failed-step Resume

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status | Notes |
| --- | --- | --- | --- |
| I. Orchestrate, Don't Recreate | Keep behavior in MoonMind orchestration/adapters, not agent cognition | PASS | Snapshot and recovery behavior remains in API/service/runtime boundaries. |
| II. One-Click Agent Deployment | Preserve Docker Compose/local-first operation | PASS | No new external dependencies or required SaaS services. |
| III. Avoid Vendor Lock-In | Avoid provider-specific assumptions | PASS | Snapshot contract is provider-neutral task input state. |
| IV. Own Your Data | Store context/artifacts on operator-controlled infrastructure | PASS | Uses existing Temporal artifact store and execution records. |
| V. Skills Are First-Class | Do not conflate runtime tool skills with agent skill bundles | PASS | No skill runtime changes planned. |
| VI. Replaceable Scaffolding | Prefer thin contracts with strong tests | PASS | Plan strengthens task snapshot contracts and boundary tests. |
| VII. Runtime Configurability | Respect existing settings/feature flags | PASS | Existing task editing gates remain configuration-controlled. |
| VIII. Modular Architecture | Keep changes behind existing service/router/frontend boundaries | PASS | Planned work is scoped to execution router, Temporal service/runtime helpers, task editing UI helpers, and tests. |
| IX. Resilient by Default | Preserve retry/resume safety and in-flight evidence | PASS | Recovery actions remain explicit and checkpoint/snapshot validated. |
| X. Continuous Improvement | Produce structured evidence | PASS | Plan requires unit/integration/final verification evidence. |
| XI. Spec-Driven Development | Plan from one-story spec with traceability | PASS | `spec.md` exists, has one story, and preserves MM-639. |
| XII. Canonical Docs vs Backlog | Keep migration notes in specs, not docs | PASS | Work artifacts stay under `specs/339-authoritative-task-snapshot-durability/`. |
| XIII. Delete, Don't Deprecate | Avoid compatibility aliases for internal contracts | PASS | Any discovered stale internal fallback should be removed rather than preserved. |

Post-design re-check: PASS. Generated design artifacts keep the same constraints and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/339-authoritative-task-snapshot-durability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-input-snapshot-reconstruction.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
api_service/api/routers/executions.py
frontend/src/lib/temporalTaskEditing.ts
frontend/src/entrypoints/task-create.tsx
moonmind/schemas/temporal_models.py
moonmind/workflows/tasks/task_contract.py
moonmind/workflows/temporal/service.py
moonmind/workflows/temporal/worker_runtime.py

tests/unit/api/routers/test_executions.py
tests/unit/workflows/temporal/test_temporal_service.py
tests/unit/workflows/temporal/test_temporal_worker_runtime.py
tests/unit/workflows/tasks/test_task_contract.py
frontend/src/entrypoints/task-create.test.tsx
tests/contract/test_temporal_execution_api.py
tests/integration/api/test_task_contract_normalization.py
tests/integration/temporal/test_full_retry_recovery_actions.py
```

**Structure Decision**: Use the existing API router, Temporal service/runtime, task contract, and Mission Control task editing helper boundaries. Add tests beside existing focused suites; do not introduce a new module unless schema validation for the snapshot becomes large enough to justify a local helper.

## Complexity Tracking

No constitution violations or extra architectural complexity are planned.
