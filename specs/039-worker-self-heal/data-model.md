# Data Model: Worker Self-Heal System (Phase 1)

## StepCheckpointState (artifact)

- **Location**: `state/steps/step-<index>.json`
- **Producer**: `moonmind/agents/codex_worker/worker.py` after successful step completion.
- **Fields**:
  - `schemaVersion` (string)
  - `stepId` (string)
  - `stepIndex` (integer)
  - `attempt` (integer)
  - `summary` (string|null)
  - `diffHash` (string|null)
  - `changedFiles` (array<string>)
  - `finishedAt` (ISO-8601 UTC)
- **Purpose**: Stable step checkpoint metadata for auditability and future replay phases.

## SelfHealAttemptArtifact (artifact)

- **Location**: `state/self_heal/attempt-<stepIndex>-<attempt>.json`
- **Producer**: `moonmind/agents/codex_worker/worker.py` on failed step attempts.
- **Fields**:
  - `schemaVersion` (string)
  - `stepId` (string)
  - `stepIndex` (integer)
  - `attempt` (integer)
  - `failureClass` (enum)
  - `failureSummary` (redacted string)
  - `failureSignature` (redacted string|null)
  - `failureSignatureHash` (string|null)
  - `strategy` (`soft_reset` or `queue_retry` in current phase)
  - `wallClockSeconds` (number)
  - `idleTimeoutTriggered` (boolean)
  - `diffHash` (string|null)
  - `changedFiles` (array<string>)
  - `timestamp` (ISO-8601 UTC)
- **Purpose**: Per-attempt forensic record for retry decisions and debugging.

## SelfHeal Retry Metadata (queue fail path)

- **Surface**: `WorkerExecutionResult.run_quality_reason`
- **Shape**:
  - `category`: `self_heal`
  - `code`: `step_retryable_exhausted`
  - `tags`: includes `retry` and `self_heal`
  - `stepId`, `stepIndex`
  - `details.failureClass`, `details.strategy`
- **Purpose**: Signal queue-level retry eligibility when in-step self-heal is exhausted for retryable classes.

## Runtime Attempt State (in-memory)

- **Model**: `StepAttemptState` in `self_heal.py`
- **Fields**:
  - `attempts_consumed`
  - `consecutive_no_progress`
  - `last_failure_signature`
  - `last_diff_hash`
- **Purpose**: Budget enforcement and no-progress detection across attempts.

## Metrics Tag Schema

- **Counters**:
  - `task.self_heal.attempts_total` with tags `{step, attempt, class, strategy, outcome}`
  - `task.self_heal.recovered_total` with tags `{step, attempt, class, strategy}`
  - `task.self_heal.exhausted_total` with tags `{step, attempt, class, strategy}`
- **Timers/Counters**:
  - `task.step.duration_seconds`
  - `task.step.wall_timeout_total`
  - `task.step.idle_timeout_total`
  - `task.step.no_progress_total`
- **Purpose**: Operational visibility and alerting consistency with existing StatsD pipeline.

## Deferred Data Surfaces

- `liveControl.recovery` command payload and associated control-event schema changes.
- Hard reset replay state transitions and `task.resume.from_step` artifacts/events.
