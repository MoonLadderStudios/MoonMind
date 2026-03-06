# Implementation Plan: Integrations Monitoring Design

**Branch**: `047-integrations-monitoring` | **Date**: 2026-03-06 | **Spec**: `specs/047-integrations-monitoring/spec.md`  
**Input**: Feature specification from `/specs/047-integrations-monitoring/spec.md`

## Summary

Implement provider-neutral integrations monitoring as production Temporal runtime behavior inside `MoonMind.Run`, extending the current execution lifecycle state machine with durable correlation storage, callback ingestion, polling fallback, bounded Continue-As-New behavior, artifact-backed results, and a first Jules provider profile. The selected orchestration mode is **runtime**; this plan keeps runtime-vs-docs behavior aligned by treating docs-only output as a failing outcome for this feature.

## Technical Context

**Language/Version**: Python 3.11 service code, Alembic migrations, OpenAPI/Markdown planning artifacts  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, Temporal execution lifecycle service, Temporal artifact service, Jules HTTP adapter/client  
**Storage**: PostgreSQL tables `temporal_executions` and `temporal_integration_correlations`, plus Temporal artifact storage (`local_fs` or S3-compatible backend under the existing Temporal artifact service)  
**Testing**: `./tools/test_unit.sh` (required), contract coverage in `tests/contract/test_temporal_execution_api.py`, and Temporal integration/failure-path coverage under `tests/integration/temporal/`  
**Target Platform**: Docker Compose MoonMind stack (`api`, `api-db`, `temporal-db`, `temporal`, `temporal-namespace-init`, `minio`, `rabbitmq`, `celery-worker`)  
**Project Type**: Multi-service backend runtime feature with Temporal lifecycle APIs  
**Performance Goals**: Single terminal completion path under callback/poll races, bounded workflow history through compact monitoring state and Continue-As-New, and provider polling that respects configured/provider guidance  
**Constraints**: Runtime implementation mode is mandatory; no docs-only completion. Preserve provider-neutral contracts, deterministic workflow code, compact memo/search attributes, redaction boundaries, and minimal shared worker topology (`mm.activity.integrations`) until isolation is justified.  
**Scale/Scope**: Initial delivery supports one active monitored external operation per execution, with Jules as the first provider profile and automated coverage for callback, polling, cancellation, Continue-As-New, and failure paths

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. Uses the existing Docker Compose stack and current local Temporal/MinIO services; no new mandatory external cloud dependency is introduced.
- **II. Avoid Vendor Lock-In**: PASS. Provider behavior stays behind provider-neutral activity contracts and reuses the existing Jules adapter as the first profile rather than baking Jules-specific workflow types into core lifecycle code.
- **III. Own Your Data**: PASS. Correlation records, execution state, and artifacts remain in operator-controlled Postgres plus existing artifact storage.
- **IV. Skills Are First-Class and Easy to Add**: PASS. This feature does not alter skill contracts and keeps runtime/provider behavior independent from spec workflow skills.
- **V. Design for Evolution / Scientific Method Loop**: PASS. Runtime contracts, traceability, and explicit tests anchor behavior so provider implementations remain replaceable.
- **VI. Powerful Runtime Configurability**: PASS. Polling thresholds, routing, retries, artifact backend, callback security, and provider settings stay config-driven.
- **VII. Modular and Extensible Architecture**: PASS. Changes stay within existing Temporal router, schema, service, adapter, artifact, and migration boundaries.
- **VIII. Self-Healing by Default**: PASS. Retry-safe correlation keys, callback dedupe, polling fallback, and Continue-As-New preservation are first-order design goals.
- **IX. Facilitate Continuous Improvement**: PASS. Monitoring state, error summaries, and result artifacts remain structured and observable for operator review.
- **X. Spec-Driven Development**: PASS. `DOC-REQ-001` through `DOC-REQ-016` are carried through plan, research, data model, contract, and traceability outputs.

### Post-Design Re-Check

- PASS. Phase 1 artifacts preserve runtime-first scope and explicit validation coverage.
- PASS. Runtime-vs-docs mode handling is explicitly gated and aligned to the selected runtime mode.
- PASS. No constitutional violations require complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/047-integrations-monitoring/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── integrations-monitoring.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── executions.py
│   └── execution_integrations.py
├── db/models.py
├── main.py
└── migrations/versions/
    └── 202603060001_integrations_monitoring.py

moonmind/
├── config/
│   ├── jules_settings.py
│   └── settings.py
├── schemas/
│   ├── jules_models.py
│   └── temporal_models.py
└── workflows/
    ├── adapters/jules_client.py
    └── temporal/
        ├── artifacts.py
        └── service.py

tests/
├── contract/test_temporal_execution_api.py
├── unit/workflows/temporal/test_temporal_service.py
├── unit/workflows/adapters/test_jules_client.py
└── integration/temporal/
    ├── test_compose_foundation.py
    └── test_integrations_monitoring.py          # planned
```

**Structure Decision**: Extend the existing Temporal lifecycle API and state-machine implementation in place. Keep provider contracts, callback ingress, artifact handling, and Jules-specific normalization behind current module boundaries instead of introducing a parallel integration service or provider-specific workflow roots.

## Phase 0 - Research Summary

`research.md` resolves the plan decisions and records these selected directions:

1. Keep `MoonMind.Run` as the default orchestration anchor and extend the existing `TemporalExecutionService` projection/state machine.
2. Treat provider operations as provider-neutral activity contracts routed through a shared integrations worker queue, not a product-level queue or provider workflow type.
3. Reuse the existing Jules adapter/client as the first provider profile and normalize Jules statuses into the shared status set.
4. Use durable callback correlation records keyed by stable correlation/callback material, not visibility scans by external operation ID.
5. Default to callback-plus-polling hybrid monitoring with a terminal latch and bounded provider event dedupe state.
6. Keep monitoring state compact in workflow history and move raw callbacks, detailed provider snapshots, outputs, and failure diagnostics into artifacts.
7. Continue-As-New must preserve `workflow_id`, `correlation_id`, active integration state, and callback routing while refreshing `run_id`.
8. Provider cancellation semantics remain explicit and best-effort, with unsupported/ambiguous results surfaced to operators rather than masked as success.
9. Callback verification, request bounds, and artifact redaction stay in the API/artifact layers so workflow code remains deterministic.
10. Runtime mode is the selected orchestration mode; docs mode is documented only for scope-check behavior and is not a valid completion path.

## Phase 1 - Design Outputs

- **Research decisions**: `research.md` captures provider contract, correlation, polling, Continue-As-New, cancellation, Jules, and mode-alignment decisions.
- **Data model**: `data-model.md` defines `TemporalExecutionRecord` monitoring projection, `ExternalOperationState`, `CorrelationRecord`, `ExternalEventPayload`, `PollingPolicy`, `IntegrationVisibilitySnapshot`, `ProviderFailureSummary`, and `ProviderProfile`.
- **API/runtime contract**: `contracts/integrations-monitoring.openapi.yaml` defines the execution-monitoring API surfaces plus provider activity payload schemas.
- **Traceability matrix**: `contracts/requirements-traceability.md` maps `DOC-REQ-001` through `DOC-REQ-016` to FRs, implementation surfaces, and validation strategy.
- **Execution guide**: `quickstart.md` provides a deterministic runtime validation path and documents runtime-vs-docs mode behavior.

## Implementation Strategy

### 1. Provider-neutral monitoring contract first

- Finalize the normalized integration status vocabulary and compact workflow-side state contract in `moonmind/schemas/temporal_models.py`.
- Keep provider activity naming aligned to `integration.<provider>.start|status|fetch_result|cancel`.
- Route provider-specific I/O through existing adapter seams (`moonmind/workflows/adapters/jules_client.py` first) instead of branching workflow types by provider.

### 2. Durable correlation and secure callback ingress

- Persist durable callback correlation rows in `temporal_integration_correlations` and keep them synchronized with execution lifecycle changes and Continue-As-New.
- Keep callback verification, request-size enforcement, and optional raw payload artifact capture in the API layer before workflow state changes occur.
- Use callback correlation lookup as the default resolution path; do not depend on search-attribute scans of provider IDs.

### 3. Workflow-side monitoring state, polling, and Continue-As-New

- Extend `TemporalExecutionService` to own the compact monitoring state, terminal latch behavior, bounded provider event dedupe, and search-attribute/memo updates.
- Add polling policy behavior that honors provider recommendations when present, otherwise starts conservatively, backs off with jitter, and resets on meaningful status transitions.
- Keep durable polling workflow-owned; any API poll-recording endpoint exists only as an internal reconciliation/test harness surface for deterministic validation, not as a replacement for workflow timers.
- Preserve `workflow_id`, `correlation_id`, callback routing, and bounded monitoring state across Continue-As-New while refreshing `run_id`.

### 4. Artifact-backed results, summaries, and security boundaries

- Keep raw callback bodies, detailed provider responses, fetched outputs, and failure diagnostics in Temporal artifacts rather than memo/history.
- Restrict memo/search attributes to safe, compact, human-readable fields such as summary, state, provider name, and optional safe URLs.
- Reuse artifact retention/redaction controls so secrets and high-variance payloads never leak into workflow memo/history or previews.

### 5. Jules-first provider profile without semantic drift

- Map Jules task statuses to the normalized provider-neutral status set and preserve raw Jules status for diagnostics.
- Reuse the existing Jules client retry behavior where it matches the new provider contract; fail fast on ambiguous or unsupported semantics rather than silently translating behavior.
- Keep runtime/docs mode handling aligned: the Jules provider work is a runtime deliverable with tests, not a documentation-only placeholder.

### 6. Validation and release gates

- Required unit command: `./tools/test_unit.sh`.
- Required contract command: `.venv/bin/python -m pytest tests/contract/test_temporal_execution_api.py` (fallback to `python -m pytest ...` or `python3 -m pytest ...` when `.venv/bin/python` is unavailable).
- Required Temporal integration command: `.venv/bin/python -m pytest tests/integration/temporal/test_integrations_monitoring.py` (same Python fallback rules) after the local Temporal stack from `quickstart.md` is running.
- Required runtime scope gates on MoonMind task branches:
  - `SPECIFY_FEATURE=047-integrations-monitoring ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
  - `SPECIFY_FEATURE=047-integrations-monitoring ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`
- Expand contract, unit, and Temporal integration coverage for configure-monitoring, callback ingestion, poll reconciliation, dedupe, callback rate limiting, artifact grant/retention controls, cancellation, Continue-As-New behavior, and failure injection.
- Add Temporal integration/failure-path coverage under `tests/integration/temporal/` for callback races, missed callbacks, provider `429/5xx`, ambiguous `start` timeouts, polling fallback, result-fetch/artifact-write retries, and Jules-specific normalization.
- Downstream `tasks.md` must preserve the required implementation order from the source design: provider contract/normalization, correlation storage, callback handling, polling fallback, visibility updates, and provider-specific tests.

## Runtime-vs-Docs Mode Alignment Gate

- Selected orchestration mode for this feature: **runtime implementation mode**.
- Required deliverables include:
  - production runtime code changes in `api_service/`, `moonmind/`, and database migrations, and
  - automated validation tests under `tests/`.
- Docs mode behavior remains documented only for scope-check semantics:
  - `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode docs`
  - `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode docs --base-ref origin/main`
- This feature is not eligible for docs-only completion.

## Risks & Mitigations

- **Risk**: Current lifecycle projection code can diverge from eventual Temporal workflow/activity runtime semantics.
  - **Mitigation**: Keep API schemas, provider activity payloads, and lifecycle tests aligned so the projection layer stays a faithful contract, not a separate behavior model.
- **Risk**: Callback and poll races can still double-complete or corrupt state if terminal-latch rules are incomplete.
  - **Mitigation**: Make terminal status sticky, cap dedupe state, and add explicit race/failure-path tests before rollout.
- **Risk**: Provider-specific behavior leaks into core workflow semantics.
  - **Mitigation**: Centralize normalization in provider profile logic and keep core state machine/provider contracts vendor-neutral.
- **Risk**: Large callback/result payloads leak into memo, search attributes, or workflow history.
  - **Mitigation**: Treat artifact-backed storage and compact `ExternalEvent` payloads as hard gates in code review and tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| _None_ | — | — |
