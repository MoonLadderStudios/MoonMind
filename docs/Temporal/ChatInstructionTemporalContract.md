# Temporal Chat Instruction Contract

**Status:** Design Draft / normative target  
**Owner:** MoonMind Platform  
**Last updated:** 2026-07-02  
**Audience:** backend, workflow authors, managed-runtime, integrations

**Implementation tracking:** Rollout tasks and tactical sequencing belong under `docs/tmp/`, issues, pull requests, or local-only handoffs.

## 1. Purpose

This document defines the Temporal-facing contract for chat instructions. It is a focused companion to `docs/Workflows/ChatInstructionIntervention.md` and should be folded into the larger Temporal architecture docs as implementation stabilizes.

## 2. Primitive selection

Public user chat instructions use a Temporal Update:

```text
MoonMind.UserWorkflow.SubmitChatInstruction
```

Reasons:

- the caller needs synchronous accepted/rejected semantics,
- the workflow must validate stale `runId`, Step, plan revision, and policy before accepting semantic effects,
- the response must explain whether the instruction attached to active work, queued for a safe point, triggered replan, or was rejected.

Internal child workflow delivery may use Signals, including the existing `operator_message` shape or a typed successor. Signals are appropriate after the parent has accepted the instruction and is notifying an active child asynchronously.

## 3. Update handling pattern

The `SubmitChatInstruction` handler should stay lightweight:

1. validate the typed request,
2. dedupe by `instructionId`, `idempotencyKey`, or Temporal Update ID,
3. reject stale targets before acceptance,
4. record a compact instruction command in workflow state,
5. wake the main workflow loop,
6. return `ChatInstructionDecision`.

The handler must not perform provider calls, artifact writes, or plan generation directly. Those belong in Activities or in the main workflow loop after the command is accepted.

## 4. Main-loop safe points

`MoonMind.UserWorkflow` should drain accepted chat commands at safe points such as:

- before selecting the next ready Step,
- before launching a child `MoonMind.AgentRun`,
- while paused,
- while awaiting provider/profile slot assignment,
- between external-provider polling cycles,
- before final completion if a bounded completion-grace window is enabled.

Conflicting mutations are serialized through the workflow-owned command queue rather than by relying on handler interleaving behavior.

## 5. Continue-As-New carry-forward

Continue-As-New payloads must not carry full chat text. Carry-forward state is limited to:

- current plan ref and plan revision,
- pending compact chat instruction commands,
- processed instruction IDs needed for bounded dedupe,
- active Step and child refs,
- Step ledger compact state,
- artifact refs needed to resume safely.

## 6. Child workflow delivery

When a chat instruction targets active agent work, the parent workflow may signal the child `MoonMind.AgentRun` with an internal typed message.

Recommended internal payload concepts:

```json
{
  "instructionId": "client-generated-id",
  "messageArtifactRef": "artifact://chat-instruction",
  "messageSummary": "Bounded display summary",
  "targetLogicalStepId": "implement-change",
  "stepExecutionOrdinal": 1,
  "deliveryPolicy": "append_live"
}
```

Child delivery remains best-effort unless the runtime declares stronger support. The parent decision should distinguish accepted workflow instruction from confirmed provider/runtime delivery when that distinction matters to the UI.

## 7. Replan and reattempt boundaries

Plan revision and active-Step reattempt are workflow orchestration effects, not Signal effects.

- Plan revision should call a planning Activity such as `plan.revise_from_chat_instruction`.
- Active-Step reattempt should request cancellation of the active child, preserve evidence, then start a new Step Execution attempt.
- External side effects require explicit policy or approval before automatic compensation.

## 8. Terminal executions

Closed Workflow Executions do not accept `SubmitChatInstruction` as an ordinary mutation. The API creates linked follow-up executions for terminal sources and pins source `workflowId`, source `runId`, plan refs, finish-summary refs, optional Step ledger refs, and chat instruction refs.

## 9. Visibility posture

The workflow may update `mm_updated_at` on accepted chat instructions. Do not put chat text, full summaries, prompts, diffs, logs, or provider payloads into Search Attributes or Memo.

A future `mm_state = replanning` value should be added only if product surfaces need a first-class list/detail state for chat-driven plan revision.
