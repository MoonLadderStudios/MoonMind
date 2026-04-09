# Review Check Ledger Contract

## Workflow behavior

1. Execute the plan step normally.
2. If approval policy is enabled for the step and the execution completed successfully:
   - mark the step `reviewing`
   - upsert a pending `approval_policy` check row
   - execute `step.review`
   - write review evidence to an artifact
   - update the same check row with final verdict summary, retry count, and `artifactRef`
3. If the verdict is `FAIL` and retries remain:
   - inject feedback into the rerun
   - rerun the same logical step
4. If the verdict is `PASS` or `INCONCLUSIVE`:
   - finish the step normally

## Step row invariants

- `logicalStepId` remains stable across review retries.
- `attempt` increments for each rerun of the logical step.
- `checks[]` remains bounded and display-safe.
- `artifactRef` points to review evidence, never inline review bodies.

## UI contract

The Checks section must render, per check row:

1. verdict badge
2. bounded summary
3. `Retry count: N`
4. `Review artifact: <artifactRef>` or explicit “No review artifact linked yet.”
