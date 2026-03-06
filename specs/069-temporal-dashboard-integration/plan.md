# Implementation Plan: Temporal Dashboard Integration

**Branch**: `048-temporal-dashboard-integration` | **Date**: 2026-03-06 | **Spec**: `specs/048-temporal-dashboard-integration/spec.md`  
**Input**: Feature specification from `/specs/048-temporal-dashboard-integration/spec.md`

## Summary

Finish the runtime-grade Temporal dashboard integration inside the existing `/tasks*` product surface. The implementation extends the current FastAPI + server-hosted dashboard shell so Temporal-backed work behaves as a first-class source with canonical route resolution, authoritative Temporal-only pagination, task-oriented detail/actions/submit behavior, latest-run artifact presentation, and automated validation coverage. Runtime mode is selected for this feature, so completion requires production code changes and tests rather than docs-only artifacts.

## Technical Context

**Language/Version**: Python 3.11 backend, vanilla JavaScript dashboard client, HTML/Jinja templates, Markdown/OpenAPI planning artifacts  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async session layer, existing `TemporalExecutionService`, Temporal artifact APIs, server-hosted dashboard runtime config builder  
**Storage**: PostgreSQL-backed Temporal execution projection plus artifact metadata/store exposed through MoonMind artifact APIs  
**Testing**: `./tools/test_unit.sh` for repository-standard unit coverage plus dashboard JS suites, targeted FastAPI contract tests in `tests/contract/`, and targeted browser/e2e validation for canonical routes and submit redirects  
**Target Platform**: Linux Docker Compose MoonMind deployment with the existing task dashboard shell and Temporal-backed REST APIs  
**Project Type**: Multi-surface web feature spanning backend routers, runtime config/settings, dashboard client code, and validation tests  
**Performance Goals**: Preserve current dashboard polling UX, keep mixed-source list fetches bounded, preserve authoritative Temporal `count`/`nextPageToken` semantics when `source=temporal`, and avoid extra browser hops beyond one source-resolution call for canonical detail routing  
**Constraints**: Temporal remains a dashboard source and must not appear as a worker runtime picker value; browser traffic must stay on MoonMind-owned REST APIs; v1 detail is latest-run artifact scoped and must not expose raw Temporal event history; runtime mode requires production code plus validation tests; docs-mode semantics must stay documented but non-authoritative for this feature  
**Scale/Scope**: Runtime config flags and source descriptors, canonical task-source resolution, Temporal list/detail normalization, authoritative `repo`/`integration` filter and `countMode` passthrough behavior, action gating, artifact-first submit/upload routing, identifier compatibility including reserved legacy `runId`, mixed-source compatibility, and traceable validation coverage for `DOC-REQ-001` through `DOC-REQ-019`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The dashboard continues to run inside the existing Docker Compose app shell with Temporal accessed only through MoonMind APIs; no new mandatory external dependency is introduced for operators.
- **II. Avoid Vendor Lock-In**: PASS. Temporal-specific behavior is isolated to the Temporal source adapter, execution router/service contracts, and dashboard source-normalization paths rather than leaking into the generic runtime picker contract.
- **III. Own Your Data**: PASS. Artifact and execution reads stay inside MoonMind-managed REST/API layers and operator-controlled storage rather than browser-direct Temporal or third-party UI access.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Submit integration explicitly preserves the existing task-shaped runtime/skill workflow and avoids overloading runtime selection with engine semantics.
- **V. The Bittersweet Lesson / Scientific Method Loop**: PASS. The plan keeps contracts and validation at the seam between dashboard UI and Temporal-backed APIs so implementation details remain replaceable.
- **VI. Powerful Runtime Configurability**: PASS. Temporal dashboard rollout is planned behind env-backed feature flags and runtime config rather than hardcoded client behavior.
- **VII. Modular and Extensible Architecture**: PASS. Changes stay within dashboard router/view-model/client, execution/artifact adapter surfaces, and settings/tests without forcing a separate dashboard backend.
- **VIII. Self-Healing by Default**: PASS. Route resolution, action gating, latest-run artifact lookup, and post-action refresh behavior remain idempotent/retry-safe at the UI/API boundary.
- **IX. Facilitate Continuous Improvement**: PASS. Validation artifacts and phased feature flags make rollout behavior observable and reversible.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. The plan keeps all `DOC-REQ-*` requirements mapped to implementation surfaces and validation strategy before task generation.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep Temporal as a source, not a runtime picker value, and preserve task-oriented UX language.
- PASS. Runtime-vs-docs alignment remains explicit: this feature is runtime mode and requires implementation plus automated validation tests.
- PASS. No constitution violations require a Complexity Tracking exception.

## Project Structure

### Documentation (this feature)

```text
specs/048-temporal-dashboard-integration/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── temporal-dashboard-view-model.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── config/settings.py
├── schemas/temporal_models.py
└── workflows/temporal/
    └── service.py

api_service/
├── api/routers/
│   ├── executions.py
│   ├── task_dashboard.py
│   ├── task_dashboard_view_model.py
│   └── temporal_artifacts.py
├── static/task_dashboard/
│   ├── dashboard.js
│   ├── dashboard.css
│   └── dashboard.tailwind.css
└── templates/task_dashboard.html

tests/
├── contract/
│   ├── test_temporal_execution_api.py
│   └── test_temporal_artifact_api.py
├── task_dashboard/
│   ├── test_queue_layouts.js
│   ├── test_submit_runtime.js
│   └── test_temporal_dashboard.js              # planned
├── unit/api/routers/
│   ├── test_executions.py
│   ├── test_task_dashboard.py
│   └── test_task_dashboard_view_model.py
└── e2e/
    └── test_task_create_submit_browser.py      # planned Temporal redirect coverage
```

**Structure Decision**: Extend the existing thin dashboard architecture in place. Keep source resolution in FastAPI routers, runtime flags in settings + `build_runtime_config()`, Temporal row/detail normalization in `dashboard.js`, and state/ownership/query semantics in the existing execution service/router rather than inventing a new UI-only backend.

## Phase 0 - Research Summary

Research in `specs/048-temporal-dashboard-integration/research.md` establishes:

1. Temporal must remain a first-class dashboard source and must not be introduced as a worker runtime picker value.
2. Runtime config must expose Temporal endpoints plus env-backed phased feature flags instead of relying on hardcoded client toggles.
3. Canonical detail routing stays `/tasks/:taskId`, with `taskId == workflowId` for Temporal-backed work and server-side source resolution as the documented contract.
4. Mixed-source list mode remains bounded and informational, while `source=temporal` preserves authoritative `/api/executions` pagination, `repo`/`integration` filtering, and `nextPageToken`/`count`/`countMode` semantics.
5. Temporal row/detail models must expose normalized task-oriented fields while preserving raw lifecycle metadata such as `rawState`, `temporalStatus`, `closeStatus`, `waitingReason`, and `attentionRequired`.
6. Detail loading must fetch execution detail first, then latest-run artifacts using the run ID returned by that detail response.
7. Action exposure must be state-aware and feature-flagged, mapping cleanly to existing update/signal/cancel APIs with task-oriented copy.
8. Submit integration must remain backend-routed and hidden from the standard runtime picker, using artifact-first payloads plus placeholder/upload/complete helper flows where needed and redirecting to canonical task detail on success.
9. Artifact presentation must stay inside MoonMind artifact authorization/download flows and default to latest-run evidence rather than mixed-run history.
10. Validation must cover runtime config, routing/source resolution, row normalization, detail behavior, action gating, submit routing, and artifact presentation through the repository-standard test commands.
11. Runtime-vs-docs mode behavior must remain aligned to selected runtime mode; docs-only completion is invalid for this feature.

## Phase 1 - Design Outputs

- `research.md`: final decisions, rationale, and rejected alternatives for source modeling, feature flags, routing, list/detail semantics, actions, submit, artifacts, validation, and mode alignment.
- `data-model.md`: Temporal dashboard source config, query model, normalized row/detail entities, action capability rules, artifact presentation model, and submit-routing state.
- `contracts/temporal-dashboard-view-model.md`: runtime config contract, source resolver contract, Temporal list/detail/action/submit integration contract, and feature-flag expectations.
- `contracts/requirements-traceability.md`: full `DOC-REQ-*` mapping to FRs, planned implementation surfaces, and validation coverage.
- `quickstart.md`: deterministic validation flow for local runtime verification and runtime-scope gates.

## Implementation Strategy

### 1. Runtime config and settings-backed rollout flags

- Replace hardcoded Temporal dashboard feature values with env-backed settings in `moonmind/config/settings.py`.
- Keep runtime config contract in `api_service/api/routers/task_dashboard_view_model.py` authoritative for:
  - `sources.temporal.*`,
  - `statusMaps.temporal`,
  - `features.temporalDashboard.*`,
  - `system.taskSourceResolver`.
- Preserve runtime/docs alignment by making runtime feature behavior configurable without rewriting docs-mode expectations.

### 2. Canonical route safety and source resolution

- Keep `/tasks/list`, `/tasks/new`, and `/tasks/:taskId` as canonical routes.
- Continue accepting Temporal-safe task IDs in the dashboard route shell.
- Treat `GET /api/tasks/{taskId}/source` as the documented route-resolution contract and keep `?source=temporal` as an explicit override/fallback.
- Ensure non-admin visibility rules for Temporal-backed detail resolution stay consistent with execution ownership policy.

### 3. Temporal list behavior and normalization

- Use `GET /api/executions` as the authoritative data source for `source=temporal`.
- Normalize Temporal execution payloads into shared dashboard row fields:
  - identity (`taskId`, `workflowId`, `temporalRunId`),
  - ownership,
  - status/raw metadata,
  - timestamps,
  - repository/integration,
  - wait/attention hints.
- Preserve `repo`, `integration`, `pageSize`, `nextPageToken`, `count`, and `countMode` semantics exactly in pinned Temporal views.
- Keep mixed-source list mode bounded and merge-sorted client-side without inventing global pagination guarantees.

### 4. Temporal detail and artifact-first evidence surface

- Load Temporal detail with:
  1. `GET /api/executions/{workflowId}`
  2. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`
- Render normalized header metadata, wait/attention cues, latest-run metadata, synthesized timeline rows, and artifact evidence without exposing raw Temporal event history.
- Respect artifact preview/default-read/raw-access policies through MoonMind artifact endpoints rather than direct blob or Temporal access.
- Keep reserved legacy `runId` semantics isolated from Temporal `temporalRunId` fields in detail/debug payloads and client state.

### 5. Action gating and task-oriented operator copy

- Drive action visibility from runtime flags plus execution state/capabilities.
- Map dashboard controls to existing API surfaces:
  - update: `UpdateInputs`, `SetTitle`, `RequestRerun`
  - signal: `Approve`, `Pause`, `Resume`
  - cancel: graceful default
- Keep copy task-oriented (`Task title`, `Pause task`, `Rerun`) and refresh detail after action completion.

### 6. Submit routing without Temporal runtime exposure

- Keep the worker runtime picker restricted to worker runtimes; do not add `temporal`.
- Add backend-routed Temporal create behavior only behind the planned submit flag.
- Reuse task-shaped submit forms and artifact references while mapping eligible submissions onto `POST /api/executions`.
- Include artifact placeholder/create, upload, and complete flows when large-input submissions cannot stay inline.
- Redirect successful Temporal-backed creates to `/tasks/{taskId}?source=temporal`.

### 7. Validation strategy

- Extend router/view-model unit tests for runtime config flags, route allowlists, and source resolution.
- Extend dashboard JS tests for Temporal row normalization, mixed-source behavior, source-pinned pagination, detail fetch sequencing, and action visibility.
- Extend execution/artifact contract tests to cover dashboard-required `repo`/`integration` filters, authoritative `countMode`, latest-run artifact scoping, artifact upload helper flows, reserved `runId` compatibility, and submit/action payload expectations.
- Extend browser/e2e validation for canonical route resolution, latest-run artifact fetch sequencing, and Temporal-backed submit redirects.
- Run repository-standard unit and dashboard-JS validation through `./tools/test_unit.sh`, including wiring `tests/task_dashboard/test_temporal_dashboard.js` into that JS test path.
- Run targeted `pytest` coverage for `tests/contract/test_temporal_execution_api.py`, `tests/contract/test_temporal_artifact_api.py`, and `tests/e2e/test_task_create_submit_browser.py` because those suites are not executed by `./tools/test_unit.sh` today.

## Runtime-vs-Docs Mode Alignment Gate

- Selected orchestration mode: **runtime implementation mode**.
- Required completion scope includes:
  - production runtime code changes in `api_service/`, `moonmind/`, and related dashboard/runtime modules, and
  - automated validation tests in `tests/` executed with `./tools/test_unit.sh` plus targeted contract/e2e `pytest` commands for the Temporal-specific suites.
- Docs-mode behavior is documented only to keep orchestration semantics aligned:
  - docs mode may skip runtime implementation-scope enforcement,
  - but this feature is not eligible for docs-only completion.

## Remediation Gates

- Every `DOC-REQ-*` row must map to one or more FRs, at least one planned implementation surface, and at least one planned validation strategy.
- Temporal must remain modeled as a dashboard source; any runtime-picker exposure is a planning failure.
- `source=temporal` views must preserve `/api/executions` pagination and count semantics exactly as returned by the backend.
- `source=temporal` views must preserve `/api/executions` `repo`/`integration` filters plus `nextPageToken`, `count`, and `countMode` semantics exactly as returned by the backend.
- Mixed-source list mode must remain bounded/informational and must not claim authoritative global pagination.
- Detail must remain latest-run scoped for artifacts and must not fetch artifacts using a stale run ID from list rows.
- Temporal payloads and client models must keep reserved legacy `runId` semantics separate from `temporalRunId`.
- Runtime coverage must include artifact placeholder/upload/complete validation for Temporal-backed large-input submit flows.
- Runtime mode requires implementation and validation tasks in downstream `tasks.md`; docs-only task sets are invalid.

## Risks & Mitigations

- **Partial implementation drift**: The repo already contains some Temporal dashboard support; reconcile existing behavior to the documented contract and add regression tests before expanding functionality.
- **Feature-flag drift between settings and runtime config**: Centralize Temporal dashboard flags in settings and assert the exported runtime config in unit tests.
- **Route-resolution ambiguity across sources**: Keep server-side source resolution authoritative and preserve `?source=temporal` as a controlled override for migration.
- **Mixed-source UX confusion**: Make authoritative vs informational list semantics explicit in copy and tests.
- **Artifact/run mismatch after rerun or Continue-As-New**: Always derive artifact lookup from the latest detail response and cover rerun/latest-run behavior in validation.
- **Submit semantics leaking engine details**: Keep Temporal engine routing backend-owned and ensure runtime picker tests reject any Temporal option exposure.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
