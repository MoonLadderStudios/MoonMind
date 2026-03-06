# Implementation Plan: Temporal Source of Truth and Projection Model

**Branch**: `048-source-truth-projection` | **Date**: 2026-03-06 | **Spec**: `specs/048-source-truth-projection/spec.md`  
**Input**: Feature specification from `/specs/048-source-truth-projection/spec.md`

## Summary

Implement the source-of-truth shift described in `docs/Temporal/SourceOfTruthAndProjectionModel.md` as production runtime behavior. The plan moves MoonMind from projection-authoritative execution lifecycle handling to Temporal-authoritative write, detail, list, and repair flows by using the current `TemporalExecutionCanonicalRecord` plus `TemporalExecutionRecord` split as a migration seam, adding Temporal control-plane and Visibility adapters, tightening compatibility serialization, and making repair/degraded-mode semantics explicit and testable.

## Technical Context

**Language/Version**: Python 3.11 backend/services, vanilla JavaScript dashboard compatibility surfaces, shell/curl validation flows  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing `moonmind/workflows/temporal` runtime package, `temporalio` Python SDK (planned for direct control-plane and Visibility wiring), Docker Compose Temporal foundation from `docker-compose.yaml`  
**Storage**: PostgreSQL-backed MoonMind app DB for projection/read-model tables, Temporal persistence + SQL Visibility via the self-hosted Temporal stack, artifact storage for large payload/log references  
**Testing**: `./tools/test_unit.sh` (required unit + dashboard JS gate), compose-backed contract validation via `docker compose -f docker-compose.test.yaml run --rm -e TEST_TYPE=contract pytest`, compose-backed Temporal integration validation via `docker compose -f docker-compose.test.yaml run --rm -e TEST_TYPE=integration/temporal pytest`, runtime scope validation via `validate-implementation-scope.sh` once `tasks.md` exists  
**Target Platform**: Linux Docker Compose MoonMind deployment with private-network Temporal services and dedicated Temporal worker fleet  
**Project Type**: Backend runtime + compatibility API/dashboard migration feature  
**Performance Goals**: No production ghost rows for Temporal-managed executions; write results reflect Temporal accept/reject outcomes; projection sync converges within seconds-level operational windows; Continue-As-New never creates duplicate primary rows for one Workflow ID  
**Constraints**: Selected orchestration mode is runtime; docs/spec-only completion is invalid; `taskId == workflowId` must hold for Temporal-backed compatibility rows; production writes must not fall back to projection-only mutation when Temporal is unavailable; count/sort semantics must reflect the active source truthfully  
**Scale/Scope**: `/api/executions`, Temporal execution service/model/migrations, compatibility/dashboard execution views, repair/backfill orchestration, degraded-mode policy, and automated lifecycle/repair/compatibility validation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The feature reuses the existing Docker Compose Temporal foundation and current API/worker services without introducing a new mandatory external dependency.
- **II. Avoid Vendor Lock-In**: PASS. Temporal-specific behavior is isolated to execution/visibility adapters and compatibility serializers; projections and API payloads remain portable data contracts.
- **III. Own Your Data**: PASS. Canonical lifecycle truth remains self-hosted in Temporal/Visibility, while projections and artifact refs stay in MoonMind-controlled stores.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Execution-authority changes do not introduce a separate skill path or hidden runtime bypass.
- **V. The Bittersweet Lesson**: PASS. The durable contract is the Temporal-authoritative execution model; the staging cache/projection seam remains replaceable implementation scaffolding.
- **VI. Powerful Runtime Configurability**: PASS. Source selection, degraded-mode policy, and Temporal connectivity remain env/config driven and observable.
- **VII. Modular and Extensible Architecture**: PASS. Work is scoped to the existing `moonmind/workflows/temporal`, `api_service/api/routers`, schema, and compatibility-view boundaries.
- **VIII. Self-Healing by Default**: PASS. Repair-on-read, sweeper/backfill, post-write sync, and orphan quarantine are explicit design outputs.
- **IX. Facilitate Continuous Improvement**: PASS. Sync metadata, source metadata, and degraded-mode observability remain first-class operator signals.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. All `DOC-REQ-*` entries are carried into planning artifacts with implementation surfaces and validation strategy.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep Temporal and Visibility as the runtime authority and treat projections strictly as derived/read-model state.
- PASS. Runtime-mode scope remains explicit: downstream `tasks.md` must include production code changes plus validation work.
- PASS. No constitution violations require a Complexity Tracking exception.

## Project Structure

### Documentation (this feature)

```text
specs/048-source-truth-projection/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── temporal-execution-source-contract.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/Temporal/
├── SourceOfTruthAndProjectionModel.md
├── VisibilityAndUiQueryModel.md
└── TaskExecutionCompatibilityModel.md

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        ├── __init__.py
        ├── service.py
        ├── client.py                     # planned Temporal control-plane adapter
        ├── visibility.py                 # planned Visibility-backed list/query adapter
        ├── projection_repair.py          # planned repair/backfill orchestration
        ├── artifacts.py
        └── workers.py

api_service/
├── api/routers/
│   ├── executions.py
│   ├── task_dashboard.py                # compatibility/task-oriented views
│   └── task_dashboard_view_model.py
├── db/models.py
└── migrations/versions/
    └── *temporal_execution*.py

tests/
├── contract/
│   └── test_temporal_execution_api.py
├── integration/
│   └── temporal/
│       └── test_source_truth_projection.py   # planned runtime + degraded-mode coverage
└── unit/
    ├── specs/
    │   └── test_doc_req_traceability.py      # planned feature-specific gate
    └── workflows/temporal/
        ├── test_temporal_service.py
        ├── test_projection_repair.py         # planned
        └── test_visibility_adapter.py        # planned
```

**Structure Decision**: Keep `moonmind/workflows/temporal/` as the single execution-authority package. Extend it with explicit Temporal client, Visibility, and projection-repair modules instead of pushing SDK calls into routers or introducing a second lifecycle service.

## Phase 0 - Research Summary

Research outcomes in `specs/048-source-truth-projection/research.md` establish:

1. Direct Temporal control-plane and Visibility reads must become the runtime authority for Temporal-managed executions.
2. `TemporalExecutionCanonicalRecord` is a migration seam and repair source, not the long-term public authority for list/detail semantics.
3. One projection row per Workflow ID remains the core local read-model invariant across Continue-As-New.
4. Write paths must go Temporal first and treat projection persistence failures as repair work, not evidence that the workflow never started or mutated.
5. Read paths must separate authoritative Temporal views from explicit compatibility/fallback views and report truthful `countMode` plus source metadata.
6. Repair orchestration should be hybrid: post-write refresh, repair-on-read, periodic sweeps, and startup/backfill.
7. Production degraded-mode policy must reject writes without Temporal and allow stale/partial reads only on explicitly approved routes.
8. Compatibility surfaces must preserve `taskId == workflowId`, source metadata, and no queue-order fiction for Temporal-backed work.
9. Runtime-mode scope is a hard gate for this feature.

## Phase 1 - Design Outputs

- **Research**: `research.md` records the control-plane, read-source, repair, degraded-mode, compatibility, and runtime-mode decisions needed before implementation.
- **Data Model**: `data-model.md` defines authoritative execution snapshots, projection rows, sync state, compatibility view payloads, repair jobs, and degraded-read decisions.
- **Runtime Contract**: `contracts/temporal-execution-source-contract.md` captures write-path, read-path, repair, degraded-mode, and Continue-As-New invariants for implementation handoff.
- **Traceability**: `contracts/requirements-traceability.md` maps `DOC-REQ-001` through `DOC-REQ-019` to FRs, planned implementation surfaces, and validation strategy.
- **Execution Guide**: `quickstart.md` defines the runtime-mode bring-up, API validation, repair/degraded-mode checks, and repository-standard test commands.

## Implementation Strategy

### 1. Introduce explicit Temporal control-plane and Visibility adapters

- Add a control-plane client module for workflow start/update/signal/cancel/describe operations and a Visibility module for list/filter/count behavior.
- Keep Temporal-specific API usage inside `moonmind/workflows/temporal/` so routers and serializers depend on stable MoonMind contracts.
- Treat the current source/projection tables as mirrors and repair aids, not substitutes for live Temporal authority.

### 2. Rework execution write paths to be Temporal-first

- Change `TemporalExecutionService.create_execution()` to authenticate/validate first, start the Temporal workflow, then mirror accepted execution identity into source/projection state.
- Route update, signal, and cancel flows through Temporal first and use authoritative workflow outcomes to determine API responses.
- Preserve idempotency helpers locally, but do not let cached projection responses invent acceptance that Temporal did not produce.
- On projection persistence failure after a successful Temporal mutation, mark repair work and return truthful source-backed state instead of erasing history.

### 3. Make list/detail reads truthful about their source

- Move `/api/executions` list/filter/count toward Visibility-backed behavior, including truthful `countMode` semantics and non-offset pagination once the route is genuinely Temporal-backed.
- Move detail/describe paths to Temporal execution state plus safe enrichment, while retaining explicit fallback modes only where the route contract permits them.
- Keep compatibility/task-oriented routes able to join projection data, but require them to preserve canonical identifiers and expose source metadata.

### 4. Lock the primary projection model around Workflow ID

- Preserve `workflow_id` as the primary key for `TemporalExecutionRecord`.
- Update `run_id` in place across Continue-As-New and prevent any duplicate primary projection row for one Workflow ID chain.
- Keep `rerun_count` and similar fields as convenience metadata only; run history remains Temporal-native.

### 5. Add deterministic projection sync and repair orchestration

- Add post-mutation best-effort sync immediately after authoritative Temporal writes.
- Add repair-on-read for missing, stale, or orphaned rows.
- Add periodic sweeper and startup/backfill jobs that compare Temporal truth to local projections and apply ordered repair rules.
- Quarantine orphaned rows instead of presenting them as active executions, and preserve operator diagnostics in `sync_error`/`sync_state`.
- Validate each repair trigger explicitly so periodic sweep, startup/backfill, and sync-state transitions are not left implied by implementation-only tasks.

### 6. Enforce degraded-mode policy explicitly

- Reject production start/update/signal/cancel/terminate requests when Temporal is unavailable.
- Allow projection-backed fallback reads only on routes that declare prototype, compatibility, join-dependent, or degraded-mode behavior.
- Surface stale/partial results honestly through source metadata, logging, and `countMode` rather than keeping projection-backed exactness hidden.
- Allow isolated local-dev/test projection-only behavior only behind explicit non-production mode checks.

### 7. Preserve compatibility surfaces without hiding Temporal backing

- Keep `taskId == workflowId` for Temporal-backed compatibility payloads.
- Ensure compatibility serializers and dashboard view models carry `sourceMode`, canonical IDs, and truthful sort/count semantics.
- Avoid queue-order semantics for Temporal-backed rows even when task-oriented labels remain user-facing.

### 8. Evolve persistence and migration surfaces safely

- Add/adjust migrations for projection metadata, source metadata, and any source-selection markers needed by compatibility routes.
- Backfill existing projection rows so the canonical source/projection split starts from a consistent baseline.
- Fail fast on unsupported mixed-mode inputs rather than silently coercing them into projection-authoritative behavior.

### 9. Validation strategy

- Extend unit coverage for Temporal-first write semantics, Continue-As-New in-place projection updates, repair ordering, periodic sweep/startup-backfill triggers, sync-state transitions (`fresh`, `stale`, `repair_pending`, `orphaned`), and degraded-mode write rejection.
- Extend contract coverage for `/api/executions` to assert Temporal-backed response semantics, truthful `countMode`, and absence of ghost-row success paths.
- Add integration coverage for Visibility-backed list/detail behavior, projection repair after persistence failure, repair-on-read, periodic sweep, startup/backfill flows, orphan quarantine, and mixed-source fallback honesty.
- Add a feature-specific `DOC-REQ` traceability gate once implementation lands so source-document mappings cannot drift unnoticed.
- Use `./tools/test_unit.sh` as the required unit + dashboard JS command, then run compose-backed contract and Temporal integration validation as separate acceptance steps.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion for this feature requires production runtime code changes plus automated validation tests.
- Planning, tasks, and validation must treat docs/spec artifacts as support material only; they do not satisfy delivery on their own.
- Downstream `tasks.md` must keep implementation tasks and validation tasks explicit, and runtime scope gates must remain enabled.

## Remediation Gates

- Every `DOC-REQ-*` row must remain mapped to FRs, planned implementation surfaces, and validation strategy in `contracts/requirements-traceability.md`.
- Production write paths must remain Temporal-first and must not silently fall back to projection-only mutation.
- `workflow_id` remains the only primary key for the main execution projection; Continue-As-New cannot create duplicate primary rows.
- Compatibility routes must preserve `taskId == workflowId` for Temporal-backed rows and keep source metadata explicit.
- Planning is invalid if it allows docs-only completion for this runtime-scoped feature.

## Risks & Mitigations

- **Risk: the repo currently has no direct Temporal SDK execution adapter, so implementation could stall at the source/projection-table seam.**
  - **Mitigation**: make the control-plane and Visibility adapter modules the first implementation milestone and keep routers isolated from SDK details.
- **Risk: partial migration leaves `/api/executions` or compatibility routes reporting projection-backed exactness as if it were Temporal truth.**
  - **Mitigation**: add contract tests for `countMode`, source metadata, and identifier bridge behavior before switching default read paths.
- **Risk: repair logic can hide real outages by over-eagerly recreating rows or suppressing orphan diagnostics.**
  - **Mitigation**: enforce ordered repair rules, quarantine orphaned rows, and keep `sync_state` plus `sync_error` operator-visible.
- **Risk: degraded-mode exceptions for local/dev leak into production semantics.**
  - **Mitigation**: gate fallback behavior behind explicit runtime mode/config checks and add negative tests for production write fallback.
- **Risk: Continue-As-New handling regresses by creating duplicate projection rows or stale run IDs across adapters.**
  - **Mitigation**: centralize projection upsert-by-Workflow-ID logic and cover it with unit + integration tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| None | N/A | N/A |
