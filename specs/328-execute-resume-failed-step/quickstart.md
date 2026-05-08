# Quickstart: Execute Resume From the Failed Step Only

## Source

Use Jira issue `MM-634` and the canonical Jira preset brief preserved in `spec.md` as the source of truth.

## Focused Verification

1. Add or run unit tests for checkpoint validation and linked resumed execution creation:

   ```bash
   ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py
   ```

2. Add or run unit tests for preserved-step ledger materialization:

   ```bash
   ./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py
   ```

3. If Task Detail display or schema changes, run the focused UI test through the unit runner:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
   ```

4. Add or run hermetic integration coverage for resumed-run ordering and no re-execution:

   ```bash
   pytest tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py -q --tb=short
   ```

5. Finish with the required full unit suite:

   ```bash
   ./tools/test_unit.sh
   ```

6. Finish with required hermetic integration verification when Docker is available:

   ```bash
   ./tools/test_integration.sh
   ```

## End-to-End Story Checks

- Submit Resume with a valid checkpoint and verify the resumed execution uses the original snapshot unchanged.
- Verify checkpoint source workflow ID, source run ID, snapshot identity, and plan identity are validated before any step executes.
- Verify workspace, branch, commit, or equivalent runtime state is restored before retrying the failed step.
- Verify prior completed source steps appear as preserved progress with source workflow ID, source run ID, logical step ID, and attempt.
- Verify preserved outputs are available to the retried failed step and downstream steps.
- Verify the failed step is the first newly executed step.
- Verify downstream steps execute normally after the failed step succeeds and produce fresh resumed-run evidence.
- Verify invalid restoration fails before execution, creates no full rerun, and does not re-execute preserved steps.

## Final Verification Notes

Final `/speckit.verify` must mention Jira issue `MM-634`, the preserved Jira preset brief in `spec.md`, FR-001 through FR-013, SC-001 through SC-008, and DESIGN-REQ-001 through DESIGN-REQ-005.
