# Implementation Plan: Temporal Visibility Query Model

**Branch**: `047-temporal-visibility-query` | **Date**: 2026-03-06 | **Spec**: `specs/047-temporal-visibility-query/spec.md`
**Input**: Feature specification from `/specs/047-temporal-visibility-query/spec.md`

## Summary

Implement the Visibility-backed query contract from `docs/Temporal/VisibilityAndUiQueryModel.md` as runtime-authoritative behavior across the Temporal execution service, execution API serializers, projection/migration helpers, and existing task-oriented compatibility surfaces. The delivery fixes canonical identifiers, Search Attribute and Memo invariants, exact filter scope, ordering, pagination/count semantics, compatibility status/wait metadata, and the selected compatibility-adapter UI path, with automated validation tests as a hard completion gate.

## Technical Context

**Language/Version**: Python 3.11, JavaScript for the existing server-hosted dashboard  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing MoonMind Temporal service stack, current task dashboard runtime config/view-model layer  
**Storage**: Temporal Visibility as semantic source of truth; PostgreSQL `temporal_executions` projection as cache/reconciliation layer; Memo/Search Attribute payloads stored on the execution projection for adapter parity  
**Testing**: `./tools/test_unit.sh`; contract coverage in `tests/contract/test_temporal_execution_api.py`; unit coverage in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/specs/test_doc_req_traceability.py`; targeted contract runs for execution API behavior that sits outside the repository unit-test entrypoint  
**Target Platform**: Linux containerized MoonMind API/dashboard runtime with Temporal-backed orchestration  
**Project Type**: Backend runtime + compatibility-adapter hardening + persistence/query contract enforcement  
**Performance Goals**: Deterministic list ordering (`mm_updated_at DESC`, `workflowId DESC`), bounded exact-match filtering, opaque scope-bound pagination tokens, and payloads that keep compatibility list rows small  
**Constraints**: Preserve Temporal-first query semantics even when projections remain; keep `workflowId` as the durable handle; do not add `temporal` as a worker runtime; keep Search Attributes/Memo bounded and display-safe; keep runtime-vs-docs completion behavior aligned to runtime implementation mode  
**Scale/Scope**: Changes span `moonmind/workflows/temporal`, `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py`, projection migration/helpers, and validation tests for API and service compatibility behavior

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. The feature stays inside the existing API/dashboard services and does not add new operator prerequisites beyond the current Docker Compose stack.
- **II. Avoid Vendor Lock-In**: PASS. The product contract is exposed through MoonMind REST adapters and normalized models rather than direct browser dependency on Temporal Server or Temporal Web.
- **III. Own Your Data**: PASS. Canonical Search Attributes, Memo fields, and projection mirrors remain inspectable and portable.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Query semantics do not alter skill runtime contracts; they only normalize how Temporal-backed executions are surfaced.
- **V. The Bittersweet Lesson / Design for Replaceability**: PASS. Temporal query semantics live in explicit service/schema/router boundaries, so projection or later UI migration logic remains replaceable.
- **VI. Powerful Runtime Configurability**: PASS. The feature preserves existing runtime boundaries and keeps Temporal out of worker runtime selection.
- **VII. Modular and Extensible Architecture**: PASS. The implementation is localized to temporal query surfaces, projection helpers, and test contracts.
- **VIII. Self-Healing by Default**: PASS. Drift repair and stale-row refresh behavior are explicit, retry-safe reconciliation concerns.
- **IX. Facilitate Continuous Improvement**: PASS. Canonical list/detail fields and stale-state indicators improve operator diagnosis and follow-up actions.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-001` through `DOC-REQ-018` remain mapped to implementation surfaces and validation in dedicated traceability artifacts.

### Post-Design Re-Check

- PASS. Phase 1 artifacts keep Temporal Visibility as the semantic owner of list/query/count behavior.
- PASS. The chosen UI path keeps `/tasks/*` task-oriented through compatibility adapters without inventing a second runtime model.
- PASS. Runtime implementation mode remains authoritative: docs/spec artifacts support the feature, but completion still requires production code changes plus automated validation tests.

## Project Structure

### Documentation (this feature)

```text
specs/047-temporal-visibility-query/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── temporal-visibility-query.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
docs/Temporal/
├── VisibilityAndUiQueryModel.md
└── TemporalDashboardIntegration.md

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        └── service.py

api_service/
├── api/routers/
│   └── executions.py
├── db/
│   └── models.py
├── migrations/versions/
│   └── 202603060001_temporal_visibility_query_contract.py

tests/
├── contract/
│   └── test_temporal_execution_api.py
└── unit/
    ├── specs/
    │   └── test_doc_req_traceability.py
    └── workflows/temporal/
        └── test_temporal_service.py
```

**Structure Decision**: Implement the contract in the existing Temporal execution service and API surfaces while preserving task compatibility through canonical identifiers and top-level adapter fields. Projection persistence remains an adapter/cache layer and must mirror Temporal-backed truth rather than redefine semantics.

## Phase 0 - Research Summary

Research outcomes in `specs/047-temporal-visibility-query/research.md` establish:

1. `workflowId` remains the single durable handle; Temporal-backed compatibility rows use `taskId == workflowId`, and `runId` stays detail/debug metadata only.
2. Required v1 Search Attributes are exactly `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, and `mm_entry`; deferred fields stay out until the governing doc changes.
3. Canonical list/detail payloads expose exact lifecycle state, Temporal close status, compatibility `dashboardStatus`, and bounded wait metadata without relying on raw projection-specific fields.
4. The list contract supports exact filters for `workflowType`, `ownerType`, `ownerId`, `state`, `entry`, plus bounded `repo` and `integration` only if those fields are present and intentionally introduced.
5. Pagination stays opaque and scope-bound; pure Temporal responses may return `countMode="exact"` while mixed-source dashboards must not treat Temporal page tokens as universal cursors.
6. The selected UI path for this delivery is the existing compatibility-adapter contract, not a first-class `temporal` dashboard source and not a new worker runtime.

## Phase 1 - Design Outputs

- **Data Model**: `data-model.md` defines the canonical execution query row, Search Attribute/Memo envelopes, compatibility identifier bridge, pagination contract, and projection drift-repair responsibilities.
- **API Contract**: `contracts/temporal-visibility-query.openapi.yaml` defines the Temporal-backed list/detail/update/signal/cancel contract and the canonical response shape used by adapters/UI consumers.
- **Traceability**: `contracts/requirements-traceability.md` maps every `DOC-REQ-*` to functional requirements, planned implementation surfaces, and validation coverage.
- **Execution Guide**: `quickstart.md` defines the runtime-mode verification flow using the repository-standard unit test entrypoint.

## Implementation Strategy

### 1. Canonical Visibility and projection invariants

- Harden `TemporalExecutionRecord` and related migrations/helpers so required v1 Search Attributes and owner metadata are always populated together and remain bounded.
- Keep `mm_updated_at` tied to meaningful user-visible mutations only; prevent telemetry-only churn from changing default recency ordering.
- Treat projection rows as mirrors of canonical Temporal-backed fields and add explicit repair/reconciliation behavior when drift is detected.

### 2. Query filters, ordering, pagination, and counts

- Keep `TemporalExecutionService.list_executions()` as the adapter point for canonical exact-match filtering, deterministic ordering, and opaque page-token scope validation.
- Add any missing bounded filters (`ownerType`, `entry`, and optionally `repo`/`integration` when intentionally surfaced) without introducing free-text, fuzzy, OR, date-range, child, or activity filters.
- Preserve exact counts for pure Temporal execution queries and design multi-source task pages around per-source cursors or an owning aggregator contract instead of reusing Temporal tokens directly.

### 3. Canonical payloads, status mapping, and identifier bridge

- Keep execution serializers and schemas centered on top-level canonical fields (`workflowId`, `taskId`, `state`, `temporalStatus`, `closeStatus`, `dashboardStatus`, owner metadata, timestamps).
- Preserve exact lifecycle state while deriving compatibility `dashboardStatus` from the fixed v1 mapping.
- Enforce `waitingReason` and `attentionRequired` semantics for `awaiting_external`, including the rule that `attentionRequired = false` must not imply user action is needed.

### 4. Compatibility integration path

- Keep `/api/executions` and task-oriented identifier semantics as the supported UI integration path for this delivery.
- Preserve `taskId == workflowId`, exact status fields, wait metadata, and list/detail payload rules so existing task-oriented consumers do not invent conflicting semantics.
- Keep `temporal` out of worker runtime selection; it is an orchestration substrate, not a task runtime.

### 5. Compatibility migration and stale-state handling

- Preserve task-oriented compatibility surfaces while preventing them from inventing different identifiers, sort rules, or pagination semantics for Temporal-backed rows.
- Deliver acted-on row patching from successful action responses plus background-refetch and degraded-read semantics for the active compatibility query.
- Expose stale-state and degraded-count indicators through the compatibility-adapter contract and current operator-facing task surfaces; only a larger first-class `temporal` dashboard-source redesign remains follow-up work.

### 6. Validation strategy

- Extend contract tests for list/detail payload invariants, supported filters, token invalidation, count semantics, and authorization rules.
- Extend unit tests for Temporal service state transitions, `mm_updated_at` update behavior, deferred-field rejection, and compatibility status/wait metadata.
- Extend contract and unit tests so compatibility-adapter semantics, identifier acceptance, and traceability coverage remain enforced.
- Keep repository-standard validation anchored on `./tools/test_unit.sh`; integration orchestration checks remain optional follow-up validation outside this planning step.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion requires production runtime code changes in the execution service/API surfaces plus automated validation tests.
- Planning and source-doc artifacts are support material only; a docs-only diff does not satisfy this feature.
- Any later tasks generated from this plan must keep runtime deliverables and test coverage explicit so MoonMind runtime mode and docs mode do not diverge.

## Remediation Gates

- Planning is invalid if any `DOC-REQ-*` row is missing from `contracts/requirements-traceability.md`.
- Planning is invalid if any mapped requirement lacks both a planned implementation surface and a planned validation strategy.
- Implementation must preserve Temporal-backed truth for query semantics even when projections or task compatibility layers remain in place.
- The chosen UI path must remain deterministic across spec, plan, tasks, and traceability artifacts: compatibility adapters now, first-class dashboard source later if separately scoped.

## Risks & Mitigations

- **Risk: projection behavior drifts from canonical Visibility semantics during migration.**
  - **Mitigation**: centralize canonical filtering/ordering/serialization in Temporal service/router helpers and add drift-repair tests.
- **Risk: compatibility consumers may still need explicit action-row patching or stale-state affordances during mixed-source migration.**
  - **Mitigation**: make action-row patching, degraded freshness/count metadata, and operator-facing stale indicators explicit deliverables in the compatibility-adapter slice, deferring only a broader dashboard redesign.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
