# Quickstart: Persist Authoritative Task Snapshots

## Test-First Validation

1. Run the focused API action capability tests:

   ```bash
   ./tools/test_unit.sh tests/unit/api/routers/test_executions.py
   ```

2. Confirm terminal `MoonMind.Run` executions without `task_input_snapshot_ref` disable edit/rerun actions even when task parameters include instructions or steps.

3. Confirm terminal executions with `task_input_snapshot_ref` still expose edit/rerun actions when the task editing feature flag is enabled.

4. Confirm failed-step resume remains gated on both `task_input_snapshot_ref` and `resume_checkpoint_ref`.

## Final Verification

Run the required unit suite before closing the story:

```bash
./tools/test_unit.sh
```

Run hermetic integration only if implementation expands beyond API serialization/action capability logic:

```bash
./tools/test_integration.sh
```
