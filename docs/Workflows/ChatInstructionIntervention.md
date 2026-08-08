# Chat Instruction Intervention

**Document Class:** Canonical declarative  
**Status:** Deferred optional extension  
**Owner:** MoonMind Platform  
**Last updated:** 2026-08-07  
**Audience:** backend, workflow authors, dashboard, integrations, managed-runtime, operators

**Authority:** This document owns a possible future **explicit workflow-steering** capability. It does not own the ordinary Workflow Detail Chat experience, which is defined by `docs/UI/WorkflowChatPanel.md` and uses the native Omnigent session UI.

**Implementation tracking:** Rollout tasks and tactical sequencing belong under `docs/tmp/`, issues, pull requests, or local-only handoffs. No implementation work is required by this document until the capability is explicitly promoted.

## 1. Purpose

This document preserves the architectural boundary for a future action that lets a user deliberately change Temporal-managed workflow behavior while a Workflow Execution is active.

It is not a general chat contract.

The primary product path is:

```text
Workflow Detail Chat -> native Omnigent UI -> native Omnigent session
```

A future workflow-steering path, if promoted, would be separate:

```text
Explicit Steer Workflow action -> artifact-backed command -> Temporal Update
```

## 2. Product boundary

The following rules are normative:

1. Ordinary messages from the native Omnigent composer do not become workflow instructions.
2. MoonMind does not infer workflow-steering intent from message text.
3. Native chat does not call `SubmitChatInstruction` or `/api/executions/{workflowId}/chat-instructions`.
4. A future workflow-steering control must be separately labeled and visually distinct from the native composer.
5. Existing Workflow actions such as **Edit Workflow**, **Rerun**, **Resume**, **Remediate**, and **Cancel** remain the primary ways to change workflow lifecycle or configuration.
6. **Continue in a new workflow** is an explicit terminal action owned by the Workflow Chat UI contract, not an ordinary native message.
7. The native transcript remains authoritative for interactive session history; MoonMind artifacts remain authoritative for durable workflow evidence.

The previous product assumption that every Workflow Detail chat message should be classified and routed through Temporal is superseded by `docs/UI/WorkflowChatPanel.md`.

## 3. Promotion criteria

This deferred capability should be promoted only when all of the following are true:

- observed product usage demonstrates a need to change future Workflow Steps without leaving the running execution,
- ordinary native chat and existing Workflow actions are insufficient for that need,
- the UI can present a clearly separate **Steer Workflow** action,
- the workflow can enforce stale-target, side-effect, cancellation, and plan-revision policy,
- the result can be explained with an explicit accepted or rejected decision,
- the capability does not require replacing or intercepting the native Omnigent composer.

Until those criteria are met, the simpler native-chat plan remains complete without this extension.

## 4. Reserved primitive

If promoted, the preferred running-workflow primitive remains a Temporal Update:

```text
MoonMind.UserWorkflow.SubmitChatInstruction
```

The dedicated API route remains reserved:

```http
POST /api/executions/{workflowId}/chat-instructions
```

The route and Update are reserved for explicit workflow-level steering. They are not compatibility aliases for native Omnigent session messaging.

## 5. Core invariants for a future implementation

A promoted implementation must retain these invariants:

1. **Temporal remains the orchestration authority.** The API and dashboard do not schedule, cancel, reattempt, or revise Steps independently.
2. **Instructions are artifact-backed.** Raw instruction text is stored outside workflow history; Temporal receives compact refs and bounded metadata.
3. **Acceptance is explicit.** The workflow validates stale run, Step, plan, and policy state before returning an accepted decision.
4. **Conflicting changes are serialized.** The workflow owns command ordering and applies changes only at legal boundaries.
5. **Closed executions remain immutable.** Terminal continuation creates linked work instead of mutating a closed execution.
6. **Plan changes create new artifacts.** A future plan revision supersedes prior future work rather than editing an immutable plan in place.
7. **Provider delivery is distinct from workflow acceptance.** An accepted workflow command does not falsely imply that a live provider session consumed it.

## 6. Reserved decision vocabulary

If the capability is promoted, a bounded decision vocabulary may include:

```text
attached_to_active_step
queued_for_safe_point
queued_for_step
active_step_cancel_requested
step_reattempt_scheduled
plan_revision_requested
future_steps_superseded
rejected_stale_target
rejected_terminal
rejected_policy
rejected_invalid_payload
rejected_unsupported_runtime
```

This vocabulary is not required for native Workflow Detail Chat and should not appear in the primary native transcript unless an explicit workflow-steering action produced the decision.

## 7. Source-of-truth rules

| Concern | Authoritative source |
| --- | --- |
| Native session messages and transcript | Omnigent session and native UI |
| Native session live state | Omnigent host/server session state |
| Workflow execution and Step state | `MoonMind.UserWorkflow` and Step ledger |
| Explicit workflow instruction text | MoonMind instruction artifact |
| Explicit instruction acceptance | Temporal Update result |
| Plan revision | immutable plan artifact plus workflow state |
| Durable provider evidence | MoonMind artifact system |
| Terminal continuation relationship | linked Workflow Execution relation |

No projection row or browser-local state may claim a workflow mutation that Temporal did not accept.

## 8. Relationship to terminal continuation

The default terminal experience is defined by `docs/UI/WorkflowChatPanel.md`:

- show the terminal native transcript read-only,
- expose captured evidence,
- offer **Continue in a new workflow** when authorized.

That action may reuse source workflow, run, Step, report, and snapshot refs, but it is not evidence that the deferred `SubmitChatInstruction` capability has been implemented.

## 9. Visibility and rollout posture

Do not add chat text or long summaries to Search Attributes or Memo.

Do not add workflow-chat steering flags to the primary Chat rollout. The primary flags are owned by `docs/UI/WorkflowChatPanel.md`.

If this extension is later promoted, it should receive its own explicit capability and rollout flag, such as:

```text
workflowSteeringInstructionEnabled
```

The flag must not change the behavior of the native Omnigent composer.
