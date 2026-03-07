# Requirements Traceability: Executions API Contract Runtime Delivery

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | `docs/Api/ExecutionsApiContract.md` §1, §2.3-2.4 | FR-001, FR-017 | `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `api_service/main.py` | Contract tests verify the six execution lifecycle routes stay under `/api/executions` and preserve execution-oriented response shapes (`tests/contract/test_temporal_execution_api.py`). |
| DOC-REQ-002 | `docs/Api/ExecutionsApiContract.md` §4.2-4.3, §5.1-5.2 | FR-003, FR-017 | `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py`, compatibility adapters in `api_service/static/task_dashboard/dashboard.js` | Router/contract tests verify camelCase responses, canonical `workflowId`, non-durable `runId`, and absence of `taskId` from direct execution responses; compatibility assertions cover `taskId == workflowId` outside this API. |
| DOC-REQ-003 | `docs/Api/ExecutionsApiContract.md` §5, §6 | FR-001, FR-002 | `api_service/api/routers/executions.py`, auth dependency wiring, `moonmind/workflows/temporal/service.py` | Router tests verify auth scope handling, `403 execution_forbidden` for invalid list scope, and non-disclosing `404 execution_not_found` on hidden direct operations (`tests/unit/api/routers/test_executions.py`). |
| DOC-REQ-004 | `docs/Api/ExecutionsApiContract.md` §7 | FR-004 | `api_service/db/models.py`, `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/service.py` | Service and contract tests verify allowed workflow type/state enums and close-status to `temporalStatus` mapping (`tests/unit/workflows/temporal/test_temporal_service.py`, `tests/contract/test_temporal_execution_api.py`). |
| DOC-REQ-005 | `docs/Api/ExecutionsApiContract.md` §8.1, §8.3 | FR-005, FR-010 | `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py` | Contract tests verify `ExecutionModel` and `ExecutionListResponse` fields, `count`, `countMode`, and opaque pagination token handling. |
| DOC-REQ-006 | `docs/Api/ExecutionsApiContract.md` §8.2 | FR-006 | `moonmind/workflows/temporal/service.py`, `api_service/db/models.py`, `api_service/api/routers/executions.py` | Service/contract tests verify baseline search attributes and memo keys are present, remain extensible, and use authenticated owner metadata in create flows. |
| DOC-REQ-007 | `docs/Api/ExecutionsApiContract.md` §9 | FR-007, FR-008 | `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/service.py`, `api_service/api/routers/executions.py` | Service and contract tests verify create validation, idempotency deduplication, initialization state, and `201 Created` responses. |
| DOC-REQ-008 | `docs/Api/ExecutionsApiContract.md` §10 | FR-009, FR-010 | `moonmind/workflows/temporal/service.py`, `api_service/api/routers/executions.py`, `api_service/db/models.py` | Service and contract tests verify list filtering, owner scoping, ordering, cursor semantics, page-size bounds, and count behavior. |
| DOC-REQ-009 | `docs/Api/ExecutionsApiContract.md` §11 | FR-011 | `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py` | Router/contract tests verify describe-by-`workflowId` returns `ExecutionModel` when visible and `execution_not_found` when missing or hidden. |
| DOC-REQ-010 | `docs/Api/ExecutionsApiContract.md` §12 | FR-012, FR-013 | `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py` | Service and router tests verify supported update names, response envelope semantics, narrow update idempotency, terminal behavior, and rerun identity invariants. |
| DOC-REQ-011 | `docs/Api/ExecutionsApiContract.md` §13 | FR-014 | `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py` | Service and router tests verify signal payload requirements, lifecycle effects, `202 Accepted` responses, and `409 signal_rejected` behavior. |
| DOC-REQ-012 | `docs/Api/ExecutionsApiContract.md` §14 | FR-015 | `moonmind/workflows/temporal/service.py`, `api_service/api/routers/executions.py` | Contract and service tests verify graceful vs forced cancel semantics, unchanged terminal returns, and ownership-scoped not-found behavior. |
| DOC-REQ-013 | `docs/Api/ExecutionsApiContract.md` §15 | FR-016 | `api_service/api/routers/executions.py`, `moonmind/schemas/temporal_models.py` | Router tests verify stable `detail.code` / `detail.message` domain error shape while allowing framework validation errors to pass through separately. |
| DOC-REQ-014 | `docs/Api/ExecutionsApiContract.md` §16, §19 | FR-017 | `api_service/api/routers/executions.py`, compatibility adapters in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/static/task_dashboard/dashboard.js` | Contract plus compatibility-adapter assertions verify stable execution JSON and explicit `taskId == workflowId` bridge behavior during migration. |
| DOC-REQ-015 | Task objective runtime scope guard | FR-018, FR-019 | Runtime implementation files above plus validation suites under `tests/contract/` and `tests/unit/` | Acceptance requires production runtime code changes, automated validation via `./tools/test_unit.sh`, and a passing `tests/unit/specs/test_doc_req_traceability.py` gate; docs-only completion is invalid. |

## Runtime Mode Alignment Gate

- Selected orchestration mode for this feature is **runtime implementation mode**.
- Traceability is only valid when runtime implementation surfaces and automated validation coverage are both present.
- Docs-only completion paths are non-compliant with `FR-018` and `FR-019`.

## Coverage Gate

- Total source requirement rows: **15** (`DOC-REQ-001` through `DOC-REQ-015`).
- Every source requirement is mapped to FRs, implementation surfaces, and planned validation strategy.
- `tests/unit/specs/test_doc_req_traceability.py` must keep the active feature's `DOC-REQ-*` rows machine-verifiable against this file.
- Planning must fail if any `DOC-REQ-*` entry becomes unmapped or lacks planned validation.

## Validation Evidence

- 2026-03-06: `./tools/test_unit.sh` passed for the repository-standard unit suite, including the execution contract, router, service, and DOC-REQ traceability coverage.
- 2026-03-06: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` passed (`runtime tasks=14, validation tasks=13`).
- 2026-03-06: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` passed (`runtime files=2, test files=3`).
