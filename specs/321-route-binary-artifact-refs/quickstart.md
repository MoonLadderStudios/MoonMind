# Quickstart: Route Binary Inputs Through Authorized Artifact Refs

## Focused Unit Strategy

1. Add or update frontend tests:
   - `frontend/src/entrypoints/task-create.test.tsx`
   - Verify upload intent creation, upload completion, execution submission ordering, structured refs, and no instruction rewriting remain intact.

2. Add or update API/router tests:
   - `tests/unit/api/routers/test_executions.py`
   - `tests/unit/api/routers/test_temporal_artifacts.py`
   - Cover pending, failed, deleted, missing, duplicate, unsupported, oversized, and unauthorized input artifact refs before execution starts.

3. Add or update artifact service and worker tests:
   - `tests/unit/workflows/temporal/test_artifacts.py`
   - `tests/unit/agents/codex_worker/test_attachment_materialization.py`
   - Prove read policy, execution-scoped links, and service-authorized materialization for task input artifacts.

Focused commands:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_temporal_artifacts.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/agents/codex_worker/test_attachment_materialization.py
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Final unit command:

```bash
./tools/test_unit.sh
```

## Integration Strategy

Add or update hermetic `integration_ci` coverage for:
- completed binary refs accepted into task-shaped execution submission,
- pending/failed/unauthorized refs rejected before execution creation,
- artifact preview/download authorization for linked input attachments,
- worker materialization through service-authorized artifact reads.

Likely files:
- `tests/integration/temporal/test_task_shaped_submission_normalization.py`
- `tests/integration/temporal/test_temporal_artifact_authorization.py`
- `tests/contract/test_temporal_execution_api.py`

Focused integration command where practical:

```bash
pytest tests/integration/temporal/test_task_shaped_submission_normalization.py tests/integration/temporal/test_temporal_artifact_authorization.py -m 'integration_ci' -q --tb=short
```

Final integration command:

```bash
./tools/test_integration.sh
```

## End-To-End Validation Scenario

1. Create a binary artifact upload intent through `POST /api/artifacts`.
2. Upload bytes and complete/finalize the artifact.
3. Submit a task with objective and step `inputAttachments` referencing the completed artifact refs.
4. Confirm execution creation succeeds and links refs as `input.attachment`.
5. Confirm task instructions and workflow parameters contain structured refs, not raw bytes or storage credentials.
6. Confirm browser preview/download succeeds only for authorized principals.
7. Confirm a worker can materialize the same refs with service authorization and writes a target-aware manifest.
8. Repeat with pending, failed, deleted, missing, wrong-owner, and cross-execution refs; each must fail before execution starts or before unauthorized content is returned.

## Traceability Checks

Before task generation and final verification, confirm:
- `MM-628` remains in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/binary-artifact-ref-contract.md`, `quickstart.md`, `tasks.md`, and verification output.
- DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-020, and DESIGN-REQ-022 remain mapped to tests and implementation evidence.
