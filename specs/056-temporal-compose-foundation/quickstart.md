# Quickstart: Temporal Compose Foundation

## 1. Confirm runtime-mode scope

- Read `specs/044-temporal-compose-foundation/spec.md` and confirm runtime deliverables are mandatory.
- Treat this feature as runtime mode end-to-end: production code plus validation tests are required.
- Do not treat docs/spec updates alone as completion criteria.

## 2. Review planned implementation surfaces

- Compose and env:
  - `docker-compose.yaml`
  - `.env-template`
  - `services/temporal/dynamicconfig/development-sql.yaml`
  - `services/temporal/scripts/bootstrap-namespace.sh`
  - `services/temporal/scripts/rehearse-visibility-schema-upgrade.sh` (planned)
- Runtime and API:
  - `moonmind/config/settings.py`
  - `moonmind/workflows/temporal/` (planned module set)
  - `api_service/api/routers/executions.py` (planned)
  - `api_service/api/routers/recurring_tasks.py`
  - `api_service/api/routers/workflows.py` (compatibility/deprecation)
- Planned validation suites:
  - `tests/contract/test_temporal_execution_api.py`
  - `tests/integration/temporal/test_compose_foundation.py`
  - `tests/integration/temporal/test_namespace_retention.py`
  - `tests/integration/temporal/test_visibility_and_lifecycle.py`
  - `tests/unit/workflows/temporal/`

## 3. Bring up Temporal foundation services

```bash
docker compose up -d temporal-db temporal temporal-namespace-init
```

Optional operator services:

```bash
docker compose --profile temporal-tools up -d temporal-admin-tools
docker compose --profile temporal-ui up -d temporal-ui
```

Expected baseline:

- `temporal` reachable internally at `temporal:7233` only.
- Namespace bootstrap completes successfully and is rerunnable (idempotent).
- No public gRPC host port is exposed.

## 4. Validate foundation policy contracts

Run/verify foundation checks:

1. Health check confirms Temporal service + persistence + SQL visibility are healthy.
2. Namespace reconciliation confirms `moonmind` namespace and retention defaults.
3. Shard decision gate is recorded/acknowledged before rollout.
4. Worker versioning default validates as Auto-Upgrade.

If upgrade rehearsal script is implemented:

```bash
./services/temporal/scripts/rehearse-visibility-schema-upgrade.sh
```

Expected outcome: rehearsal passes, otherwise rollout remains blocked.

## 5. Validate lifecycle and schedule behavior

- Verify lifecycle contract endpoints:
  - `POST /api/executions`
  - `POST /api/executions/{workflowId}/update`
  - `POST /api/executions/{workflowId}/signal`
  - `POST /api/executions/{workflowId}/cancel`
  - `GET /api/executions`
  - `GET /api/executions/{workflowId}`
- Confirm list/filter/count/pagination are sourced from Temporal Visibility.
- Confirm recurring automation is managed through Temporal schedules, not external cron ownership.
- Confirm manifest failure policies (`fail_fast`, `continue_and_report`, `best_effort`) behave as requested.

## 6. Run repository-standard unit validation

```bash
./tools/test_unit.sh
```

Notes:

- In WSL, this command delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Do not replace this acceptance command with direct `pytest`.

## 7. Run runtime scope guard checks (implementation stage)

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime scope gates pass with production runtime implementation and validation tasks represented.

## 8. Planning artifact completion gate

Before advancing to `/speckit.tasks`, ensure these artifacts exist:

- `specs/044-temporal-compose-foundation/plan.md`
- `specs/044-temporal-compose-foundation/research.md`
- `specs/044-temporal-compose-foundation/data-model.md`
- `specs/044-temporal-compose-foundation/quickstart.md`
- `specs/044-temporal-compose-foundation/contracts/temporal-executions.openapi.yaml`
- `specs/044-temporal-compose-foundation/contracts/requirements-traceability.md`
