# Follow-Up Workflow Proposal System Archive

Status: Archived on 2026-09-01
Removal tracker: #3893

This directory preserves the historical design for MoonMind's automatic follow-up workflow proposal system. The design covers post-run candidate generation, proposal persistence, GitHub and Jira issue delivery, reviewer decisions, and promotion into another `MoonMind.UserWorkflow`.

The proposal system was fully removed under #3893. Documents in this directory
are historical evidence only and are not active product or implementation
authority. The preserved design document retains its original status metadata
verbatim as part of that record. This archive notice supersedes those
historical status lines.

## Contents

- [Workflow Proposal System](WorkflowProposalSystem.md) — the preserved proposal-system design formerly stored at `docs/Workflows/WorkflowProposalSystem.md`.

## Removal work

- #3894 — disable new proposals and inventory retained state
- #3895 — add a replay-safe workflow cutover
- #3896 — remove proposal fields from workflow contracts and UI
- #3897 — retire the proposal API, provider delivery, review commands, and promotion
- #3898 — delete the proposal engine, activities, worker support, configuration, and automatic skills
- #3899 — drop proposal persistence
- #3900 — remove remaining status, summaries, telemetry, tests, active documentation references, and replay compatibility

Same-run remediation, explicit workflow submission, and explicit user-directed issue authoring are outside this archived subsystem.

## Retirement inventory and export

Before applying Alembic revision `367_remove_workflow_proposals`, take and
verify a database backup. Run the bounded
[retirement inventory](retirement_inventory.sql) with `psql --file`; it opens a
read-only transaction, reports counts and disposition gaps, limits record
detail to 1,000 rows, and never selects the stored workflow request. Run the
[explicit export](retirement_export.sql) only with standard output redirected
to a protected path. The export contains the stored workflow request and
provider decision metadata, so handle it with the same access and retention
policy as a database backup.

Temporal history evidence is separate from database evidence. Before removing
replay code, list a retention-bounded cohort of `MoonMind.UserWorkflow`
executions in the `default` namespace and inspect each history for the retired
stage and activity names. Record only workflow/run identities and matching
event types; do not copy payload bodies into the report. The deployment may
remove replay compatibility only when this result is empty for its retained
history cohort. The final removal used this criterion rather than elapsed time.

The destructive migration has no schema downgrade because recreating empty
tables would not restore review or delivery evidence. Application rollback
after it runs requires restoring the verified pre-upgrade database backup and
the matching application revision.
