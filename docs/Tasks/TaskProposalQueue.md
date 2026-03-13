# Task Proposal Queue

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskQueueSystem.md`, `docs/Tasks/TaskUiArchitecture.md`

---

## 1. Summary

MoonMind supports a primitive for follow-up proposals:

1. A proposal stores a canonical `taskCreateRequest`.
2. The promoted task executes against `taskCreateRequest.payload.repository`.
3. Dedup and notifications are repository-aware.

This system provides a policy layer so Temporal Activities or automation scripts can intentionally generate:

1. Project follow-up proposals.
2. MoonMind CI/run-quality proposals.
3. Both, when appropriate.

---

## 2. Existing Behavior to Preserve

1. `taskCreateRequest` remains the canonical promote-to-task payload.
2. `taskCreateRequest.payload.repository` remains the execution target after promotion.
3. Dedup key remains based on `(repository + normalized title)`.
4. Notifications continue to include `proposal.repository` and priority.
5. Human review remains required before promotion to a durable Temporal Workflow execution.

---

## 3. Policy Targeting

Temporal Activities emitting these proposals follow a global policy or a task-override policy on where the proposals should be submitted.

### 3.1 Global policy knobs

1. `MOONMIND_PROPOSAL_TARGETS=project|moonmind|both`
2. `MOONMIND_CI_REPOSITORY=MoonLadderStudios/MoonMind` (default)

Behavior:

1. `project`: proposals exclusively target the execution's project repository.
2. `moonmind`: proposals exclusively target `MOONMIND_CI_REPOSITORY`.
3. `both`: workers may emit both types when signals match.

### 3.2 Per-task override (preferred)

`task.proposalPolicy` within the execution payload dynamically alters this state for individual workflows.

## 4. Priority Routing

Priority routing derives the `reviewPriority` dynamically. A "flaky test" proposal or a "repeated failure loop" proposal ranks at `HIGH`, while cosmetic code cleanup proposals rank at `LOW`.
