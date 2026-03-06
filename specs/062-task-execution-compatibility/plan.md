# Implementation Plan: Task Execution Compatibility

**Branch**: `047-task-execution-compatibility` | **Date**: 2026-03-06 | **Spec**: `specs/047-task-execution-compatibility/spec.md`  
**Input**: Feature specification from `/specs/047-task-execution-compatibility/spec.md`

## Summary

Implement the Temporal task-execution compatibility bridge as runtime-authoritative behavior: keep `/tasks/list` and `/tasks/{taskId}` task-first while Temporal remains the execution substrate, add a canonical server-side task source index, normalize Temporal-backed list/detail payloads and status semantics, and move mixed-source task pagination/count behavior under a compatibility-owned API contract. The selected orchestration mode is runtime, so docs/spec-only output is explicitly insufficient.

## Technical Context

**Language/Version**: Python 3.11 backend services + vanilla JavaScript dashboard runtime  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing Temporal execution router/service stack, queue/orchestrator dashboard adapters  
**Storage**: PostgreSQL task/execution tables (`agent_jobs`, orchestrator run/task tables, `temporal_executions`) plus a planned persisted task source mapping/index table; filesystem and Temporal artifact refs remain the large-payload channel  
**Testing**: `./tools/test_unit.sh` (required unit/dashboard entrypoint), targeted contract API validation for `tests/contract/test_task_compatibility_api.py` and `tests/contract/test_temporal_execution_api.py`, and runtime scope checks via `.specify/scripts/bash/validate-implementation-scope.sh`  
**Target Platform**: Docker Compose MoonMind API + worker stack with Temporal-enabled runtime and the existing `/tasks/*` dashboard shell  
**Project Type**: Multi-service backend with static dashboard frontend  
**Performance Goals**: Preserve bounded list/detail latency for task views, keep Temporal-only pagination exact and stable, and keep mixed-source pagination deterministic without raw backend token leakage  
**Constraints**: Preserve task-first UX vocabulary, keep `taskId == workflowId` for Temporal-backed rows, keep queue-backed manifest jobs on `source=queue`, never expose secret/raw prompt blobs through Memo/Search Attribute compatibility payloads, and keep runtime-vs-docs behavior aligned to runtime implementation mode  
**Scale/Scope**: Task list/detail/action compatibility across queue, orchestrator, and Temporal sources, with `MoonMind.Run` and `MoonMind.ManifestIngest` as the Temporal v1 workflow types in scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The feature stays inside the existing API/dashboard/runtime stack and adds no new operator prerequisite beyond the current Compose services.
- **II. Avoid Vendor Lock-In**: PASS. Compatibility contracts are task-facing and portable; Temporal-specific controls remain isolated behind adapters and existing `/api/executions` surfaces.
- **III. Own Your Data**: PASS. Compatibility payloads continue using inspectable JSON metadata and artifact references instead of opaque vendor-only formats.
- **IV. Skills Are First-Class and Easy to Add**: PASS. The feature does not bypass the runtime skill model and does not change task/skill composition semantics.
- **V. Design for Replaceability**: PASS. The plan adds explicit compatibility schemas/services instead of scattering ad hoc field rewrites across UI code.
- **VI. Powerful Runtime Configurability**: PASS. Source filtering, count modes, and Temporal thresholds remain request/config driven; runtime mode remains explicit.
- **VII. Modular and Extensible Architecture**: PASS. Changes stay within router/schema/service/dashboard boundaries with a dedicated compatibility layer and a persisted source index.
- **VIII. Self-Healing by Default**: PASS. Stable task identity, persisted source mapping, and explicit terminal-action handling improve retry-safe behavior and recovery after reruns/Continue-As-New.
- **IX. Facilitate Continuous Improvement**: PASS. Normalized detail/debug fields and count-mode metadata improve observability for operators and future migration work.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-*` mappings are explicit in the traceability artifact and runtime mode remains a hard gate.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep compatibility logic explicit, additive, and testable.
- PASS. Runtime mode remains authoritative: completion requires production runtime code plus validation tests.
- PASS. No constitutional violations require entries in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/047-task-execution-compatibility/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── task-execution-compatibility.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
docs/Temporal/
└── TaskExecutionCompatibilityModel.md

api_service/
├── api/routers/
│   ├── executions.py
│   ├── task_compatibility.py
│   ├── task_dashboard.py
│   └── task_dashboard_view_model.py
├── db/models.py
├── main.py
├── migrations/versions/
│   └── *task_source_mapping*.py
└── static/task_dashboard/dashboard.js

moonmind/
├── schemas/
│   ├── task_compatibility_models.py
│   └── temporal_models.py
└── workflows/
    ├── tasks/
    │   ├── compatibility.py
    │   └── source_mapping.py
    └── temporal/
        └── service.py

tests/
├── contract/
│   ├── test_task_compatibility_api.py
│   └── test_temporal_execution_api.py
├── task_dashboard/
│   └── test_queue_layouts.js
└── unit/
    ├── api/routers/
    │   ├── test_task_compatibility.py
    │   ├── test_task_dashboard.py
    │   └── test_task_dashboard_view_model.py
    └── workflows/tasks/
        └── test_task_compatibility_service.py
```

**Structure Decision**: Implement a dedicated compatibility layer inside the existing API/runtime modules rather than overloading the queue alias router or creating a separate frontend surface. `/api/executions` remains the Temporal-native control plane; `/api/tasks/list` and `/api/tasks/{taskId}` become task-facing compatibility surfaces.

## Phase 0 - Research Summary

Research outcomes in `specs/047-task-execution-compatibility/research.md` establish:

1. Add a dedicated task compatibility facade instead of teaching the dashboard to keep merging raw source payloads indefinitely.
2. Use a persisted task source mapping/global task index for `/tasks/{taskId}` resolution rather than backend probing or ID-shape rules.
3. Keep Temporal-only list/detail/action truth in the existing `/api/executions*` surfaces, but normalize task-facing payloads through shared compatibility schemas.
4. Add a compatibility-owned merged cursor/count contract for mixed-source lists; raw Temporal page tokens remain valid only for Temporal-only views.
5. Allowlist and bound Search Attributes, Memo fields, and parameter previews so compatibility payloads stay secret-safe.
6. Keep Temporal-backed manifest executions as `source=temporal` + `entry=manifest` and preserve queue-backed manifest jobs as `source=queue`.
7. Treat runtime mode as a hard completion gate: production code changes and validation tests are both required.

## Phase 1 - Design Outputs

- **Data Model**: `data-model.md` defines the persisted source index, normalized task list/detail records, mixed-source cursor envelope, and action/debug availability models.
- **API Contract**: `contracts/task-execution-compatibility.openapi.yaml` defines task-compatible list/detail/resolution routes and the Temporal action endpoints they compose with.
- **Traceability**: `contracts/requirements-traceability.md` maps `DOC-REQ-001` through `DOC-REQ-011` to FRs, planned implementation surfaces, implementation task coverage, validation task coverage, and validation strategy.
- **Execution Guide**: `quickstart.md` defines the runtime-mode implementation and verification sequence, including repository-standard test commands and runtime scope gates.

## Implementation Strategy

### 1. Add a task compatibility facade

- Introduce dedicated schemas and service helpers for normalized task rows/details instead of reusing queue-only or Temporal-only payloads directly.
- Add `GET /api/tasks/list` as the compatibility list API used by `/tasks/list`.
- Add `GET /api/tasks/{taskId}` as the canonical task detail API used by the unified task shell.
- Keep `/api/tasks` as the existing queue alias for backward compatibility; do not silently change its semantics.

### 2. Persist source resolution in a global task index

- Add a `task_source_mappings` table keyed by `task_id` with canonical `source`, `entry`, durable source identifier, owner metadata, and timestamps.
- Populate/update mappings on queue/orchestrator/Temporal task creation and on compatibility reads for legacy rows that predate the table.
- Change `/api/tasks/{taskId}/resolution` and unified detail routing to read this index first; probing backends becomes a temporary fallback only for unmigrated rows.
- Preserve Temporal-backed stable identity by storing `task_id == workflow_id` for Temporal rows even when `temporalRunId` changes after Continue-As-New.

### 3. Normalize Temporal-backed task payloads safely

- Add shared normalization helpers that turn `TemporalExecutionRecord` plus lifecycle metadata into task-compatible row/detail payloads.
- Preserve the full normalized field set required by the source document: task ID, source, entry, status, raw state, Temporal status, owner metadata, timestamps, artifacts, and canonical detail route.
- Add reviewed `actions` and `debug` blocks on detail responses so the unified task shell can render enabled controls and raw lifecycle context without exposing unsafe internals.
- Bound and allowlist Memo/Search Attribute output; expose only task-safe parameter summaries instead of raw execution parameters.

### 4. Move mixed-source list behavior under a server-owned contract

- Replace the current dashboard-only client merge as the documented contract with a server-side compatibility list API.
- Use canonical sorting by normalized `updatedAt DESC` with a deterministic tie-breaker and source-aware merge pagination.
- Encode mixed-source cursor state as a compatibility-owned opaque token containing per-source continuation state; never leak a raw Temporal `nextPageToken` as a universal mixed-source cursor.
- Return `count` only when reliable and always pair it with `countMode` (`exact` or `estimated_or_unknown`).

### 5. Keep Temporal-native actions behind task-facing UX

- Continue using the existing `/api/executions` create/update/signal/cancel endpoints as the Temporal-native control plane.
- Make compatibility detail payloads advertise task-facing action semantics (`rename`, `edit_inputs`, `rerun`, `approve`, `pause`, `resume`, `deliver_callback`, `cancel`) that map onto those endpoints.
- Preserve accepted/applied/message semantics for updates and graceful-by-default cancellation semantics for user-facing task flows.
- Keep owner/admin authorization checks explicit on all control paths and preserve no-op/unavailable responses for terminal executions.

### 6. Dashboard integration and manifest behavior

- Update the dashboard runtime config and `dashboard.js` list/detail loaders to consume `GET /api/tasks/list` and `GET /api/tasks/{taskId}` for canonical task views.
- Keep `/tasks/list` and `/tasks/{taskId}` as the only required product routes; source-specific Temporal routes remain optional sugar only.
- Preserve `source=temporal` as a first-class task source in the dashboard config and retain `entry=manifest` for Temporal-backed manifest runs.
- Ensure queue-backed manifest jobs remain under queue/manifests surfaces until their runtime actually migrates.

### 7. Validation strategy

- Add contract tests for unified task list/detail/resolution responses, mixed-source cursor/count behavior, and Temporal-backed compatibility payload normalization.
- Extend Temporal contract tests to cover action semantics that matter for task compatibility: rerun identity stability, graceful cancel default, forced termination path, and owner authorization.
- Add unit tests for the new source-mapping/index service, normalization helpers, and task detail routing precedence.
- Add dashboard runtime tests to verify canonical `/tasks/list` and `/tasks/{taskId}` behavior with Temporal-backed data and mixed-source pagination state.
- Run `./tools/test_unit.sh` for unit and dashboard validation, then run targeted contract pytest for `tests/contract/test_task_compatibility_api.py` and `tests/contract/test_temporal_execution_api.py`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- This feature must ship production runtime code changes and validation tests; documentation/spec artifacts are supporting inputs only.
- Docs-only completion is explicitly non-compliant with FR-001, FR-002, and SC-006.
- Runtime scope validation remains part of the implementation plan:
  - `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
  - `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`

## Remediation Gates

- Every `DOC-REQ-*` row must remain mapped to FRs, planned implementation surfaces, implementation task coverage, validation task coverage, and validation strategy.
- Runtime mode requires both production implementation tasks and validation tasks in `tasks.md`; docs-only task sets are invalid.
- Unified task detail routing must remain anchored to the persisted source-mapping/global task-index contract rather than backend probing or ID-shape rules.
- Temporal manifest compatibility must remain `source=temporal` plus `entry=manifest` without relabeling queue-backed manifest jobs.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime mode scope gate is explicitly satisfied by production runtime code tasks (`T001-T008`, `T013-T017`, `T021-T026`, `T030-T034`) and validation tasks (`T009-T012`, `T018-T020`, `T027-T029`, `T036-T038`) in `tasks.md`.
- `DOC-REQ-*` traceability now includes deterministic implementation-task and validation-task mappings for every source requirement (`DOC-REQ-001` through `DOC-REQ-011`) in both `tasks.md` and `contracts/requirements-traceability.md`.
- Cross-artifact determinism is preserved: runtime-authoritative scope, source-mapping rules, and validation gate language now align across `spec.md`, `plan.md`, `tasks.md`, and `contracts/requirements-traceability.md`.
- Validation execution is explicit: `./tools/test_unit.sh` covers unit/dashboard suites, while targeted contract pytest covers `tests/contract/test_task_compatibility_api.py` and `tests/contract/test_temporal_execution_api.py`.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls in `tasks.md` so runtime implementation and validation expectations remain auditable before implementation starts.
- Reinforced plan-level traceability gates so any unmapped `DOC-REQ-*` or missing task coverage remains a plan-invalidating condition.

### Residual risks

- Compatibility behavior spans API routers, Temporal services, normalization helpers, dashboard code, and persistence layers; semantic drift remains possible if changes bypass shared helpers and contract tests.
- Mixed-source cursor/count logic and source-resolution backfill behavior still depend on disciplined follow-through during runtime implementation and verification.

## Risks & Mitigations

- **Risk: source resolution drift while legacy rows lack mapping entries**.
  - **Mitigation**: add a persisted mapping table plus deterministic backfill-on-read/write behavior and targeted legacy coverage tests.
- **Risk: mixed-source cursor logic becomes brittle or leaks backend-specific tokens**.
  - **Mitigation**: centralize cursor encoding/decoding in the compatibility service and keep raw Temporal tokens nested inside server-owned cursor state only.
- **Risk: secret or oversized payloads leak through Search Attributes, Memo, or parameters**.
  - **Mitigation**: use explicit allowlists, size caps, and artifact refs; add tests for redaction/bounding behavior.
- **Risk: dashboard detail/action code keeps depending on source-specific heuristics**.
  - **Mitigation**: switch canonical list/detail flows to the compatibility APIs and preserve resolution tests for Temporal opaque IDs and ambiguous legacy IDs.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
