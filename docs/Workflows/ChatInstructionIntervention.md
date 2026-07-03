# Chat Instruction Intervention

**Status:** Design Draft / normative target  
**Owner:** MoonMind Platform  
**Last updated:** 2026-07-02  
**Audience:** backend, workflow authors, dashboard, integrations, managed-runtime, operators

**Implementation tracking:** Rollout tasks and tactical sequencing belong under `docs/tmp/`, issues, pull requests, or local-only handoffs. This document defines the durable architecture and product contract.

## 1. Purpose

MoonMind is adding a chat interface to active and completed Workflow Executions. This document defines how a user chat message can safely affect Temporal-managed work without turning the API, dashboard, or projection layer into a second orchestration engine.

The preferred product term is **chat**. New public contracts should use names such as `chatInstruction`, `chatThread`, and `SubmitChatInstruction`.

A chat message becomes a typed, artifact-backed workflow instruction. `MoonMind.UserWorkflow` decides whether it attaches to the active Step, queues for a safe point, triggers a plan revision, cancels and reattempts active work, creates a linked follow-up execution, or rejects the request.

## 2. Related docs

This document is the cross-cutting owner for chat instruction intervention and should be reflected by the Temporal architecture, workflow lifecycle, signal/update, type-safety, Step ledger, run-history, artifact, API, UI, and managed-runtime docs.

## 3. Design principles

1. **Chat is an input surface, not an orchestrator.** The dashboard and API may classify and route a chat instruction, but they must not schedule work independently of Temporal workflow state.
2. **Public chat mutations use Updates.** The running-workflow primitive is `SubmitChatInstruction`; internal child delivery may use compact Signals such as `operator_message`.
3. **Chat text is artifact-backed.** Workflow history, Search Attributes, Memo, and Step rows carry compact refs, summaries, hashes, and bounded metadata only.
4. **The workflow serializes conflicting mutations.** The Update validates, dedupes, records a compact command, wakes the main loop, and returns a bounded decision. The main loop drains commands at safe points.
5. **Closed executions are immutable.** Terminal Workflow Executions do not accept ordinary chat mutation; the API creates linked follow-up executions with pinned source refs.
6. **Replanning creates a new plan artifact.** Chat that changes future work creates a plan revision and records superseded future Steps.

## 4. Public control surface

Preferred endpoint:

```http
POST /api/executions/{workflowId}/chat-instructions
```

The endpoint authenticates the caller, stores the chat message as an instruction artifact, inspects execution state, calls the `SubmitChatInstruction` Update when the execution is running, creates a linked follow-up execution when the source execution is terminal, and returns a `ChatInstructionDecision`.

The generic update endpoint may also accept `updateName = SubmitChatInstruction`, but the dedicated route is preferred because it owns artifact creation, policy checks, terminal fallback, and follow-up creation.

Existing `SendMessage` behavior may remain as a compatibility alias for active-Step chat addendum only. New clients should use `SubmitChatInstruction`.

## 5. Boundary models

New models should live in a domain schema module such as `moonmind/schemas/chat_instruction_models.py`.

`ChatInstructionInput` should include `instructionId`, optional `idempotencyKey`, optional `chatThreadId`, `messageArtifactRef`, bounded `messageSummary`, `scope`, `intentHint`, a target block with `workflowId`, `runId`, `logicalStepId`, `stepExecutionOrdinal`, `planRevision`, and `childWorkflowId`, an `observedState` block for stale-target rejection, and policy flags such as `allowCancelActiveStep`, `allowVoidFutureSteps`, and `allowCreateFollowupExecution`.

`ChatInstructionDecision` should include `accepted`, `instructionId`, `decision`, `workflowId`, `runId`, optional `targetLogicalStepId`, optional `childWorkflowId`, `messageArtifactRef`, optional `newPlanRef`, optional `followupWorkflowId`, and warnings.

Allowed decisions include `attached_to_active_step`, `queued_for_safe_point`, `queued_for_step`, `active_step_cancel_requested`, `step_reattempt_scheduled`, `plan_revision_requested`, `future_steps_superseded`, `created_followup_execution`, `rejected_stale_target`, `rejected_terminal`, `rejected_policy`, `rejected_invalid_payload`, and `rejected_unsupported_runtime`.

## 6. Runtime behavior

`MoonMind.UserWorkflow` evaluates chat instructions against workflow-owned state.

- If an active child supports live addendum, the instruction is attached to the active Step and forwarded internally.
- If the active child cannot safely accept a live addendum and policy permits, the parent cancels active work and schedules a new Step Execution attempt.
- If the instruction changes future work, the workflow requests a plan revision, writes a new immutable plan artifact, and marks old future Steps superseded.
- If the client targets a stale `runId`, Step, or plan revision, the Update rejects the instruction before applying semantic effects.
- If the source execution is terminal, the workflow rejects mutation and the API may create a linked follow-up execution.

## 7. Plan revision and Step ledger rules

Chat-driven plan changes do not edit a plan in place. The new plan artifact should record `planRevision`, `supersedesPlanRef`, `chatInstructionRef`, `replanBoundary`, preserved logical Step IDs, superseded logical Step IDs, and new logical Step IDs.

The preferred target-state Step status for future work voided by chat is `superseded`. If adding a new status is too disruptive initially, represent it as `status = skipped` plus `terminalDisposition = superseded_by_chat_instruction` and refs to the chat instruction and superseding plan artifact.

## 8. Terminal follow-up behavior

A terminal source execution remains immutable. The API-created follow-up execution receives its own `workflowId` and `runId` and pins source `workflowId`, source `runId`, source plan refs, finish summary refs, optional Step ledger snapshot refs, and the chat instruction ref. The relationship type should be `chat_followup`.

Follow-up execution is not failed-step recovery and not `RequestRerun` / Continue-As-New unless a future policy explicitly defines that behavior.

## 9. Source-of-truth rules

| Concern | Authoritative source | Projection role |
| --- | --- | --- |
| Chat text | Instruction artifact | optional preview/ref display |
| Instruction acceptance | Workflow Update result or API follow-up creation result | cache latest decision for UI |
| Running instruction command | `MoonMind.UserWorkflow` state/history | derived row or timeline item |
| Active child delivery | child workflow history and runtime/provider artifacts | display delivery state |
| Plan revision | new plan artifact + workflow state refs | display current plan revision |
| Superseded future Steps | workflow Step ledger | optional derived step projection |
| Follow-up relation | API-created relation plus source/target refs | related-workflows display |

Projection rows must not accept chat instructions by local mutation only.

## 10. Visibility, artifact, and UI posture

Do not add chat text or long chat summaries to Search Attributes. `mm_updated_at` may move on accepted chat instructions. A future bounded state such as `mm_state = replanning` or a bounded pending-instruction field should be added only if list filtering requires it.

Recommended content types are `application/vnd.moonmind.chat-instruction+json;version=1`, `application/vnd.moonmind.chat-instruction-decision+json;version=1`, and `application/vnd.moonmind.chat-plan-revision+json;version=1`.

The workflow detail page should expose a chat panel that sends chat instructions, shows the explicit decision, displays stale-target guidance, renders superseded future Steps distinctly, and links terminal source executions to chat follow-up executions.

## 11. Rollout plan

1. Add typed `SubmitChatInstruction`, keep `SendMessage` as a compatibility alias, store chat text as artifacts, and forward supported active-Step messages.
2. Add the dedicated API route and terminal follow-up execution creation.
3. Add plan revision metadata, a replan activity, superseded Step rows, and UI rendering.
4. Add policy-gated active child cancellation and Step reattempts.
5. Introduce a dedicated `MoonMind.ChatThread` workflow only if the product later needs multi-execution thread orchestration as a durable entity.
