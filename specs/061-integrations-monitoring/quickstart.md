# Quickstart: Integrations Monitoring Design

## 1. Confirm selected orchestration mode

- This feature is in **runtime implementation mode**.
- Required deliverables include production runtime code changes and automated validation tests.
- Docs-only updates are not sufficient for completion.

Docs-mode note for consistency:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode docs
```

Expected result: docs mode skips runtime scope enforcement, but this feature remains runtime-gated.

## 2. Review implementation surfaces

- Runtime lifecycle and callback ingress:
  - `api_service/api/routers/executions.py`
  - `api_service/api/routers/execution_integrations.py`
  - `api_service/main.py`
- Persistence and schemas:
  - `api_service/db/models.py`
  - `api_service/migrations/versions/202603060001_integrations_monitoring.py`
  - `moonmind/schemas/temporal_models.py`
- Provider and artifact behavior:
  - `moonmind/workflows/temporal/service.py`
  - `moonmind/workflows/temporal/artifacts.py`
  - `moonmind/workflows/adapters/jules_client.py`
  - `moonmind/config/jules_settings.py`
- Validation:
  - `tests/unit/workflows/temporal/test_temporal_service.py`
  - `tests/contract/test_temporal_execution_api.py`
  - `tests/unit/workflows/adapters/test_jules_client.py`
  - `tests/integration/temporal/test_integrations_monitoring.py` (planned)

## 3. Start the local Temporal runtime stack

```bash
docker compose up -d api-db temporal-db temporal temporal-namespace-init minio rabbitmq api celery-worker
```

Expected baseline:

- Temporal, API, artifact storage, and worker dependencies are healthy in the local Compose network.
- Existing execution APIs remain reachable.
- Jules runtime configuration is explicit; disabled Jules configuration fails fast rather than silently downgrading behavior.

## 4. Exercise the core execution-monitoring flow

1. Create a `MoonMind.Run` execution through `POST /api/executions`.
2. Configure monitoring through `POST /api/executions/{workflowId}/integration` with:
   - `integrationName=jules`
   - provider `externalOperationId`
   - normalized/provider status
   - callback support metadata
3. Verify the execution response shows:
   - `state=awaiting_external`
   - canonical `mm_*` search attributes
   - compact `integration` state with stable `correlationId`

Expected result: the run enters a durable external wait state without creating a provider-specific root workflow type or queue semantic.

## 5. Validate callback ingress and dedupe behavior

1. Submit a valid callback to `POST /api/integrations/{integrationName}/callbacks/{callbackCorrelationKey}`.
2. Re-submit the same callback event ID and then a late non-terminal callback.
3. Send malformed or unverifiable callback payloads.

Expected result:

- Valid callbacks resolve through durable correlation storage and update the target execution.
- Duplicate and stale callbacks are ignored safely.
- Invalid callbacks are rejected before workflow state mutation.

## 6. Validate polling fallback, Continue-As-New, and cancellation

1. Record a non-terminal poll result through the internal reconciliation/test surface `POST /api/executions/{workflowId}/integration/poll` while keeping durable polling timers workflow-owned.
2. Continue polling until wait-cycle thresholds force Continue-As-New.
3. Verify the execution keeps the same `workflowId`, a new `runId`, and preserved monitoring identity/state.
4. Trigger `POST /api/executions/{workflowId}/cancel` while monitoring is active.

Expected result:

- Polling acts as a safe fallback when callbacks are absent.
- Continue-As-New preserves correlation and visibility metadata.
- Cancellation records provider acceptance/ambiguity explicitly and still closes the MoonMind execution correctly.

## 7. Validate artifact and visibility boundaries

1. Capture raw callback bodies and result payloads as Temporal artifacts.
2. Inspect execution memo and search attributes after callback, poll, success, and failure paths.
3. Verify operator-facing summaries remain compact and readable.

Expected result:

- Raw provider payloads and large results are artifact-backed.
- Memo/search attributes do not contain secrets, oversized payloads, or high-cardinality provider identifiers.

## 8. Run repository-standard validation

```bash
./tools/test_unit.sh
```

Notes:

- Use `./tools/test_unit.sh` as the required unit-test command.
- Do not substitute direct `pytest` invocation for acceptance.

## 9. Run runtime scope guards

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected result: runtime scope gates pass with production runtime changes and validation tests represented.

## 10. Planning artifact completion gate

Before `/agentkit.tasks`, ensure these artifacts exist:

- `specs/047-integrations-monitoring/plan.md`
- `specs/047-integrations-monitoring/research.md`
- `specs/047-integrations-monitoring/data-model.md`
- `specs/047-integrations-monitoring/quickstart.md`
- `specs/047-integrations-monitoring/contracts/integrations-monitoring.openapi.yaml`
- `specs/047-integrations-monitoring/contracts/requirements-traceability.md`
