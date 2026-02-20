# Data Model: Worker Self-Heal System

## StepCheckpointState (artifact)
- **Location**: `state/steps/step-<index>.json` next to the existing `patches/steps/step-<index>.patch` artifact.
- **Fields**:
  - `stepId` (string) – canonical step identifier from `task.steps[*].id` or fallback `step-{n}`.
  - `stepIndex` (integer) – zero-based index in `resolved_steps`.
  - `attempt` (integer) – cumulative attempt count when the step succeeded.
  - `diffHash` (string) – sha256 of the normalized patch content.
  - `changedFiles` (array<string>) – ordered list of files touched by the patch (relative paths, unique).
  - `summary` (string|null) – runtime-provided success summary, truncated/ scrubbed.
  - `finishedAt` (ISO-8601 string) – UTC timestamp when checkpoint was written.
- **Purpose**: Allows deterministic replay during hard resets or operator `resume_from_step` by pairing metadata with the existing patch artifact; also feeds requirements-traceability and dashboard views.

## SelfHealAttemptArtifact (artifact)
- **Location**: `state/self_heal/attempt-<stepIndex>-<attempt>.json` (mirrors the controller’s attempt key).
- **Fields**:
  - `stepId`, `stepIndex`, `attempt` – same semantics as above.
  - `failureClass` (`transient_runtime`, `stuck_no_progress`, `deterministic_contract`, `deterministic_policy`, `deterministic_repo`).
  - `failureSignature` (string) – scrubbed + truncated hashable signature used for no-progress detection.
  - `strategy` (`soft_reset`, `hard_reset`, `queue_retry`, `operator_request`).
  - `wallClockSeconds`, `idleSeconds` – measured durations.
  - `retryContext` – minimal prompt payload (objective, step metadata, failure summary, diff hash, changed files) to prove DOC-REQ-006.
  - `events` (array) – ordered list of event ids emitted for this attempt (`task.step.attempt.started`, etc.) for audit.
- **Purpose**: Provides durable observability of how each attempt was classified, which strategy ran, and what payload was sent to the runtime.

## LiveControlRecovery Payload (queue `agent_jobs.payload.liveControl`)
- **Shape**:
  ```json
  {
    "paused": false,
    "takeover": false,
    "updatedAt": "2026-02-20T17:04:32Z",
    "recovery": {
      "action": "resume_from_step",      // also accepts retry_step, hard_reset_step
      "stepId": "tpl:speckit-orchestrate:1.0.0:05:b32d2f66",
      "strategy": "hard_reset_step",     // optional explicit strategy override
      "requestedBy": "user-uuid",
      "reason": "operator recovery",
      "updatedAt": "2026-02-20T17:04:32Z"
    }
  }
  ```
- **Behavior**: API service persists this blob inside `agent_jobs.payload`, repository methods validate actions, and workers clear the `recovery` object once the request has been honored (emitting `task.control.recovery.ack`).

## TaskRunControlEvent Metadata
- **New actions**: `retry_step`, `hard_reset_step`, `resume_from_step` (in addition to `pause`, `resume`, `takeover`, `send_message`).
- **Metadata**: `{ "action": <string>, "stepId": <string|null>, "strategy": <string|null>, "reason": <string|null> }` so dashboards can render operator intent and audit logs remain structured.

## Retry Prompt Context Envelope
- **Fields embedded into the minimal retry instruction**:
  - `objective` (string) – task instructions from canonical payload.
  - `step` (object) – `{ id, index, title }` for the current step.
  - `failureSummary` (string) – single-paragraph summary trimmed/scrubbed.
  - `constraints` (array<string>) – documented constraints (do not re-run unchanged remediation, isolate flaky tests, etc.).
  - `changedFiles` (array<string>) – subset of `StepCheckpointState.changedFiles` relevant to the attempt.
  - `diffHash` (string) – matches the hash stored in artifacts.
- **Usage**: Included in the JSON the worker ships to Codex/Gemini/Claude on every retry, satisfying DOC-REQ-006 without replaying entire transcripts.

## Metrics Tags
- **Counters** (`task.self_heal.attempts_total`, `task.self_heal.recovered_total`, `task.self_heal.exhausted_total`) carry tags `{class, strategy, step}`.
- **Timers** (`task.step.duration_seconds`, `task.step.wall_timeout_total`, `task.step.idle_timeout_total`, `task.step.no_progress_total`) carry `{step, attempt}`.
- **Storage**: Metrics are ephemeral but the tag schema is part of the contract with downstream monitoring dashboards.
