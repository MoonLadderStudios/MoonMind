# Workflow Chat Panel

**Status:** Design Draft  
**Owner:** MoonMind Dashboard  
**Last updated:** 2026-07-02  
**Audience:** dashboard, backend, workflow authors

**Implementation tracking:** Rollout tasks belong under `docs/tmp/`, issues, or pull requests.

## 1. Purpose

This document defines the dashboard contract for the workflow detail chat panel. The panel lets a user submit chat instructions that affect a running Workflow Execution or create linked follow-up work after completion.

This is a UI companion to:

- `docs/Workflows/ChatInstructionIntervention.md`
- `docs/Api/ChatInstructionsApiContract.md`
- `docs/Temporal/ChatInstructionTemporalContract.md`

## 2. Product stance

The chat panel is a control and clarification surface for Workflow Executions. It does not replace the Step ledger, artifacts, logs, or execution actions.

The UI must render the explicit `ChatInstructionDecision` returned by the API. It must not infer acceptance from local state or optimistic UI alone.

## 3. Entry points

The workflow detail page may expose the chat panel when:

- the caller is authorized to update or follow up on the execution,
- the execution is running, waiting, paused, or terminal with follow-up policy enabled,
- the backend runtime config exposes the chat instruction endpoint.

The panel should be available from the main detail route:

```text
/workflows/{workflowId}
```

## 4. Send flow

The panel sends:

```http
POST /api/executions/{workflowId}/chat-instructions
```

The request includes the chat message, optional target hints, the current UI-observed `runId`, active `logicalStepId`, plan revision when known, and policy flags implied by the selected UI action.

The response tells the UI what happened.

## 5. Decision rendering

| Decision | UI behavior |
| --- | --- |
| `attached_to_active_step` | Show accepted state on the message and link to the active Step. |
| `queued_for_safe_point` | Show queued state and explain that it will apply before the next safe boundary. |
| `queued_for_step` | Show the target Step or next Step if known. |
| `active_step_cancel_requested` | Show that current active work is being canceled before reattempt. |
| `step_reattempt_scheduled` | Show the new Step Execution attempt once available. |
| `plan_revision_requested` | Show replanning state and keep the Step list loading from server truth. |
| `future_steps_superseded` | Show superseded Step badges and link to the new plan revision. |
| `created_followup_execution` | Show a link to the follow-up Workflow Execution. |
| `rejected_stale_target` | Ask the user to refresh or retarget because the page was stale. |
| `rejected_terminal` | Offer follow-up creation if policy allows. |
| `rejected_policy` | Explain the policy block and show available safe actions. |
| `rejected_invalid_payload` | Explain that the submitted instruction payload or target state is invalid. |
| `rejected_unsupported_runtime` | Explain that the active runtime cannot accept live chat instructions. |

## 6. Targeting controls

The first implementation may default to `scope = auto`. Advanced controls may later allow:

- target active Step,
- target a specific Step,
- apply to future Steps,
- create follow-up from a completed execution,
- allow or deny active-Step cancellation,
- allow or deny future-Step voiding.

Target controls should be disabled or hidden when the backend cannot enforce the requested policy.

## 7. Step ledger integration

The Steps section remains the primary operator comprehension surface. The chat panel should not duplicate Step truth.

When chat changes Step state, the UI should refetch or subscribe to server state and render:

- instruction refs on affected Step rows,
- new Step Execution attempts,
- superseded future Steps,
- plan revision refs,
- follow-up links for terminal source executions.

## 8. Terminal follow-up UI

For terminal executions, the composer should make the product behavior explicit:

- the source execution will remain unchanged,
- a new linked follow-up execution may be created,
- the follow-up will pin source workflow/run/plan/report refs,
- the user will be taken to or shown a link to the follow-up Workflow Execution.

## 9. Stale-state handling

Every chat request should include the UI-observed `runId`, active Step, and plan revision when known. If the backend returns `rejected_stale_target`, the panel should refresh execution detail and Step ledger before allowing retry.

## 10. Rollout flags

Recommended flags:

- `workflowChatPanelEnabled`
- `workflowChatFollowupEnabled`
- `workflowChatPlanRevisionEnabled`
- `workflowChatCancelReattemptEnabled`

Read-first behavior remains required: the UI should fetch current execution detail and Step ledger before presenting destructive chat actions.
