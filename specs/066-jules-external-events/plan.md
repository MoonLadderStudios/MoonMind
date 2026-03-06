# Implementation Plan: Jules Temporal External Events

**Branch**: `048-jules-external-events` | **Date**: 2026-03-06 | **Spec**: `specs/048-jules-external-events/spec.md`  
**Input**: Feature specification from `/specs/048-jules-external-events/spec.md`

## Summary

Complete the Jules Temporal external-event profile as production runtime code across shared Jules helpers, Temporal integration activities, compatibility API/runtime surfaces, and automated validation. The selected orchestration mode is **runtime** rather than docs-only, so this plan keeps runtime-vs-docs behavior aligned by treating docs/spec updates without code and test coverage as a failing outcome.

## Technical Context

**Language/Version**: Python 3.11 service and worker code  
**Primary Dependencies**: FastAPI, Pydantic v2, Temporal activity/runtime modules, `httpx`, SQLAlchemy-backed Temporal artifact service, existing Jules adapter and worker runtime code  
**Storage**: Temporal artifact storage via `moonmind/workflows/temporal/artifacts.py` plus existing compact workflow/API state; no new primary datastore required  
**Testing**: `./tools/test_unit.sh` (required), plus runtime-scope validation via `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` during implementation  
**Target Platform**: Docker Compose MoonMind stack with API, worker, and Temporal worker fleets  
**Project Type**: Multi-service backend/runtime feature with API compatibility and dashboard/runtime-config touch points  
**Performance Goals**: Keep workflow-visible Jules activity payloads compact, artifact large provider snapshots, and preserve bounded polling/backoff behavior without duplicate completion  
**Constraints**: Reuse the existing Jules runtime gate; do not add a Temporal-only enablement flag; keep `mm.activity.integrations` as the default queue; keep `callback_supported=false` until a verified callback ingress exists; preserve secret scrubbing and truthful cancellation reporting; keep runtime-vs-docs behavior aligned with runtime mode  
**Scale/Scope**: Jules-specific monitoring semantics across shared helper code, Temporal activity runtime, queue/API compatibility surfaces, worker runtime reuse, and unit/contract validation

## Constitution Check

### Pre-Phase 0 Gate

- **I. One-Click Agent Deployment**: **PASS**. Uses the existing Compose topology and current Jules env/config gates; no new mandatory external dependency is introduced.
- **II. Avoid Vendor Lock-In**: **PASS**. Jules specifics remain behind adapter and normalization modules, while generic Temporal orchestration contracts stay provider-neutral.
- **III. Own Your Data**: **PASS**. Large provider snapshots and callback bodies remain artifact-backed rather than embedded in opaque provider storage or workflow history.
- **IV. Skills Are First-Class and Easy to Add**: **PASS**. This feature does not change skill registration semantics and preserves runtime-neutral orchestration above provider-specific adapters.
- **V. The Bittersweet Lesson**: **PASS**. Design keeps scaffolding thin by extending existing adapters, activity bindings, and tests instead of creating parallel runtime paths.
- **VI. Powerful Runtime Configurability**: **PASS**. Jules behavior remains driven by current env/config settings and shared runtime-gate helpers.
- **VII. Modular and Extensible Architecture**: **PASS**. Changes stay within existing module boundaries: `moonmind/jules/`, Temporal activity runtime/catalog, API compatibility routers, and worker runtime reuse.
- **VIII. Self-Healing by Default**: **PASS**. Idempotent starts, bounded polling, artifact-backed snapshots, and truthful cancellation/failure handling are explicit design requirements.
- **IX. Facilitate Continuous Improvement**: **PASS**. Operator-visible summaries, failure artifacts, and structured validation stay part of the planned delivery.
- **X. Spec-Driven Development Is the Source of Truth**: **PASS**. This plan, its Phase 0/1 artifacts, and later tasks map directly to the spec's FRs and `DOC-REQ-*` requirements.

### Post-Phase 1 Re-Check

- All ten constitutional principles remain **PASS** after producing `research.md`, `data-model.md`, `quickstart.md`, and `contracts/*`.
- No constitutional violation requires a complexity exception.

## Project Structure

### Documentation (this feature)

```text
specs/048-jules-external-events/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── jules-temporal-activity-contract.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/routers/
    ├── agent_queue.py
    ├── mcp_tools.py
    └── task_dashboard_view_model.py

moonmind/
├── agents/codex_worker/worker.py
├── agents/codex_worker/cli.py
├── config/
│   ├── jules_settings.py
│   └── settings.py
├── jules/
│   ├── runtime.py
│   └── status.py
├── mcp/jules_tool_registry.py
├── schemas/jules_models.py
├── workflows/adapters/jules_client.py
└── workflows/temporal/
    ├── activity_catalog.py
    ├── activity_runtime.py
    ├── artifacts.py
    └── workers.py

tests/
├── contract/test_temporal_activity_topology.py
├── unit/api/routers/
│   ├── test_agent_queue.py
│   ├── test_mcp_tools.py
│   └── test_task_dashboard_view_model.py
├── unit/agents/codex_worker/
│   ├── test_cli.py
│   └── test_worker.py
├── unit/jules/
│   ├── test_jules_runtime.py
│   └── test_status.py
├── unit/mcp/
│   └── test_jules_tool_registry.py
├── unit/specs/
│   └── test_doc_req_traceability_048.py
└── unit/workflows/
    ├── adapters/test_jules_client.py
    └── temporal/test_activity_runtime.py
```

**Structure Decision**: Implement within the existing Jules helper modules, Temporal activity runtime, API compatibility surfaces, and worker/runtime tests. Do not introduce a new queue family, separate provider service, or duplicate Jules gate/normalization logic.

## Phase 0 - Research Summary

`research.md` resolves the design questions for this feature and locks these directions:

1. Reuse the shared Jules runtime gate across Temporal, API, tooling, and worker paths.
2. Keep one shared Jules status normalizer for both legacy polling and Temporal code.
3. Treat current `integration.jules.*` activities as the canonical implementation seam and extend them rather than replacing them.
4. Embed only non-secret correlation hints in Jules metadata while MoonMind remains the durable source of truth.
5. Keep `fetch_result` conservative: terminal snapshot + summary artifacts, not speculative rich provider downloads.
6. Leave provider-side cancellation explicitly unsupported until a real Jules cancel API exists.
7. Keep callback architecture future-ready, but default `callback_supported=false` until ingress/auth/dedupe are real.
8. Preserve MoonMind workflow identity as primary in API/UI compatibility rows and expose Jules `taskId` separately.
9. Treat runtime-mode scope as a hard delivery gate: runtime code plus automated tests are mandatory.

## Phase 1 - Design Outputs

- **Data model**: `data-model.md` defines the bounded runtime entities for Jules correlation, status snapshots, artifact sets, cancellation outcomes, and compatibility views.
- **Contract**: `contracts/jules-temporal-activity-contract.md` defines the planned activity semantics for `integration.jules.start`, `integration.jules.status`, `integration.jules.fetch_result`, and the reserved `integration.jules.cancel`, plus future `ExternalEvent` callback expectations.
- **Traceability matrix**: `contracts/requirements-traceability.md` maps every `DOC-REQ-001` through `DOC-REQ-017` to planned runtime implementation surfaces and validation strategy.
- **Execution guide**: `quickstart.md` provides the implementation/verification path and repository-standard validation commands.

## Implementation Strategy

### 1. Consolidate shared Jules semantics

- Keep `moonmind/jules/runtime.py` as the canonical runtime gate and ensure Temporal/API/tooling paths all reuse it.
- Keep `moonmind/jules/status.py` as the shared status normalizer used by both Temporal and legacy worker polling.
- Preserve existing adapter transport semantics in `moonmind/workflows/adapters/jules_client.py`: bearer auth, retry-on-`5xx`/`429`/timeouts, fail-fast on other `4xx`, and scrubbed exception text.

### 2. Complete the Temporal Jules activity contract

- Extend `TemporalJulesActivities` in `moonmind/workflows/temporal/activity_runtime.py` to fully satisfy the semantic contract around `external_operation_id`, `provider_status`, `normalized_status`, `external_url`, and artifact-backed result snapshots.
- Keep activity names on `mm.activity.integrations` via `moonmind/workflows/temporal/activity_catalog.py` and `moonmind/workflows/temporal/workers.py`.
- Keep `integration.jules.cancel` reserved and explicitly unsupported until the provider exposes a real cancel API.

### 3. Preserve hybrid-repo migration behavior

- Keep the existing non-Temporal Jules adapter, MCP tooling, and software-polling worker paths valid during migration.
- Reuse the shared normalizer and runtime gate so Temporal and legacy behavior cannot drift semantically.
- Keep callback architecture future-facing, but do not claim callback implementation until ingress/auth/dedupe support actually exists.

### 4. Align compatibility and operator-facing surfaces

- Preserve early rejection of invalid `targetRuntime=jules` requests in API and worker entry points.
- Keep Jules MCP tools hidden when Jules is disabled or incompletely configured.
- Ensure compatibility responses and runtime metadata distinguish MoonMind workflow/task identity from Jules provider identity (`taskId`).

### 5. Artifact and result materialization discipline

- Persist start/status/final snapshots through the Temporal artifact service when available.
- Require terminal snapshot artifacts and failure-summary artifacts for failed/canceled/unsupported-cancel outcomes.
- Keep large provider payloads and any future raw callback bodies out of workflow-visible inline payloads.

### 6. Validation strategy

- Extend unit coverage in:
  - `tests/unit/jules/test_status.py`
  - `tests/unit/jules/test_jules_runtime.py`
  - `tests/unit/workflows/adapters/test_jules_client.py`
  - `tests/unit/workflows/temporal/test_activity_runtime.py`
  - `tests/unit/api/routers/test_agent_queue.py`
  - `tests/unit/api/routers/test_mcp_tools.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
  - `tests/unit/agents/codex_worker/test_cli.py`
  - `tests/unit/agents/codex_worker/test_worker.py`
  - `tests/unit/mcp/test_jules_tool_registry.py`
  - `tests/unit/specs/test_doc_req_traceability_048.py`
- Keep contract coverage in `tests/contract/test_temporal_activity_topology.py` aligned with the activity queue/catalog contract.
- Run the repository-standard validation command `./tools/test_unit.sh`.

## Runtime vs Docs Mode Alignment

- Selected mode: **runtime**.
- Required deliverables: production runtime code changes plus automated validation tests.
- Rejected completion mode: docs-only/spec-only updates with no runtime/test implementation evidence.

## Risks & Mitigations

- **Risk**: Temporal and legacy worker paths drift on status meaning or gate behavior.  
  **Mitigation**: Reuse `moonmind/jules/runtime.py` and `moonmind/jules/status.py` as shared primitives and add regression tests in both Temporal and worker suites.
- **Risk**: `fetch_result` overpromises provider outputs that Jules does not expose.  
  **Mitigation**: Keep the contract conservative around terminal task snapshots and MoonMind-authored summaries only.
- **Risk**: Future callback references are mistaken for implemented behavior.  
  **Mitigation**: Keep callback support contract-only, default `callback_supported=false`, and treat `integration.jules.cancel` as reserved/unsupported.
- **Risk**: Compatibility layers expose Jules `taskId` as the durable MoonMind execution identifier.  
  **Mitigation**: Explicitly model workflow/task identity separately in API/runtime metadata and add regression tests.
- **Risk**: Secrets leak through artifacts or exception text.  
  **Mitigation**: Preserve scrubbed adapter errors, artifact restrictions, and validation coverage around secret hygiene.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| _None_ | - | - |
