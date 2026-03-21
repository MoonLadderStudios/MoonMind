# Implementation Plan: Manifest Ingest Runtime

**Branch**: `049-manifest-ingest-runtime` | **Date**: 2026-03-06 | **Spec**: `specs/049-manifest-ingest-runtime/spec.md`  
**Input**: Feature specification from `/specs/049-manifest-ingest-runtime/spec.md`

## Summary

Implement `docs/RAG/ManifestIngestDesign.md` as a runtime-authoritative Temporal feature, not a projection-only placeholder. The current repo already stages registry manifests into Temporal artifacts, persists `MoonMind.ManifestIngest` execution metadata, exposes shared execution APIs, and defines task-queue/worker-fleet topology, but it does not yet run real Temporal workflows, schedule child `MoonMind.Run` executions, persist canonical plan/summary/run-index artifacts, or expose manifest-specific update/query behavior. This plan closes those gaps with production runtime code, API/detail surface updates, worker execution support, and automated validation through `./tools/test_unit.sh`.

## Technical Context

**Language/Version**: Python 3.11, OpenAPI YAML, JavaScript task dashboard, shell bootstrap scripts  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing MoonMind Temporal artifact/lifecycle services, `temporalio` Python SDK (planned runtime dependency), PyYAML manifest parsing  
**Storage**: PostgreSQL manifest registry + `temporal_executions` projection tables, Temporal artifact metadata/link tables, MinIO/S3-compatible or local artifact blob storage for manifest/plan/checkpoint/summary/run-index payloads  
**Testing**: `./tools/test_unit.sh` (required), unit tests under `tests/unit/workflows/temporal/`, API/contract tests under `tests/unit/api/routers/` and `tests/contract/`, dashboard JS tests under `tests/task_dashboard/`, targeted Temporal integration coverage under `tests/integration/temporal/`  
**Target Platform**: Linux Docker Compose MoonMind deployment with Temporal server, private worker fleets, and artifact storage reachable on internal networks  
**Project Type**: Backend runtime + Temporal workflow worker + API/detail compatibility feature  
**Performance Goals**: Keep workflow payloads reference-sized, enforce bounded child concurrency, maintain stable manifest lineage pagination via run-index artifacts, and Continue-As-New before configured history/checkpoint thresholds are crossed  
**Constraints**: Runtime implementation mode is mandatory; queue-backed manifest flows stay `source=queue` until separately migrated; workflow code must remain deterministic; task queues remain routing-only; the existing shared execution contract's accepted `failurePolicy` values (`fail_fast`, `continue_and_report`, `best_effort`) must remain caller-visible without silent coercion; secrets/high-cardinality payloads cannot enter workflow history, Search Attributes, or Memo; `.specify` runtime gates must use `SPECIFY_FEATURE=049-manifest-ingest-runtime` in this workspace because the checked-out branch name is a MoonMind task branch rather than the feature branch name  
**Scale/Scope**: Manifest ingest workflow execution, manifest compile/checkpoint/index artifacts, child-run lineage, manifest-specific update/query/detail surfaces, workflow-worker registration, and automated validation coverage

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The feature builds on the existing Docker Compose Temporal stack and worker services without introducing new essential external services.
- **II. Avoid Vendor Lock-In**: PASS. Workflow inputs, artifacts, lineage indexes, and API payloads remain MoonMind-owned portable formats; Temporal-specific behavior stays behind runtime modules.
- **III. Own Your Data**: PASS. Manifest, plan, checkpoint, summary, and run-index artifacts remain operator-controlled and artifact-backed.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Manifest ingest composes with the existing activity catalog and skill execution routing instead of inventing a new runtime path.
- **V. Design for Replaceability / Scientific Method Loop**: PASS. Workflow orchestration, activity contracts, and validation stay explicit and testable so the runtime can evolve without hidden coupling.
- **VI. Powerful Runtime Configurability**: PASS. Concurrency defaults/caps, checkpoint thresholds, task queues, and artifact backend remain settings-driven rather than hardcoded.
- **VII. Modular and Extensible Architecture**: PASS. Work is localized to Temporal runtime modules, API/router/schema layers, worker bootstrap, and tests.
- **VIII. Self-Healing by Default**: PASS. Continue-As-New, idempotent activity behavior, and request-cancel parent-close semantics preserve recoverability.
- **IX. Facilitate Continuous Improvement**: PASS. Summary/run-index artifacts and shared visibility metadata improve operator diagnosis and future iteration.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-001` through `DOC-REQ-016` are fully traced into planning artifacts and remain gating requirements.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep manifest ingest runtime anchored to real workflow execution rather than projection-only metadata.
- PASS. Runtime-vs-docs alignment remains explicit: this feature is not eligible for docs-only completion.
- PASS. Design reuses shared visibility and artifact contracts instead of inventing manifest-specific parallel sources of truth.
- PASS. No constitution violations require exceptions in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/049-manifest-ingest-runtime/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── manifest-ingest-runtime.openapi.yaml
│   └── requirements-traceability.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/RAG/ManifestIngestDesign.md
pyproject.toml
docker-compose.yaml
services/temporal/scripts/start-worker.sh

moonmind/
├── config/settings.py
├── schemas/
│   ├── manifest_ingest_models.py                    # planned
│   └── temporal_models.py
└── workflows/
    └── temporal/
        ├── __init__.py
        ├── activity_catalog.py
        ├── activity_runtime.py
        ├── artifacts.py
        ├── client.py                                # planned
        ├── manifest_ingest.py                       # planned
        └── workers.py

api_service/
├── api/routers/
│   ├── executions.py
│   ├── manifests.py
│   └── task_dashboard_view_model.py
├── api/schemas.py
├── db/models.py
└── migrations/versions/
    └── 202603060001_manifest_temporal_runtime_metadata.py

tests/
├── contract/
│   └── test_temporal_execution_api.py
├── integration/temporal/
│   └── test_manifest_ingest_runtime.py              # planned
├── task_dashboard/
│   └── test_temporal_run_history.js
└── unit/
    ├── api/routers/
    │   ├── test_executions.py
    │   ├── test_manifests.py
    │   └── test_task_dashboard_view_model.py
    ├── services/
    │   └── test_manifests_service.py
    └── workflows/temporal/
        ├── test_activity_runtime.py
        ├── test_manifest_ingest.py                  # planned
        └── test_temporal_workers.py
```

**Structure Decision**: Keep the current monorepo layout and extend the existing Temporal runtime package in place. Manifest ingest should become a real workflow/runtime module under `moonmind/workflows/temporal/`, with API/detail surfaces layered on top of shared execution and artifact contracts rather than a separate manifest-only control plane.

## Phase 0 - Research Summary

Research outcomes in `specs/049-manifest-ingest-runtime/research.md` establish:

1. The repo already has enough control-plane foundation to avoid a greenfield rewrite: manifest submission, artifact staging, execution projection, and worker-fleet routing exist today.
2. Real Temporal workflow execution is still missing, so this feature must add the SDK-backed client/worker/workflow path rather than just expanding projection metadata.
3. Manifest compilation must produce a normalized plan artifact with stable node IDs based on canonical node content, not traversal order.
4. Manifest ingest side effects must stay in explicit activities for read/parse/validate/compile/index/summary persistence.
5. Child work should use `MoonMind.Run` with immutable ingest lineage and request-cancel parent-close semantics.
6. Update/query behavior must be manifest-specific, validated, bounded, and artifact-backed.
7. Concurrency, checkpointing, and Continue-As-New need manifest-specific settings in addition to the repo's existing lifecycle thresholds.
8. Shared visibility lists the ingest itself; the canonical run-index artifact remains the only authoritative per-manifest child-run pagination source in v1.
9. Runtime mode is the selected orchestration mode, so downstream tasks must include production code and automated validation.

## Phase 1 - Design Outputs

- **Research**: `research.md` captures repo-grounded implementation decisions, rationale, and rejected alternatives for runtime, lineage, updates, concurrency, and security.
- **Data Model**: `data-model.md` defines workflow inputs, execution policy, compiled plan nodes, checkpoint artifacts, summary/index artifacts, update contracts, and authorization lineage.
- **API Contract**: `contracts/manifest-ingest-runtime.openapi.yaml` defines planned submit, describe, update, status, and node-page surfaces for Temporal-native manifest ingest.
- **Traceability**: `contracts/requirements-traceability.md` maps every `DOC-REQ-*` requirement to FRs, concrete implementation surfaces, and planned validation strategy.
- **Execution Guide**: `quickstart.md` defines runtime-mode startup, submit, inspect, edit, and validation flow using the repository-standard test and scope-gate commands.

## Implementation Strategy

### 1. Add real Temporal runtime execution for manifest ingest

- Add the missing Temporal SDK dependency and runtime client layer under `moonmind/workflows/temporal/`.
- Convert `moonmind/workflows/temporal/workers.py` from topology-reporting only into a real worker entrypoint that can register workflows and activity handlers while preserving `--describe-json`.
- Add `moonmind/workflows/temporal/manifest_ingest.py` to host:
  - `MoonMind.ManifestIngest` workflow definition
  - manifest-specific update/query handlers
  - child `MoonMind.Run` orchestration logic
  - Continue-As-New resume behavior

### 2. Define manifest-ingest-specific schemas and persistence hooks

- Add dedicated runtime schemas for manifest ingest inputs, status, checkpoint, summary, and run-index payloads.
- Extend shared execution serialization so manifest detail can expose:
  - `planArtifactRef`
  - `summaryArtifactRef`
  - `runIndexArtifactRef`
  - manifest-specific counts and phase metadata
- Add any necessary DB projection columns or memo/search-attribute rules to keep those refs and bounded display fields accessible without duplicating large payloads.

### 3. Implement artifact-bounded manifest compile and checkpoint flow

- Reuse the existing artifact service for manifest read/write and introduce manifest-specific compile/checkpoint/index helpers in activity runtime.
- Enforce pipeline order:
  - read manifest artifact
  - parse/validate manifest
  - compile normalized plan artifact
  - persist checkpointable orchestration state
  - persist final summary and run-index artifacts
- Keep large manifest and plan bytes outside workflow history.

### 4. Orchestrate child runs with deterministic policy handling

- Schedule each ready executable node as a child `MoonMind.Run` workflow with immutable ingest lineage and artifact refs.
- Enforce config-driven concurrency defaults and hard caps.
- Implement deterministic behavior for:
  - `FAIL_FAST`
  - `continue_and_report` as an explicit accepted compatibility value, not a hidden rewrite
  - `BEST_EFFORT`, with terminal state `failed` when any node fails
- Use checkpoint artifacts plus Continue-As-New when history or scheduling pressure approaches limits.

### 5. Expose manifest-specific control and inspection surfaces

- Extend execution update handling to support:
  - `UpdateManifest`
  - `SetConcurrency`
  - `Pause`
  - `Resume`
  - `CancelNodes`
  - `RetryNodes`
- Add bounded manifest status and node-list routes backed by workflow query state plus run-index/checkpoint artifacts.
- Keep `/api/manifests/{name}/runs` as a registry convenience wrapper, but make the resulting Temporal execution detail the authoritative runtime view.

### 6. Align shared visibility and dashboard/detail behavior

- Preserve shared execution visibility contract for the ingest execution (`mm_entry=manifest`, bounded memo/search attributes).
- Keep child-run pagination and totals sourced from the canonical run-index artifact until a shared lineage Search Attribute is standardized.
- Update dashboard runtime config/detail handling so manifest ingest detail uses:
  - shared Temporal execution detail for the ingest itself
  - artifact-backed lineage paging for child runs

### 7. Preserve authorization lineage and secrecy constraints

- Validate artifact access and caller authorization before parse/compile side effects begin.
- Propagate immutable authorization lineage into child workflows and relevant artifacts.
- Reuse secret-redaction and artifact-auth patterns so secrets, signed URLs, manifest bodies, and other high-cardinality payloads never appear in workflow history, Search Attributes, or Memo.

### 8. Validation strategy

- Add workflow-focused unit tests for compile, scheduling, update/query semantics, policy behavior, checkpointing, and parent-close handling.
- Extend service/router/contract tests for manifest submit, execution detail/update, and bounded node pagination.
- Extend dashboard tests where manifest ingest detail consumes the new run-index/detail contract.
- Run repository-standard validation with `./tools/test_unit.sh`.
- Run runtime scope gates with:
  - `SPECIFY_FEATURE=049-manifest-ingest-runtime ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
  - `SPECIFY_FEATURE=049-manifest-ingest-runtime ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion for this feature requires production runtime code changes plus automated validation tests.
- Docs/spec artifacts are supporting deliverables only and cannot satisfy the feature objective by themselves.
- Downstream `tasks.md` generation must preserve explicit runtime-file tasks and explicit validation tasks so runtime/docs behavior stays aligned with the selected mode.

## Risks & Mitigations

- **Risk: the repo still lacks real Temporal SDK workflow execution, so worker/runtime changes could sprawl into foundation work.**
  - **Mitigation**: build on the existing projection/artifact/worker-topology modules and keep new runtime ownership isolated to `moonmind/workflows/temporal/`.
- **Risk: manifest lineage totals or pagination drift if detail views mix visibility data with a separate DB projection.**
  - **Mitigation**: keep the ingest itself in shared execution detail and use the canonical run-index artifact as the only child-lineage source.
- **Risk: large manifests or high fan-out ingests pressure workflow history and worker memory.**
  - **Mitigation**: keep manifest/plan/checkpoint payloads artifact-backed, enforce bounded concurrency, and checkpoint before Continue-As-New thresholds.
- **Risk: `.specify` scope tooling can misresolve the feature on the current MoonMind task branch.**
  - **Mitigation**: standardize on `SPECIFY_FEATURE=049-manifest-ingest-runtime` for runtime scope checks and any later `.specify` automation in this workspace.
- **Risk: authorization or secret leakage regressions under child-run lineage and artifact linking.**
  - **Mitigation**: propagate immutable authorization lineage, reuse artifact authorization checks, and add explicit leak-prevention tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
