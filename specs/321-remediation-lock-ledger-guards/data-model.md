# Data Model: Remediation Lock, Ledger, and Loop Guards

## Remediation Task

Represents a MoonMind task marked with remediation intent.

Fields:
- `remediationWorkflowId`: logical workflow ID of the remediation task.
- `remediationRunId`: current run ID of the remediation task when evaluating a mutation.
- `authorityMode`: observe-only, approval-gated, or admin-auto authority mode.
- `target`: pinned target execution identity.

Validation rules:
- `remediationWorkflowId` is required before evaluating a side-effecting action.
- A remediation task may not target itself unless policy explicitly allows self-targeting.

## Target Execution

Represents the logical execution and pinned run snapshot being diagnosed or mutated.

Fields:
- `targetWorkflowId`: logical execution ID.
- `targetRunId`: pinned run ID captured for remediation.
- `currentRunId`: current run ID from fresh bounded target health.
- `state`: current bounded target state.
- `summary`: current bounded target summary.
- `sessionIdentity`: current managed-session identity when available.

Validation rules:
- Side-effecting actions compare pinned and current identity/state before execution.
- Material target change produces no-op, rediagnose, or escalation behavior according to policy.
- Missing required fresh target health produces a bounded denial reason.

## Mutation Lock

Exclusive permission to mutate a shared remediation target.

Fields:
- `lockId`: stable lock identifier for scope, target, run, and holder.
- `scope`: default `target_execution`.
- `mode`: default `exclusive`.
- `holderWorkflowId`: remediation workflow holding the lock.
- `holderRunId`: run ID for the holder.
- `targetWorkflowId`: target execution ID.
- `targetRunId`: target run ID.
- `createdAt`: acquisition timestamp.
- `expiresAt`: expiration timestamp.
- `released`: whether the lock was explicitly released or lost.

Validation rules:
- Only one unreleased, unexpired lock may exist for a target scope/run.
- Same holder retry reuses the lock.
- Other holders receive `mutation_lock_conflict` while the lock is active.
- A released holder receives `mutation_lock_lost` rather than silently continuing.

State transitions:
- `not_evaluated` -> `acquired`
- `acquired` -> `mutation_lock_conflict` for competing holders
- `acquired` -> `released`
- `released` -> `mutation_lock_lost` for previous holder
- `expired` -> `recovered` for the next allowed holder

## Action Ledger Entry

Durable duplicate-suppression decision for one logical side-effecting action request.

Fields:
- `idempotencyKey`: stable key for the logical side effect.
- `requestShapeHash`: hash of action kind, target, run, and parameters.
- `recordedAt`: decision timestamp.
- `result`: serialized guard result.

Validation rules:
- Missing idempotency keys are denied.
- Same key and same request shape return the prior ledger decision.
- Same key with a different request shape is denied as unsafe reuse.
- Ledger state is durable across service restarts through remediation link state.

## Budget And Cooldown Decision

Policy evaluation for repeated side effects.

Fields:
- `actionsUsed`: count of actions already accepted for a target.
- `maxActionsPerTarget`: allowed total action count.
- `attemptsForActionKind`: accepted attempts for the target/action kind.
- `maxAttemptsPerActionKind`: allowed attempts for one action kind.
- `cooldownSeconds`: minimum interval before repeating an identical action shape.
- `status`: within-budget or blocked reason.

Validation rules:
- Exceeding total actions escalates or denies before action execution.
- Exceeding action-kind attempts escalates or denies before action execution.
- Repeated identical action shapes inside cooldown are denied.

## Safety Decision

Operator-visible result of guard evaluation.

Fields:
- `decision`: allowed, denied, no-op, rediagnose, or escalate.
- `reason`: bounded machine-readable reason.
- `executable`: whether downstream action execution may proceed.
- `lock`, `ledger`, `budget`, `nestedRemediation`, `targetFreshness`: structured evidence for the decision.
- `redactedParameters`: safe parameter echo for audit and artifact publication.

Validation rules:
- Any blocked action must include a bounded reason.
- Sensitive values and raw local paths are redacted before publication.
