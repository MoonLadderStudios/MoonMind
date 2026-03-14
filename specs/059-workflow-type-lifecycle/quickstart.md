# Quickstart: Workflow Type Catalog and Lifecycle

## 1. Confirm runtime-mode scope

- Read `specs/046-workflow-type-lifecycle/spec.md`.
- Treat this feature as runtime implementation mode: production code + validation tests are required.
- Do not treat docs/spec edits alone as completion.

## 2. Review implementation surfaces

- Runtime/service/model:
  - `moonmind/workflows/temporal/service.py`
  - `moonmind/schemas/temporal_models.py`
  - `api_service/db/models.py`
  - `api_service/migrations/versions/202603050001_temporal_execution_lifecycle.py`
- API surface:
  - `api_service/api/routers/executions.py`
- Validation suites:
  - `tests/unit/workflows/temporal/test_temporal_service.py`
  - `tests/contract/test_temporal_execution_api.py`
  - `tests/integration/temporal/test_compose_foundation.py`

## 3. Validate lifecycle contract flows

1. Create one `MoonMind.Run` execution and one `MoonMind.ManifestIngest` execution.
2. Verify each execution has:
   - `workflowId` format `mm:<id>`
   - required visibility fields (`mm_owner_id`, `mm_state`, `mm_updated_at`)
   - required memo fields (`title`, `summary`)
3. Exercise update contracts:
   - `UpdateInputs`
   - `SetTitle`
   - `RequestRerun` (verify Continue-As-New semantics keep Workflow ID)
4. Exercise signal contracts:
   - `ExternalEvent`
   - `Approve`
   - optional `Pause`/`Resume` behavior if enabled
5. Exercise cancellation paths:
   - graceful cancel -> `canceled`
   - forced termination -> failed semantics with reason in summary

## 4. Verify history-safety and failure taxonomy behavior

- Trigger progress thresholds and confirm Continue-As-New behavior preserves Workflow ID and required references.
- Confirm failed terminal states include one supported error category:
  - `user_error`
  - `integration_error`
  - `execution_error`
  - `system_error`

## 5. Run repository-standard unit tests

```bash
./tools/test_unit.sh
```

Notes:

- This repository requires `./tools/test_unit.sh` for unit acceptance.
- Do not replace with direct `pytest`.
- In WSL, the script delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.

## 6. Optional integration validation path

Use Docker-based orchestrator integration tests when validating full runtime behavior:

```bash
docker compose -f docker-compose.test.yaml run --rm orchestrator-tests
```

## 7. Runtime scope validation gates (implementation stage)

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime scope checks pass with production runtime code and validation task coverage.

## 8. Planning artifact completion gate

Before running `/agentkit.tasks`, ensure these artifacts exist:

- `specs/046-workflow-type-lifecycle/plan.md`
- `specs/046-workflow-type-lifecycle/research.md`
- `specs/046-workflow-type-lifecycle/data-model.md`
- `specs/046-workflow-type-lifecycle/quickstart.md`
- `specs/046-workflow-type-lifecycle/contracts/temporal-workflow-lifecycle.openapi.yaml`
- `specs/046-workflow-type-lifecycle/contracts/requirements-traceability.md`
