# Quickstart: Preserve Slash Command Fidelity

## Prerequisites

- Use the current repository checkout for MM-687.
- Keep the canonical source at `specs/357-preserve-slash-command-fidelity/spec.md`.
- Follow `specs/357-preserve-slash-command-fidelity/tasks.md` for the test-first implementation order.

## Test-First Workflow

1. Add failing unit tests for snapshot and draft reconstruction:

   ```bash
   ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py
   ./tools/test_unit.sh --ui-args frontend/src/lib/temporalTaskEditing.test.ts
   ```

   Expected red state before implementation: slash-specific historical metadata preservation or absent-metadata preview-only cases fail if not already covered.

2. Add failing Task Create/Edit/Rerun UI tests:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
   ```

   Cover exact rerun preserving `runtimeCommand`, edit-for-rerun warning recomputation without source mutation, and historical snapshots without metadata.

3. Add failing Task Detail UI tests:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
   ```

   Cover original authored instructions and runtime command interpretation display.

4. Add backend/unit tests for audit event construction and sanitization:

   ```bash
   ./tools/test_unit.sh tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py
   ```

   Keep the focused test path narrowed during iteration, then run the full unit suite before completion.

5. Add a hermetic `integration_ci` integration test for artifact-backed execution retrieval, rerun, and observability event exposure:

   ```bash
   ./tools/test_integration.sh
   ```

   The focused integration test should prove the operator-facing API can reconstruct historical authored instructions, command metadata, and non-secret audit events from existing execution/artifact state.

## End-to-End Validation Scenario

1. Create or fixture a task whose authored instructions begin with `/review`.
2. Ensure the authoritative task input snapshot includes original instructions and runtime command metadata with runtime capability and hint catalog versions.
3. Open edit mode and confirm the snapshot values are restored.
4. Remove command metadata from a historical fixture and confirm edit mode does not mutate the raw instruction value.
5. Request exact rerun and confirm no mutation payload replaces the original metadata.
6. Use edit-for-rerun with simulated current catalog drift and confirm warnings are current-preview-only.
7. Open task details and confirm original instructions appear alongside command, runtime, render mode, status, and version details.
8. Inspect audit/observability output and confirm detected, rendered, and pass-through events are present without secrets.

## Final Verification Commands

Run before handing off to `/moonspec.verify`:

```bash
./tools/test_unit.sh
./tools/test_integration.sh
```

If Docker is unavailable for integration tests in the managed environment, record the blocker and keep the focused unit evidence plus any integration tests added.

## Managed Environment Evidence

- 2026-05-15: Focused Python unit coverage passed with `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py`.
- 2026-05-15: Full frontend Vitest coverage passed with `./node_modules/.bin/vitest run --config frontend/vite.config.ts`.
- 2026-05-15: Focused hermetic integration coverage passed with `pytest tests/integration/api/test_runtime_command_historical_fidelity.py -q --tb=short`.
- 2026-05-15: Full `./tools/test_integration.sh` could not run in this managed environment because Docker image build access returned `403 Forbidden` from administrative rules.
