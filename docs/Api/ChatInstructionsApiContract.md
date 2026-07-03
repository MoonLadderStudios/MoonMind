# Chat Instructions API Contract

**Status:** Design Draft  
**Owner:** MoonMind Platform  
**Last updated:** 2026-07-02  
**Audience:** backend, dashboard, integrations

**Implementation tracking:** Rollout tasks and implementation checklists belong under `docs/tmp/`, issues, or pull requests. This document defines the public API contract target.

## 1. Purpose

This document defines the `/api/executions/{workflowId}/chat-instructions` endpoint. It is the preferred API surface for user chat that is intended to steer a Temporal-backed Workflow Execution.

The endpoint is deliberately separate from the generic update endpoint because chat instructions need API-owned behavior before and after the Temporal call:

- authorization against the source execution,
- chat text artifact creation,
- policy checks,
- running-vs-terminal state routing,
- linked follow-up execution creation for terminal sources,
- response normalization for the dashboard chat panel.

## 2. Endpoint

```http
POST /api/executions/{workflowId}/chat-instructions
```

Path parameters:

| Field | Meaning |
| --- | --- |
| `workflowId` | Source Workflow Execution identity and target for running-workflow chat. |

Success status:

- `200 OK` when a running execution accepts the instruction through workflow logic.
- `201 Created` when the API creates a linked follow-up execution for a terminal source.

Rejected instructions are not success responses. Well-formed instructions rejected by state or policy return `409 chat_instruction_rejected`; invalid request shape or domain validation returns `422 invalid_chat_instruction_request`.

## 3. Request body

Representative shape:

```json
{
  "instructionId": "client-generated-id",
  "idempotencyKey": "optional-dedupe-key",
  "chatThreadId": "optional-thread-id",
  "message": "Use the Provider Profiles wording in the remaining steps.",
  "scope": "auto",
  "intentHint": "unknown",
  "target": {
    "runId": "current-run-id",
    "logicalStepId": "optional-step-id",
    "stepExecutionOrdinal": 1,
    "planRevision": 2,
    "childWorkflowId": "optional-child-id"
  },
  "observedState": {
    "runId": "current-run-id-seen-by-client",
    "logicalStepId": "active-step-seen-by-client",
    "planRevision": 2,
    "updatedAt": "2026-07-02T00:00:00Z"
  },
  "policy": {
    "allowCancelActiveStep": false,
    "allowVoidFutureSteps": true,
    "allowCreateFollowupExecution": true,
    "requireApprovalForExternalSideEffects": true
  }
}
```

Rules:

- `message` is accepted at the API boundary and then stored as an artifact before workflow delivery.
- The workflow-facing Update receives `messageArtifactRef`, not the full user message.
- `instructionId` and/or `idempotencyKey` are stable client keys and must dedupe retries.
- `observedState` lets the workflow reject stale UI targets before applying semantic effects.
- `policy` expresses caller/product permission for destructive changes such as active-Step cancel-and-reattempt.

## 4. Running execution behavior

When the source execution is non-terminal, the API sends the `SubmitChatInstruction` Update to `MoonMind.UserWorkflow`.

The workflow returns a `ChatInstructionDecision` and remains authoritative for the semantic effect.

Allowed decisions include:

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

The API must not locally claim acceptance if the workflow rejects the Update.

## 5. Terminal execution behavior

When the source execution is terminal, the API does not send an ordinary chat mutation to the closed workflow.

If policy permits follow-up creation, the API starts a linked Workflow Execution with source refs pinned in the new execution input:

```json
{
  "chatFollowup": {
    "sourceWorkflowId": "mm:source",
    "sourceRunId": "source-run-id",
    "sourcePlanRef": "artifact://plan",
    "sourceFinishSummaryRef": "artifact://finish-summary",
    "sourceStepLedgerRef": "artifact://optional-step-ledger-snapshot",
    "chatInstructionRef": "artifact://instruction"
  }
}
```

The response decision is `created_followup_execution` and includes `followupWorkflowId`.

If policy does not permit follow-up creation, the response is a typed rejection such as `rejected_terminal` or `rejected_policy`.

## 6. Response body

Representative running-workflow response:

```json
{
  "accepted": true,
  "instructionId": "client-generated-id",
  "decision": "attached_to_active_step",
  "workflowId": "mm:source",
  "runId": "current-run-id",
  "targetLogicalStepId": "implement-change",
  "childWorkflowId": "mm:source:agent:implement-change",
  "messageArtifactRef": "artifact://chat-instruction",
  "newPlanRef": null,
  "followupWorkflowId": null,
  "warnings": []
}
```

Representative terminal follow-up response:

```json
{
  "accepted": true,
  "instructionId": "client-generated-id",
  "decision": "created_followup_execution",
  "workflowId": "mm:source",
  "runId": "source-run-id",
  "messageArtifactRef": "artifact://chat-instruction",
  "newPlanRef": null,
  "followupWorkflowId": "mm:followup",
  "warnings": []
}
```

## 7. Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `execution_not_found` | Source execution does not exist or is not visible to the caller. |
| `409` | `chat_instruction_rejected` | Instruction is well-formed but rejected by state or policy. |
| `422` | `invalid_chat_instruction_request` | Request shape or domain validation failed. |
| `503` | `temporal_unavailable` | Running execution requires Temporal but Temporal is unavailable. |

## 8. Relationship to other execution actions

- `SubmitChatInstruction` is not a replacement for `Cancel`.
- `SubmitChatInstruction` is not failed-step recovery.
- `SubmitChatInstruction` is not `RequestRerun` unless a future policy explicitly maps a chat intent to a new-run flow.
- Chat-driven terminal follow-up starts a new linked Workflow Execution and leaves the source unchanged.
- Chat-driven future-Step changes create new plan artifacts and supersede future Step rows rather than editing old plan artifacts in place.
