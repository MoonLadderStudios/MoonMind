# Remaining work: `docs/Temporal/RunHistoryAndRerunSemantics.md`

Updated: 2026-04-04

## Step-ledger rollout

- Keep default task detail and default step detail anchored to the latest/current run only.
- Add explicit follow-on design if historical run step views or cross-run attempt history become product requirements.
- Verify rerun and Continue-As-New flows preserve `workflowId` identity while rotating `runId` and step-attempt scope correctly.
