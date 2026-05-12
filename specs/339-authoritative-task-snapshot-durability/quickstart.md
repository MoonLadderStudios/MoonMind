# Quickstart: Authoritative Task Snapshot Durability

## Focused Unit Tests

Run targeted Python unit tests while implementing:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/tasks/test_task_contract.py
```

Run targeted frontend reconstruction tests after JS dependencies are available:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:
- Snapshot payload includes all MM-639 required authored task fields.
- Missing snapshots disable edit/rerun and expose explicit degraded reasons.
- Frontend draft reconstruction uses authoritative snapshot content and rejects invalid attachment bindings.
- Exact full rerun, edited full retry, and Resume remain distinct intents.

## Focused Hermetic Integration / Contract Tests

Run focused integration and contract tests:

```bash
./tools/test_unit.sh tests/contract/test_temporal_execution_api.py
./tools/test_integration.sh
```

During development, narrower pytest targets may be useful:

```bash
pytest tests/integration/temporal/test_full_retry_recovery_actions.py -q
pytest tests/integration/api/test_task_contract_normalization.py -q
pytest tests/contract/test_temporal_execution_api.py -q
```

Expected coverage:
- Real API submission writes one retrievable snapshot artifact before recovery actions are evaluated.
- Snapshot artifact content preserves objective/step attachments by target, preset metadata, provenance, final order, branch, publish, runtime, and dependencies.
- Live preset/template catalog divergence does not affect reconstruction for already submitted tasks.
- Attachment-aware executions without reconstructible snapshots are explicitly degraded.

## End-to-End Story Validation

1. Submit a task-shaped `MoonMind.Run` with objective text, multiple ordered steps, objective and step attachments, runtime/publish selections, repository and branch, dependency declarations, and preset-derived metadata.
2. Confirm execution detail exposes `taskInputSnapshot.available=true`, `reconstructionMode=authoritative`, and a retrievable artifact ref.
3. Change or remove the live preset/template definition used by the submitted task.
4. Open edit, exact full rerun, and edited full retry flows; confirm they reconstruct from the snapshot, not the live catalog.
5. For exact full rerun, confirm no completed progress or Resume checkpoint state is imported.
6. For edited full retry, confirm the new execution receives a new snapshot and the source execution evidence remains unchanged.
7. For failed-step Resume, confirm task input edits are rejected and checkpoint snapshot identity must match the source snapshot.
8. For a synthetic attachment-aware execution missing a snapshot, confirm recovery is blocked or degraded explicitly with no silent attachment loss.

## Final Verification Commands

```bash
./tools/test_unit.sh
./tools/test_integration.sh
```

Then run the MoonSpec verification stage against `specs/339-authoritative-task-snapshot-durability/spec.md`.
