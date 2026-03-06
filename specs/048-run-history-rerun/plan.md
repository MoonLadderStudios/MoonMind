# Implementation Plan: Run History and Rerun Semantics

**Branch**: `048-run-history-rerun` | **Date**: 2026-03-06 | **Spec**: `specs/048-run-history-rerun/spec.md`  
**Input**: Feature specification from `/specs/048-run-history-rerun/spec.md`

## Summary

Implement the Temporal run-history and rerun contract from `docs/Temporal/RunHistoryAndRerunSemantics.md` as runtime-authoritative behavior. The work hardens the existing latest-run execution projection and Continue-As-New rerun support, then aligns task/dashboard compatibility semantics so Temporal-backed work uses one durable logical identity (`workflowId` / `taskId`) across detail routing, list rows, artifact resolution, and rerun or lifecycle rollover, with automated validation proving stable logical identity and rotated current run metadata.

## Technical Context

**Language/Version**: Python 3.11, JavaScript (task dashboard), OpenAPI/Markdown spec artifacts  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing MoonMind Temporal execution/artifact services, task dashboard runtime config and browser bundle  
**Storage**: PostgreSQL `temporal_executions` latest-run projection, Temporal artifact metadata/link tables, artifact blob storage backing `api/artifacts`  
**Testing**: `./tools/test_unit.sh`, contract tests in `tests/contract/`, unit tests in `tests/unit/workflows/temporal/` and `tests/unit/api/routers/`, dashboard JS tests in `tests/task_dashboard/`  
**Target Platform**: Linux Docker Compose MoonMind API + worker deployment with Temporal-backed execution APIs and task dashboard  
**Project Type**: Backend runtime + compatibility API + dashboard integration feature  
**Performance Goals**: Stable task/detail identity across Continue-As-New; latest-run artifact lookup correctness; no duplicate logical rows for the same Temporal execution after rerun or lifecycle rollover  
**Constraints**: Runtime implementation mode is mandatory; `workflowId` remains canonical; `taskId == workflowId` for Temporal-backed rows; `runId` stays diagnostic metadata only; v1 must not introduce a first-class per-run history browser or immutable per-run projection; terminal rerun remains unsupported unless implemented explicitly  
**Scale/Scope**: Temporal execution service/schema/router alignment, Temporal artifact route consumption, task dashboard temporal list/detail behavior, and automated validation for rerun, rollover, compatibility routing, and latest-run resolution

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The feature lands inside the existing API/dashboard/runtime and test surfaces without adding new operator prerequisites or external services.
- **II. Avoid Vendor Lock-In**: PASS. The work standardizes product identity and API semantics around MoonMind-owned workflow metadata and artifact references rather than provider-specific UI handles.
- **III. Own Your Data**: PASS. Historical evidence remains artifact-backed and inspectable; no opaque provider-only read model is introduced.
- **IV. Skills Are First-Class and Easy to Add**: PASS. The change is orthogonal to skill authoring and preserves runtime-neutral workflow identity semantics for skill-driven executions.
- **V. Design for Replaceability**: PASS. Latest-run and rerun behavior stays in explicit service/router/dashboard contracts rather than hidden adapter fallbacks.
- **VI. Powerful Runtime Configurability**: PASS. Existing Continue-As-New thresholds remain config-driven, and runtime mode stays explicit in planning and validation gates.
- **VII. Modular and Extensible Architecture**: PASS. Changes stay within current temporal service, artifact router, execution schema, and dashboard compatibility boundaries.
- **VIII. Self-Healing by Default**: PASS. Idempotent rerun handling, stable identifiers, and explicit non-terminal rerun rules preserve retry-safe recovery behavior.
- **IX. Facilitate Continuous Improvement**: PASS. Continue-As-New cause metadata and latest-run detail semantics improve operator diagnosis without requiring Temporal-native UI exposure.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. All `DOC-REQ-*` rows map to planned runtime surfaces and validation in this plan package.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep `workflowId` as the only durable task/detail identity for Temporal-backed work.
- PASS. The design keeps the application database as a latest-run projection and defers immutable per-run history to future dedicated surfaces.
- PASS. Runtime mode remains authoritative: docs/spec updates support implementation but do not satisfy completion.
- PASS. No constitution violations require exceptions in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/048-run-history-rerun/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── temporal-run-history-rerun.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/Temporal/RunHistoryAndRerunSemantics.md

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        ├── service.py
        └── artifacts.py

api_service/
├── api/routers/
│   ├── executions.py
│   ├── task_dashboard.py
│   ├── task_dashboard_view_model.py
│   └── temporal_artifacts.py
└── static/task_dashboard/
    └── dashboard.js

tests/
├── contract/
│   ├── test_temporal_execution_api.py
│   └── test_temporal_artifact_api.py
├── task_dashboard/
│   └── test_temporal_run_history.js
└── unit/
    ├── api/routers/
    │   ├── test_executions.py
    │   ├── test_task_dashboard.py
    │   ├── test_task_dashboard_view_model.py
    │   └── test_temporal_artifacts.py
    └── workflows/temporal/
        └── test_temporal_service.py
```

**Structure Decision**: Keep the existing Temporal execution projection and task dashboard as the single v1 implementation path. Extend `moonmind/workflows/temporal/service.py` and the execution/artifact routers as the backend authority, then align dashboard routing/list/detail behavior on top of those latest-run APIs instead of adding a second projection or a parallel history endpoint.

## Phase 0 - Research Summary

Research outcomes in `specs/048-run-history-rerun/research.md` establish:

1. The repo already models one logical Temporal execution per `workflowId`, so v1 should strengthen that projection rather than add per-run database rows.
2. `RequestRerun` already uses Continue-As-New semantics in the service layer; implementation work should preserve that baseline while making compatibility and dashboard surfaces explicitly follow it.
3. `taskId == workflowId` is already exposed by execution detail serialization and should become the invariant row/detail identity for Temporal-backed dashboard surfaces.
4. `runId` and `temporalRunId` should remain current-run metadata for debugging and artifact lookup, never the primary task/detail route key.
5. Artifact loading must resolve the latest `temporalRunId` from detail at render time, not from stale list snapshots, to survive Continue-As-New.
6. Manual rerun, lifecycle-threshold rollover, and major reconfiguration must stay distinguishable through `continue_as_new_cause`, not inferred from `rerun_count`.
7. Terminal execution rerun remains a future explicit behavior; current safe posture is a non-applied update response.
8. Runtime mode is the selected orchestration mode for this feature; planning is invalid if it permits docs-only completion.

## Phase 1 - Design Outputs

- **Research**: `research.md` records the repo-aligned identifier, projection, rerun, artifact, and dashboard behavior decisions that remove ambiguity before implementation.
- **Data Model**: `data-model.md` defines logical execution identity, current Temporal run metadata, latest-run projection fields, task compatibility rows, rerun requests, and artifact resolution context.
- **API Contract**: `contracts/temporal-run-history-rerun.openapi.yaml` captures the latest-run execution detail/list/update/artifact contract surfaces and the canonical identifier rules they expose.
- **Traceability**: `contracts/requirements-traceability.md` maps every `DOC-REQ-*` to FRs, implementation surfaces, and planned validation strategy.
- **Execution Guide**: `quickstart.md` defines runtime-mode implementation and validation flow using the repository-standard test command and dashboard validation checks.

## Implementation Strategy

### 1. Normalize latest-run identity in execution APIs

- Keep `/api/executions/{workflowId}` as the latest/current execution view for one logical Temporal execution.
- Ensure execution serialization continues to expose:
  - `workflowId` as the canonical durable identity
  - `taskId` equal to `workflowId` for Temporal-backed compatibility payloads
  - `runId` and `temporalRunId` as current-run metadata only
  - `latestRunView=true` to make latest-run semantics explicit
- Tighten schema/router tests so route and payload identity cannot drift back toward `runId`-anchored behavior.

### 2. Harden rerun and Continue-As-New projection semantics

- Preserve `RequestRerun` as Continue-As-New on the same logical execution:
  - same `workflowId`
  - rotated `runId`
  - cleared run-local paused/waiting/terminal markers
  - reset threshold counters
  - summary and memo updates that record rerun
- Keep rerun-time `input_ref`, `plan_ref`, and `parameters_patch` updates audit-visible through artifact references and memo/summary context.
- Preserve workflow-type-specific restart state rules:
  - `MoonMind.Run` -> `planning` without `plan_ref`, otherwise `executing`
  - `MoonMind.ManifestIngest` -> `executing`
- Leave terminal rerun unsupported in v1 unless a dedicated explicit restart surface is designed.

### 3. Distinguish manual rerun from other same-execution rollover

- Keep `continue_as_new_cause` as the canonical classification for:
  - `manual_rerun`
  - `lifecycle_threshold`
  - `major_reconfiguration`
- Prevent dashboard/API copy from treating every `rerun_count` increment as a user-visible rerun.
- Preserve the latest-run row/detail identity across both manual rerun and automatic Continue-As-New without creating sibling logical rows.

### 4. Align task compatibility and dashboard routing on `workflowId`

- Make task-oriented compatibility flows consistently resolve Temporal-backed detail using `taskId == workflowId`.
- Extend dashboard temporal row normalization so list rows use stable logical identity instead of falling back to `runId`.
- Keep `/tasks/{taskId}` detail routing source-agnostic while ensuring Temporal-backed detail probes and links use the logical execution identifier.
- Preserve current dashboard behavior that shows latest run metadata in detail while keeping the route anchored to the logical task/execution handle.

### 5. Resolve artifacts from latest run metadata, not stale row snapshots

- Keep artifact listing endpoint shape `/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`.
- Require detail rendering to fetch execution detail first, then derive the artifact request from the returned current `temporalRunId`.
- Validate that rerun or rollover between list render and detail load still produces artifacts for the latest run instead of a stale prior run instance.

### 6. Preserve latest-run projection boundaries

- Keep `temporal_executions` as one mutable latest-run row per `workflowId`.
- Do not add a first-class v1 run-history list, historical-run route, or immutable per-run projection in this feature.
- Keep `startedAt` semantics as logical execution start; if a current-run start field is needed later, it must be introduced explicitly rather than inferred from existing fields.

### 7. Validation strategy

- Extend unit tests in `tests/unit/workflows/temporal/test_temporal_service.py` for rerun state reset, terminal rerun rejection, continue-as-new cause handling, and workflow-type-specific restart state.
- Extend execution API tests in `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py` for:
  - `taskId == workflowId`
  - stable latest-run detail payload semantics
  - rerun preserving `workflowId` while rotating `runId`
  - non-terminal and terminal `RequestRerun` outcomes
- Extend artifact API/router tests to prove latest-run artifact fetches use the current `temporalRunId`.
- Add dashboard runtime coverage in `tests/unit/api/routers/test_task_dashboard_view_model.py`, `tests/unit/api/routers/test_task_dashboard.py`, and `tests/task_dashboard/test_temporal_run_history.js` for stable temporal row IDs and detail/artifact routing behavior.
- Run repository-standard unit acceptance through `./tools/test_unit.sh`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion for this feature requires production runtime code changes plus automated validation tests.
- Documentation and spec artifacts support implementation but do not satisfy the delivery gate on their own.
- Downstream `tasks.md` generation must preserve this runtime-mode scope and keep validation work explicit across backend and dashboard surfaces.

## Remediation Gates

- Every `DOC-REQ-*` entry must remain mapped to FRs, implementation surfaces, and validation strategy in `contracts/requirements-traceability.md`.
- `workflowId` must remain the canonical route, bookmark, and compatibility identity for Temporal-backed work.
- `taskId == workflowId` must remain true for all Temporal-backed dashboard/API payloads.
- `runId` or `temporalRunId` may be displayed or used for artifact/debug resolution but must not become the primary product route key.
- Planning is invalid if it introduces a v1 per-run history browser, historical-run route, or docs-only completion path.

## Risks & Mitigations

- **Risk: dashboard list/detail code silently falls back to `runId` and reintroduces unstable row identity.**
  - **Mitigation**: add explicit temporal row normalization and JS/router tests that assert `taskId`/row ID stability.
- **Risk: artifact requests use stale run metadata after Continue-As-New.**
  - **Mitigation**: keep detail-first artifact resolution and add rerun-focused API/dashboard validation coverage.
- **Risk: operators misread automatic Continue-As-New as a user rerun.**
  - **Mitigation**: expose and test `continue_as_new_cause` semantics; do not infer user intent from `rerun_count`.
- **Risk: terminal rerun expectations diverge between docs and runtime.**
  - **Mitigation**: keep current non-applied terminal response explicit in contract, tests, and quickstart validation notes until a dedicated restart surface exists.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
