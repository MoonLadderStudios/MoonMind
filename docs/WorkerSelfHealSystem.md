# Worker Self-Heal System

Status: Proposed
Owners: MoonMind Engineering
Last Updated: 2026-02-19

## 1. Purpose

Define a deterministic self-healing system for queue Task runs so workers can recover from stuck step execution without silently looping forever.

This design focuses on `type="task"` jobs that execute `task.steps[]` and introduces:

- stuck detection (timeouts + no-progress)
- bounded in-step retries
- context reset strategies (soft and hard)
- resume semantics from a step checkpoint
- explicit escalation when self-heal cannot recover

## 2. Problem Statement

Current behavior allows long-running retries inside one step prompt path when an agent fails to converge. Queue-level retries (`maxAttempts`) exist, but worker execution currently marks failures as non-retryable in common paths, so queue retry budgets are not fully leveraged for recovery.

Result: jobs can continue heartbeating while making low-value repeated edits, with no explicit "stuck" terminal reason.

## 3. Goals

1. Recover from transient failures automatically.
2. Bound retry behavior to avoid indefinite loops.
3. Preserve successful progress from previous steps.
4. Provide operator-visible state and controls for recovery.
5. Fail fast and clearly when recovery is exhausted.

## 4. Non-Goals

- Parallel step execution.
- Arbitrary runtime migration across providers within one attempt.
- Full checkpointed filesystem snapshots in v1.

## 5. High-Level Recovery Model

Self-heal runs at two layers:

1. In-step self-heal (primary)
- Retries only the failing step.
- Starts a fresh runtime process for each retry.
- Uses minimal retry context instead of full prior transcript.

2. Job-level retry (secondary)
- When in-step budget is exhausted, mark failure `retryable=true` for eligible transient/stuck classes.
- Queue `maxAttempts` performs coarse requeue with exponential backoff.

## 6. Failure Detection

A step is considered stuck/recoverable when any of the following occurs:

1. Step wall-clock timeout exceeded.
2. Step idle timeout exceeded (no output chunk for configured interval).
3. No-progress threshold reached:
- same normalized failure signature across attempts, and
- same post-attempt diff hash (or empty diff) across attempts.

A step is considered non-recoverable when classified as deterministic configuration/contract failure:

- schema/validation failures
- missing required credentials/permissions
- unsupported runtime/capability mismatches
- repository policy violations

## 7. Failure Classification

Classification drives retryability.

### 7.1 Classes

- `transient_runtime`: CLI timeout, transport failure, interrupted subprocess, temporary service unavailability.
- `stuck_no_progress`: repeated identical failure + unchanged diff.
- `deterministic_contract`: payload/schema/validation errors.
- `deterministic_policy`: auth/capability/policy blocks.
- `deterministic_repo`: permanent git/repository constraints.

### 7.2 Policy

- Retry in-step: `transient_runtime`, `stuck_no_progress`.
- Do not retry in-step: deterministic classes.
- Queue-level retryable fail allowed only for classes configured as externally recoverable.

## 8. Retry Budgets and Guardrails

Introduce explicit budgets:

- `step_max_attempts`: max attempts per step (initial + retries).
- `step_timeout_seconds`: per-attempt wall-clock timeout.
- `step_idle_timeout_seconds`: per-attempt no-output timeout.
- `step_no_progress_limit`: max consecutive no-progress attempts.
- `job_self_heal_max_resets`: max hard resets per job.

Default values (initial proposal):

- `step_max_attempts = 3`
- `step_timeout_seconds = 900`
- `step_idle_timeout_seconds = 300`
- `step_no_progress_limit = 2`
- `job_self_heal_max_resets = 1`

## 9. Context Reset Strategy

### 9.1 Soft Reset (default first)

- Terminate active runtime process.
- Start a new runtime process for same step in same repo workspace.
- Provide compact retry prompt with minimal history.

### 9.2 Hard Reset (escalation)

When no-progress persists after soft resets:

1. Create fresh recovery workspace from resolved start ref.
2. Replay successful step checkpoints (patches) up to `step_index - 1`.
3. Re-run failing step attempt with minimal retry context.
4. On success, replace active workspace pointer for subsequent steps.

## 10. Minimal Retry Context Format

Every retry attempt should use a reduced prompt envelope:

- Task objective (unchanged)
- current step id/title/instructions
- concise failure summary from prior attempt
- explicit constraints:
  - "do not re-run unchanged remediation"
  - "if test harness instability is detected, isolate cause before editing product code"

Do not include full previous logs/transcript; include only:

- failure signature
- last command summary
- changed-file list + diff hash

## 11. Checkpointing and Resume

### 11.1 Checkpoint Artifact

After each successful step, persist:

- `patches/steps/step-XXXX.patch` (already present)
- `state/steps/step-XXXX.json` containing:
  - step id/index
  - attempt count
  - resulting diff hash
  - changed files
  - timestamp

### 11.2 Resume Semantics

Resume target is a step index. Worker behavior:

- rebuild workspace from baseline
- replay checkpoints before target step
- continue execution at target step

This avoids dependency on in-memory process state and provides deterministic replay.

## 12. Worker Execution Changes

### 12.1 Step Attempt Loop

Replace one-pass step execution with bounded attempt loop per step:

```text
for step in steps:
  for attempt in 1..step_max_attempts:
    run step with timers
    if success: checkpoint and break
    classify failure
    if deterministic: fail step
    if no_progress_limit reached: escalate reset or fail
    if attempt budget remaining: self-heal retry
  if step failed: terminate execute stage
```

### 12.2 Timeouts

Apply wall-clock timeout around each runtime invocation. Apply idle timeout using live chunk callback timestamps.

### 12.3 Retryable Queue Failures

When self-heal budget is exhausted for retryable classes, fail job with `retryable=true` so queue backoff and `maxAttempts` can recover from worker-local bad state.

### 12.4 Cancel/Pause Compatibility

Self-heal must respect existing controls:

- cancellation always preempts retries
- pause blocks before next attempt
- takeover can request hard reset attempt immediately (operator-driven)

## 13. API and Schema Changes

### 13.1 Queue Control API

Extend task-run control request to support recovery actions:

- `pause`
- `resume`
- `takeover`
- `retry_step`
- `hard_reset_step`
- `resume_from_step`

Payload extension:

```json
{
  "action": "resume_from_step",
  "stepId": "tpl:speckit-orchestrate:1.0.0:05:f7739e20",
  "reason": "operator recovery"
}
```

### 13.2 Job Payload Live Control

Add optional `payload.liveControl.recovery` object consumed via heartbeat:

```json
{
  "liveControl": {
    "paused": false,
    "takeover": false,
    "recovery": {
      "action": "resume_from_step",
      "stepId": "...",
      "updatedAt": "..."
    }
  }
}
```

### 13.3 Optional Persistent State (v2)

Add `task_execution_state` persistence for robust cross-worker resume:

Option A (minimal migration):

- `agent_jobs.execution_state_json` (JSONB)

Option B (query-friendly):

- `task_step_attempts` table (attempt-level history)
- `task_step_checkpoints` table (successful replay points)

v1 can ship with event+artifact based state and add persistent tables in v2.

## 14. Event and Artifact Contract

New events:

- `task.step.attempt.started`
- `task.step.attempt.finished`
- `task.step.attempt.failed`
- `task.self_heal.triggered`
- `task.self_heal.escalated`
- `task.self_heal.exhausted`
- `task.resume.from_step`

Required event payload fields:

- `stepId`, `stepIndex`, `attempt`
- `failureClass`
- `failureSignature`
- `diffHash`
- `strategy` (`soft_reset` | `hard_reset`)

New artifacts:

- `state/steps/step-XXXX.json`
- `state/self_heal/attempt-XXXX.json`

## 15. Metrics

Emit StatsD metrics:

- `task.self_heal.attempts_total` (tags: class, strategy, outcome)
- `task.self_heal.recovered_total`
- `task.self_heal.exhausted_total`
- `task.step.duration_seconds`
- `task.step.idle_timeout_total`
- `task.step.wall_timeout_total`
- `task.step.no_progress_total`

## 16. Security and Redaction

Failure signatures and recovery summaries must be redacted with existing secret scrubber before event/artifact persistence.

Never persist raw tokens, env dumps, or unredacted command strings in self-heal state.

## 17. Rollout Plan

### Phase 1 (Worker-only, low risk)

- step attempt loop
- timeout/no-progress detection
- soft reset retries
- new events/metrics
- deterministic fail classification

### Phase 2 (Escalation + queue retry integration)

- hard reset strategy with checkpoint replay
- retryable queue fail for recoverable exhausted failures

### Phase 3 (Operator resume controls)

- control actions for `retry_step` / `resume_from_step`
- UI buttons and detail-state rendering

### Phase 4 (Persistent execution state)

- `execution_state_json` or dedicated attempt/checkpoint tables
- cross-worker robust resume

## 18. Test Plan

Unit tests:

- classifier mapping
- no-progress detection
- attempt budget enforcement
- timeout and idle-timeout handling
- checkpoint replay order

Integration tests:

- injected hanging command -> soft reset recovery
- repeated no-progress -> hard reset -> recovery
- exhausted recovery -> retryable queue fail and requeue
- cancel during self-heal attempt
- pause/resume around retry boundary

## 19. Operational Playbook

When a run appears stuck:

1. Inspect `task.self_heal.*` events for step/attempt trajectory.
2. If attempts are progressing, allow in-step recovery to finish.
3. If `no_progress` repeats and hard reset is available, trigger takeover and `hard_reset_step`.
4. If exhausted, rely on queue retry or cancel and resubmit from checkpointed step.

## 20. Open Questions

1. Should hard reset clone from `startingBranch` or last known clean merge-base against `origin/main`?
2. Should queue-level retry increment attempt for deterministic infrastructure classes (for example temporary package index outage) automatically?
3. Do we require persistent attempt/checkpoint tables in v1 for UI observability, or are events/artifacts sufficient?
4. Should `resume_from_step` be restricted to operator takeover mode only?
