# Temporal Chat Instruction Contract

**Document Class:** Canonical declarative  
**Status:** Deferred optional extension  
**Owner:** MoonMind Platform  
**Last updated:** 2026-08-07  
**Audience:** backend, workflow authors, managed-runtime, integrations

**Authority:** This document reserves the Temporal boundary for a possible future explicit Workflow-steering action. It does not apply to ordinary native Omnigent chat messages. The primary Chat product contract is `docs/UI/WorkflowChatPanel.md`.

**Implementation tracking:** Rollout tasks and tactical sequencing belong under `docs/tmp/`, issues, pull requests, or local-only handoffs. No workflow-loop changes are required until the capability is explicitly promoted.

## 1. Purpose

The ordinary Workflow Detail Chat path does not pass through Temporal:

```text
Native Omnigent composer -> native Omnigent session event API
```

This document applies only to a future, separately invoked command such as:

```text
Steer Workflow -> MoonMind.UserWorkflow.SubmitChatInstruction
```

MoonMind must not classify native chat text and silently convert it into a Temporal Update.

## 2. Reserved primitive

If the extension is promoted, the public workflow primitive remains:

```text
MoonMind.UserWorkflow.SubmitChatInstruction
```

A Temporal Update is appropriate for explicit workflow steering because the caller needs a synchronous accepted or rejected decision and the workflow must validate current run, Step, plan, and policy state.

The reserved Update is not:

- the Omnigent session message transport,
- a compatibility alias for native chat,
- a required part of the Workflow Chat rollout,
- a reason to duplicate the native composer.

## 3. Promotion gate

The Update handler and workflow command queue should be implemented only after `docs/Workflows/ChatInstructionIntervention.md` is promoted from its deferred status.

Promotion requires:

- a separately labeled workflow-steering UI,
- demonstrated need beyond native chat and existing Workflow actions,
- stale-target and side-effect policy,
- explicit accepted/rejected semantics,
- no interception of ordinary native session messages.

## 4. Future handler invariants

If implemented, the `SubmitChatInstruction` handler must remain lightweight:

1. validate the typed request,
2. dedupe by stable client keys,
3. reject stale targets before acceptance,
4. record a compact artifact-backed command in workflow state,
5. wake the main workflow loop,
6. return a bounded decision.

The handler must not:

- perform provider calls,
- post to an Omnigent session,
- write large artifacts directly,
- generate a revised plan directly,
- cancel a child directly,
- claim that a provider consumed the instruction.

Activities or the main workflow loop own any promoted orchestration effects after acceptance.

## 5. Future safe-point handling

A promoted implementation may drain explicit workflow commands at legal workflow-owned boundaries such as:

- before selecting the next ready Step,
- before launching a child `MoonMind.AgentRun`,
- while paused,
- before final completion during a bounded completion-grace window.

The current native-chat plan does not require adding these safe points or a workflow command queue.

Conflicting workflow mutations, if later supported, must be serialized through workflow-owned state rather than browser or handler interleaving.

## 6. Artifact and history posture

A future workflow-facing request carries:

- `instructionId`,
- optional stable idempotency key,
- `messageArtifactRef`,
- bounded message summary,
- observed run, Step, and plan identity,
- explicit policy fields.

It must not carry full chat transcripts, provider payloads, diffs, logs, or large user text in workflow history.

Continue-As-New, if relevant, carries only compact pending command state, bounded dedupe state, plan refs, Step refs, and artifact refs.

Native Omnigent transcript history remains outside this command contract.

## 7. Future child delivery

If an accepted explicit workflow command targets active agent work, the parent workflow may later deliver a typed artifact-ref instruction to the child workflow or runtime.

Workflow acceptance and provider delivery are different states:

```text
accepted by Temporal != consumed by Omnigent session
```

The parent decision must not claim live provider delivery without provider evidence.

Ordinary native messages bypass this mechanism and use the native Omnigent session directly.

## 8. Future replan and reattempt effects

If promoted:

- plan revision is a workflow orchestration effect that creates a new immutable plan artifact,
- active-Step reattempt requests cancellation, preserves evidence, and creates a new Step Execution attempt,
- external side effects require explicit policy or approval,
- superseded future Steps remain visible in the Step ledger.

None of these effects are implied by a message sent through the native composer.

## 9. Terminal executions

Closed Workflow Executions do not accept `SubmitChatInstruction` as an ordinary mutation.

The default terminal product path is defined by `docs/UI/WorkflowChatPanel.md`:

- inspect the terminal native transcript read-only,
- view captured MoonMind evidence,
- use **Continue in a new workflow** when authorized.

That linked continuation does not require this deferred Temporal Update contract.

## 10. Visibility posture

Do not put chat text, full summaries, prompts, diffs, logs, or provider payloads into Search Attributes or Memo.

No new `mm_state`, pending-instruction field, or chat-steering Search Attribute is required for the native Chat rollout.

If the extension is later promoted, bounded visibility fields should be added only when a real list or detail filtering requirement exists.
