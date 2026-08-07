# Chat Instructions API Contract

**Document Class:** Canonical declarative  
**Status:** Deferred reserved contract  
**Owner:** MoonMind Platform  
**Last updated:** 2026-08-07  
**Audience:** backend, dashboard, integrations

**Authority:** This document reserves an API shape for a possible future explicit Workflow-steering action. It does not define the ordinary Workflow Detail Chat send path. Native chat behavior is owned by `docs/UI/WorkflowChatPanel.md`.

**Implementation tracking:** Rollout tasks and implementation checklists belong under `docs/tmp/`, issues, or pull requests. No route implementation is required until the capability is explicitly promoted.

## 1. Purpose

The reserved endpoint is:

```http
POST /api/executions/{workflowId}/chat-instructions
```

If implemented, it accepts an explicit request to change Temporal-managed workflow behavior. It is not the API used by the native Omnigent composer.

The primary chat path is:

```text
Native Omnigent composer -> native Omnigent session event API
```

The browser must not send ordinary chat messages to the reserved workflow-instruction endpoint, and MoonMind must not heuristically redirect native messages into it.

## 2. Activation boundary

This endpoint should be implemented only after the promotion criteria in `docs/Workflows/ChatInstructionIntervention.md` are satisfied.

A promoted UI must present a distinct action such as:

```text
Steer Workflow
```

It must not reuse the native composer Send button or make users guess whether a message targets the live agent session or the workflow plan.

## 3. Reserved responsibilities

If promoted, the endpoint owns only the API-side work needed before and after the Temporal call:

- authorize the caller against the source Workflow Execution,
- validate that explicit workflow steering is enabled,
- store raw instruction text as an artifact,
- construct a bounded workflow-facing request,
- call `MoonMind.UserWorkflow.SubmitChatInstruction`,
- return the explicit accepted or rejected decision,
- avoid claiming provider delivery that has not been observed.

It must not:

- proxy ordinary Omnigent chat messages,
- replace the native Omnigent event API,
- classify free-form native messages as workflow changes,
- create browser-owned workflow state,
- mutate a terminal Workflow Execution.

## 4. Representative request

A future explicit request may use this shape:

```json
{
  "instructionId": "client-generated-id",
  "idempotencyKey": "optional-dedupe-key",
  "message": "Apply this requirement to the remaining workflow steps.",
  "scope": "future_steps",
  "target": {
    "runId": "current-run-id",
    "logicalStepId": "optional-step-id",
    "stepExecutionOrdinal": 1,
    "planRevision": 2
  },
  "observedState": {
    "runId": "current-run-id-seen-by-client",
    "logicalStepId": "active-step-seen-by-client",
    "planRevision": 2,
    "updatedAt": "2026-08-07T00:00:00Z"
  },
  "policy": {
    "allowCancelActiveStep": false,
    "allowVoidFutureSteps": false,
    "requireApprovalForExternalSideEffects": true
  }
}
```

Rules:

- `message` is accepted at the API boundary and stored as an artifact before workflow delivery.
- Temporal receives `messageArtifactRef` and bounded metadata rather than the full message.
- stable client keys dedupe retries,
- `observedState` enables stale-target rejection,
- destructive policy defaults fail closed.

## 5. Representative response

A future response may use this shape:

```json
{
  "accepted": true,
  "instructionId": "client-generated-id",
  "decision": "queued_for_safe_point",
  "workflowId": "mm:source",
  "runId": "current-run-id",
  "targetLogicalStepId": "implement-change",
  "messageArtifactRef": "artifact://chat-instruction",
  "newPlanRef": null,
  "warnings": []
}
```

The API must return the Temporal-owned decision. It must not infer acceptance from successful artifact creation, optimistic UI state, or a successful native session message POST.

## 6. Reserved decisions

A promoted implementation may return the bounded decisions declared by `docs/Workflows/ChatInstructionIntervention.md`, including accepted queueing, active-Step handling, plan-revision handling, and typed rejection outcomes.

These decisions are not required for ordinary native chat and should never appear merely because a user sent a message through Omnigent.

## 7. Error posture

If implemented, the route should preserve stable error categories such as:

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `execution_not_found` | Source execution does not exist or is not visible to the caller. |
| `409` | `chat_instruction_rejected` | Explicit instruction is well-formed but rejected by workflow state or policy. |
| `422` | `invalid_chat_instruction_request` | Request shape or domain validation failed. |
| `503` | `temporal_unavailable` | The explicit steering action requires Temporal but Temporal is unavailable. |

When the capability is not enabled, the route should be absent or return a stable unsupported-capability response. The primary Chat surface remains available through the native Omnigent binding independently.

## 8. Terminal workflows

The default terminal action is **Continue in a new workflow**, defined by `docs/UI/WorkflowChatPanel.md`.

That action creates linked work through the Workflow creation/continuation surface and leaves the source execution unchanged. It does not require this reserved endpoint.

A future explicit steering contract may define additional terminal behavior, but it must not replace the simple linked-continuation action or make the native terminal transcript writable.

## 9. Relationship to execution actions

- This endpoint is not a replacement for **Cancel**.
- It is not failed-Step recovery.
- It is not **Rerun**, **Resume**, **Remediate**, or **Edit Workflow**.
- It is not the native Omnigent message route.
- It is an optional, separately invoked workflow-level command surface only.
