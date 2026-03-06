# Quickstart: Jules Temporal External Events

## 1. Confirm runtime-mode scope

- Read `specs/048-jules-external-events/spec.md`.
- Treat this feature as runtime implementation mode end to end: production code changes plus automated tests are required.
- Do not treat docs/spec updates alone as completion.

## 2. Review the current Jules implementation surfaces

- Shared gate + normalization:
  - `moonmind/jules/runtime.py`
  - `moonmind/jules/status.py`
- Transport + schemas:
  - `moonmind/config/jules_settings.py`
  - `moonmind/schemas/jules_models.py`
  - `moonmind/workflows/adapters/jules_client.py`
- Temporal integration runtime:
  - `moonmind/workflows/temporal/activity_catalog.py`
  - `moonmind/workflows/temporal/activity_runtime.py`
  - `moonmind/workflows/temporal/workers.py`
  - `moonmind/workflows/temporal/artifacts.py`
- Compatibility/runtime entry points:
  - `api_service/api/routers/agent_queue.py`
  - `api_service/api/routers/mcp_tools.py`
  - `api_service/api/routers/task_dashboard_view_model.py`
  - `moonmind/agents/codex_worker/worker.py`

## 3. Implement shared Jules contract behavior

- Keep Jules availability controlled by the shared runtime gate only.
- Keep all Jules status normalization routed through `moonmind/jules/status.py`.
- Preserve current adapter transport semantics and secret scrubbing.
- Preserve `jules` as integration identity, `taskId` as provider handle, raw `status` as `provider_status`, and `url` as optional external deep link.

## 4. Complete Temporal activity coverage

- Finish `integration.jules.start` contract behavior around correlation hints, idempotency, compact semantic outputs, and artifact-backed start snapshots.
- Finish `integration.jules.status` contract behavior around compact polling outputs, raw/normalized status preservation, and terminal detection.
- Keep `integration.jules.fetch_result` conservative: terminal snapshot artifacts plus failure/summary artifacts when needed.
- Keep `integration.jules.cancel` reserved/unsupported until Jules exposes a real cancel endpoint.

## 5. Keep hybrid migration surfaces aligned

- Keep legacy software polling and Temporal activities using the same gate and status semantics.
- Keep Jules MCP tools hidden when Jules is disabled or incompletely configured.
- Keep queue/API/dashboard compatibility views using MoonMind workflow/task identity as the durable handle and Jules `taskId` as the external provider handle.
- Keep callback behavior future-facing only: `callback_supported` stays false unless callback ingress is actually implemented and verified.

## 6. Extend validation coverage

- Unit + contract suites to extend:
  - `tests/unit/jules/test_status.py`
  - `tests/unit/jules/test_jules_runtime.py`
  - `tests/unit/workflows/adapters/test_jules_client.py`
  - `tests/unit/workflows/temporal/test_activity_runtime.py`
  - `tests/contract/test_temporal_activity_topology.py`
  - `tests/unit/api/routers/test_agent_queue.py`
  - `tests/unit/api/routers/test_mcp_tools.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
  - `tests/unit/agents/codex_worker/test_cli.py`
  - `tests/unit/agents/codex_worker/test_worker.py`

## 7. Run repository-standard validation

```bash
./tools/test_unit.sh
```

Notes:
- In WSL, this delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Do not replace this with direct `pytest`.

## 8. Runtime scope guard checks

Run during implementation/task execution:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime scope passes only when production runtime code and validation tests are both represented.

## 9. Planning artifact completion gate

Before moving to `/speckit.tasks`, ensure these artifacts are present:

- `specs/048-jules-external-events/plan.md`
- `specs/048-jules-external-events/research.md`
- `specs/048-jules-external-events/data-model.md`
- `specs/048-jules-external-events/quickstart.md`
- `specs/048-jules-external-events/contracts/jules-temporal-activity-contract.md`
- `specs/048-jules-external-events/contracts/requirements-traceability.md`
