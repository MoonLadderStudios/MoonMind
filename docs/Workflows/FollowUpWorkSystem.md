# Explicit Follow-Up Work System

**Status:** Implemented  
**Document Class:** Cross-Cutting Concept View and architectural decision record  
**Decision:** MoonLadderStudios/MoonMind#3893  
**Owners:** MoonMind Platform  
**Last Updated:** 2026-09-03

## Decision

MoonMind does not automatically turn one workflow's observations into another
workflow or provider issue. A `MoonMind.UserWorkflow` proceeds from execution
or external waiting directly to finalization. Starting more work is a separate,
explicit authority handoff:

- a user or authorized automation submits a new workflow through the normal
  execution contract; or
- a user explicitly invokes an issue-authoring Skill, such as
  `code-improvement-proposal`, and that Skill follows its own provider mutation
  and evidence contract.

Provider comments, labels, webhooks, finish summaries, telemetry, and stored
historical input do not grant authority to create another workflow. Ordinary
finish-summary prose may describe unfinished work, but it is evidence only.

## Rationale

The retired automatic workflow proposal subsystem duplicated workflow
submission, provider delivery, review state, persistence, authorization, and
recovery semantics. That parallel lifecycle increased the failure state space
after the requested work had already reached its terminal path. Explicit
submission and explicit issue-authoring Skills provide the useful outcomes
through existing authority boundaries without a second durable work queue.

## Contract consequences

- New submissions reject `proposeTasks`, `proposalPolicy`, and their legacy
  aliases with a bounded message that identifies the removed field.
- Edit and rerun reconstruction may read historical inputs but strips those
  fields before creating a new submission.
- No workflow lifecycle state or finish-summary field represents automatic
  follow-up generation.
- No GitHub or Jira event can promote stored follow-up work into an execution.
- Same-run remediation remains responsible for bounded attempts to complete the
  current request before termination.
- Procedural-memory proposals remain internal learning objects. They do not
  create workflows or provider issues.

## Historical evidence and rollback boundary

The former design and the read-only pre-migration inventory/export commands are
preserved in the [historical archive](../Archive/ProposalSystem/README.md). They
are not product authority. The schema-removal migration is reversible only by
restoring a verified pre-upgrade database backup because an empty schema
downgrade cannot restore deleted review and delivery evidence.

Temporal replay compatibility is removed only after a retention-bounded history
inventory reports no execution containing the retired stage or activities.
Elapsed time alone is not retirement evidence.
