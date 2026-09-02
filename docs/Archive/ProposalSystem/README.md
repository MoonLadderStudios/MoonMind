# Follow-Up Workflow Proposal System Archive

Status: Archived on 2026-09-01
Removal tracker: #3893

This directory preserves the historical design for MoonMind's automatic follow-up workflow proposal system. The design covers post-run candidate generation, proposal persistence, GitHub and Jira issue delivery, reviewer decisions, and promotion into another `MoonMind.UserWorkflow`.

The proposal system is scheduled for full removal. Documents in this directory are historical evidence only and are not active product or implementation authority. The preserved design document retains its original status metadata verbatim as part of that record. This archive notice supersedes those historical status lines.

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
