# Quickstart: Activity Catalog and Worker Topology

## 1. Confirm runtime-mode scope

- Read `specs/060-activity-worker-topology/spec.md`.
- Treat this feature as runtime implementation mode: production code and automated validation tests are required.
- Do not treat docs/spec updates alone as completion.

## 2. Review implementation surfaces

- Catalog and routing:
  - `moonmind/workflows/temporal/activity_catalog.py`
  - `moonmind/workflows/temporal/activity_runtime.py`
  - `moonmind/workflows/temporal/workers.py` (planned)
- Artifact and plan execution:
  - `moonmind/workflows/temporal/artifacts.py`
  - `moonmind/workflows/skills/skill_dispatcher.py`
  - `moonmind/workflows/skills/skill_plan_contracts.py`
- Fleet/runtime wiring:
  - `moonmind/config/settings.py`
  - `docker-compose.yaml`
  - `services/temporal/scripts/start-worker.sh` (planned)
- Validation suites:
  - `tests/unit/workflows/temporal/test_activity_catalog.py`
  - `tests/unit/workflows/temporal/test_activity_runtime.py`
  - `tests/unit/workflows/temporal/test_temporal_workers.py` (planned)
  - `tests/contract/test_temporal_activity_topology.py` (planned)
  - `tests/integration/temporal/test_activity_worker_topology.py` (planned)

## 3. Start the compose topology

After implementation, bring up Temporal plus the dedicated worker fleets:

```bash
docker compose up temporal-db temporal temporal-namespace-init minio api \
  temporal-worker-workflow temporal-worker-artifacts temporal-worker-llm \
  temporal-worker-sandbox temporal-worker-integrations
```

Expected runtime shape:

- `temporal-worker-workflow` polls `mm.workflow`
- `temporal-worker-artifacts` polls `mm.activity.artifacts`
- `temporal-worker-llm` polls `mm.activity.llm`
- `temporal-worker-sandbox` polls `mm.activity.sandbox`
- `temporal-worker-integrations` polls `mm.activity.integrations`

## 4. Validate canonical routing behavior

1. Schedule one activity from each canonical family:
   - artifact
   - plan
   - skill
   - sandbox
   - integration
2. Verify each invocation lands on the documented queue and fleet.
3. Verify an unjustified explicit skill binding or incompatible capability request is rejected rather than rerouted.

## 5. Validate payload, visibility, and artifact discipline

1. Execute side-effecting activity requests with `correlation_id`, `idempotency_key`, and artifact references for large inputs.
2. Confirm results return compact summaries plus `output_refs` or `diagnostics_ref` for large output/log data.
3. Confirm workflow visibility updates happen in workflow/service code, not inside activity helpers.

## 6. Validate security and reliability behavior

1. Run a long sandbox command and confirm:
   - heartbeat updates are emitted
   - cancellation remains responsive
   - logs are persisted as redacted artifacts when large
2. Retry representative artifact, sandbox, and integration operations and confirm idempotent behavior.
3. Confirm sandbox workers do not receive provider-only secrets and integrations workers do not execute arbitrary shell commands.
4. Confirm MinIO/object storage remains internal-only in local/dev mode.

## 7. Run repository-standard unit acceptance

```bash
./tools/test_unit.sh
```

Notes:

- This repository requires `./tools/test_unit.sh` for unit acceptance.
- Do not replace it with direct `pytest`.
- In WSL, the script delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Treat `tests/unit/specs/test_doc_req_traceability.py` as part of that unit gate for this feature's `DOC-REQ-*` mapping contract.

## 8. Run compose-backed Temporal validation

- Use the feature-specific Temporal integration suite to verify:
  - fleet startup
  - queue registration
  - routing correctness for User Story 1 exit criteria
  - heartbeat/retry behavior for later hardening
  - restricted artifact preview behavior

## 9. Planning completion gate

Before running `/speckit.tasks`, ensure these artifacts exist:

- `specs/060-activity-worker-topology/plan.md`
- `specs/060-activity-worker-topology/research.md`
- `specs/060-activity-worker-topology/data-model.md`
- `specs/060-activity-worker-topology/quickstart.md`
- `specs/060-activity-worker-topology/contracts/temporal-activity-worker-contract.md`
- `specs/060-activity-worker-topology/contracts/requirements-traceability.md`
