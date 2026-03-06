# Implementation Plan: Temporal Artifact Presentation Contract

**Branch**: `047-temporal-artifact-presentation` | **Date**: 2026-03-06 | **Spec**: `specs/047-temporal-artifact-presentation/spec.md`  
**Input**: Feature specification from `/specs/047-temporal-artifact-presentation/spec.md`

## Summary

Implement the missing Temporal artifact presentation contract in the runtime dashboard path by keeping `/tasks/:taskId` task-oriented, resolving Temporal detail through `taskId == workflowId`, fetching artifacts only after execution detail reveals the latest `temporalRunId`, and rendering preview-first artifact access that respects raw-access policy metadata. Delivery remains runtime-authoritative: production code plus validation tests, not docs-only updates.

## Technical Context

**Language/Version**: Python 3.11 and browser-side JavaScript (CommonJS test harness for dashboard runtime tests)  
**Primary Dependencies**: FastAPI, Jinja runtime config/template shell, existing dashboard client in `api_service/static/task_dashboard/dashboard.js`, Temporal execution APIs, Temporal artifact APIs  
**Storage**: Existing Temporal execution store plus artifact metadata/blob storage already exposed through `/api/executions/*` and `/api/artifacts/*`; no new storage system required  
**Testing**: `./tools/test_unit.sh`, Node-based dashboard runtime tests under `tests/task_dashboard/`, Python unit tests under `tests/unit/api/routers/`, and `DOC-REQ-*` traceability validation in `tests/unit/specs/test_doc_req_traceability.py`  
**Target Platform**: MoonMind server-hosted task dashboard served from `api_service` in local/dev Docker Compose and standard runtime deployments  
**Project Type**: Web application feature spanning backend route/config surfaces and a server-hosted frontend runtime  
**Performance Goals**: Keep Temporal detail load to a deterministic two-step fetch (`detail` then latest-run `artifacts`) and preserve responsive artifact actions without exposing raw history JSON or extra mixed-run queries  
**Constraints**: Runtime implementation mode is mandatory; docs-only completion is invalid. Preserve canonical `/tasks/:taskId` routing, keep `taskId == workflowId` for Temporal-backed work, and do not introduce Temporal as a user-facing runtime selector  
**Scale/Scope**: Temporal task detail route handling, dashboard runtime config, Temporal detail rendering, artifact presentation/access behavior, and regression coverage for route/run-scope/access-policy behavior

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The feature reuses the existing API/dashboard stack with no new operator prerequisites.
- **II. Avoid Vendor Lock-In**: PASS. Temporal-specific detail behavior is implemented through MoonMind-owned REST/view-model contracts, not direct browser coupling to Temporal.
- **III. Own Your Data**: PASS. Artifact presentation remains backed by MoonMind execution and artifact APIs with operator-controlled storage.
- **IV. Skills Are First-Class and Easy to Add**: PASS. The feature does not alter skill execution semantics or skill authoring flows.
- **V. Design for Evolution / Scientific Method Loop**: PASS. UI behavior is anchored by explicit contracts and repeatable runtime tests so adapter internals remain replaceable.
- **VI. Powerful Runtime Configurability**: PASS. Temporal dashboard behavior remains controlled by runtime config and feature flags instead of hardcoded hidden modes.
- **VII. Modular and Extensible Architecture**: PASS. Changes stay within route shell, dashboard view-model config, Temporal detail renderer, and tests.
- **VIII. Self-Healing by Default**: PASS. Latest-run resolution and artifact access use deterministic re-fetch behavior instead of stale cached run identifiers.
- **IX. Facilitate Continuous Improvement**: PASS. The runtime contract adds explicit validation around route resolution, run scoping, and access-policy handling.
- **X. Spec-Driven Development**: PASS. `DOC-REQ-001` through `DOC-REQ-010` are traced to design artifacts and validation strategy.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep the feature in the runtime path (`api_service/api/routers/*`, `api_service/static/task_dashboard/*`, tests) rather than drifting into docs-only output.
- PASS. The design preserves task-oriented routing/copy while allowing advanced Temporal metadata as secondary detail.
- PASS. Runtime-vs-docs behavior remains explicitly aligned: runtime mode requires production code plus tests, while docs mode is documented only for scope-check semantics.

## Project Structure

### Documentation (this feature)

```text
specs/047-temporal-artifact-presentation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── temporal-artifact-presentation-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── task_dashboard.py
│   ├── task_dashboard_view_model.py
│   ├── executions.py
│   └── temporal_artifacts.py
├── static/task_dashboard/
│   ├── dashboard.js
│   └── dashboard.css
└── templates/task_dashboard.html

moonmind/
└── schemas/
    └── temporal_artifact_models.py

tests/
├── task_dashboard/
│   └── test_temporal_detail_runtime.js
└── unit/
    ├── api/routers/
    │   ├── test_task_dashboard.py
    │   ├── test_task_dashboard_view_model.py
    │   └── test_temporal_artifacts.py
    └── specs/
        └── test_doc_req_traceability.py
```

**Structure Decision**: Extend the existing dashboard shell and Temporal adapter surfaces in place. Keep route validation in FastAPI, runtime wiring in `task_dashboard_view_model.py`, presentation logic in `dashboard.js`, and regression coverage in the existing Python and Node test suites.

## Phase 0 - Research Summary

Research outcomes in `specs/047-temporal-artifact-presentation/research.md` establish:

1. `/tasks/:taskId` remains the canonical Temporal detail route; `taskId == workflowId` is the compatibility bridge.
2. Temporal detail must fetch execution detail first, then fetch artifacts using the latest `temporalRunId` from detail rather than stale row metadata.
3. Artifact presentation should normalize preview/default-read/raw-access metadata in the dashboard runtime instead of assuming inline-safe raw content.
4. The default Temporal detail view should remain summary/timeline plus artifacts, not raw Temporal history JSON.
5. Task-oriented controls and submit/redirect behavior should remain governed by runtime config and task-facing labels.
6. Runtime mode is the governing completion criterion; docs-mode behavior is documented only to keep scope checks consistent.

## Phase 1 - Design Outputs

- `research.md`: decisions for route identity, fetch ordering, artifact presentation, action/copy posture, and mode alignment.
- `data-model.md`: Temporal task detail, latest-run scope, artifact presentation entry, access policy, and task action surface entities.
- `contracts/temporal-artifact-presentation-contract.md`: runtime contract for route resolution, detail fetch order, artifact rendering, access actions, and task-oriented action behavior.
- `contracts/requirements-traceability.md`: complete `DOC-REQ-*` mapping to FRs, planned implementation surfaces, and validation strategy.
- `quickstart.md`: deterministic runtime validation steps and mode-aware scope-check guidance.

## Implementation Strategy

### 1. Route and source resolution

- Keep `/tasks/:taskId` as the canonical detail route for Temporal-backed work.
- Ensure route validation in `api_service/api/routers/task_dashboard.py` accepts safe Temporal workflow IDs without introducing a separate primary namespace.
- Preserve `taskId == workflowId` semantics so reruns/Continue-As-New keep the canonical route stable.

### 2. Temporal runtime config and feature posture

- Use `api_service/api/routers/task_dashboard_view_model.py` as the authoritative source for Temporal detail, action, and artifact endpoint templates.
- Preserve the task-oriented feature-flag posture:
  - detail enabled for Temporal reads,
  - action/submit/debug exposure only when explicitly supported.
- Keep Temporal out of worker runtime selection.

### 3. Detail fetch order and latest-run scoping

- In `api_service/static/task_dashboard/dashboard.js`, resolve Temporal detail by:
  1. fetching `/api/executions/{workflowId}`,
  2. extracting the latest `namespace`, `workflowId`, and `temporalRunId`,
  3. fetching `/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`.
- Prevent mixed-run default artifact views by treating the latest run from the detail response as the only default artifact scope.

### 4. Artifact presentation and access policy behavior

- Normalize artifact display from linkage and access metadata:
  - preferred label from execution links,
  - preview-first action when `preview_artifact_ref` exists,
  - raw download only when `raw_access_allowed` permits it,
  - no unsafe inline-raw assumption.
- Use MoonMind-controlled access flows (`presign-download` or authorized download endpoint) for preview/download actions.
- Treat artifact edits as new immutable references rather than in-place mutation behavior.

### 5. Task-oriented detail experience

- Keep the primary detail view task-oriented:
  - synthesized summary and timeline,
  - normalized waiting context,
  - advanced Temporal metadata only as secondary fields.
- Avoid exposing raw Temporal history JSON as the default UX.
- Map allowed controls onto Temporal update/signal/cancel endpoints while retaining task-facing copy.

### 6. Validation strategy

- Use Python unit tests to guard:
  - dashboard route acceptance for Temporal workflow IDs,
  - runtime config exposure of Temporal endpoints and feature flags.
- Use Node dashboard runtime tests to guard:
  - latest-run artifact request resolution,
  - preview-first and raw-restricted artifact presentation,
  - route/run-scope regressions in Temporal detail helpers.
- Run repository-standard acceptance with `./tools/test_unit.sh`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- This feature must ship:
  - production runtime code changes in dashboard/router/runtime surfaces, and
  - automated validation tests in the repository test suites.
- Docs mode remains explicitly documented for consistency only:
  - `./.specify/scripts/bash/validate-implementation-scope.sh --mode docs` may skip runtime scope enforcement,
  - but this feature is not eligible for docs-only completion.

## Remediation Gates

- Every `DOC-REQ-*` row must map to at least one FR, one planned implementation surface, and one explicit validation strategy in `contracts/requirements-traceability.md`.
- Downstream `tasks.md` must keep runtime implementation tasks and validation tasks; docs-only task sets are invalid for this feature.
- Planned implementation ownership must match the active runtime files that carry artifact policy metadata and access behavior, including `api_service/api/routers/temporal_artifacts.py` and `moonmind/schemas/temporal_artifact_models.py`.
- Validation coverage must explicitly include dashboard/runtime tests, artifact API tests, and `tests/unit/specs/test_doc_req_traceability.py` so requirement mappings remain enforced as code changes land.
- Cross-artifact language for runtime scope, latest-run scoping, and artifact access-policy handling must remain consistent across `spec.md`, `plan.md`, `tasks.md`, and the contract docs.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
