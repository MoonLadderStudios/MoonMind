# Remaining work: `docs/Temporal/WorkflowArtifactSystemDesign.md`

Updated: 2026-04-04

## Step-ledger rollout

- Standardize step-scoped artifact link metadata (`step_id`, `attempt`, optional `scope`) in artifact creation and linkage flows.
- Add server-side grouping for step evidence so Mission Control does not derive latest step output client-side.
- Verify managed-run and provider result artifacts populate the semantic step artifact slots used by the step ledger.
