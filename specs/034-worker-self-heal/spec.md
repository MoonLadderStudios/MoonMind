# Feature Specification: Worker Self-Heal System

**Feature Branch**: `034-worker-self-heal`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "Implement the worker self heal system described in docs/WorkerSelfHealSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| ID | Source | Requirement |
| --- | --- | --- |
| DOC-REQ-001 | docs/WorkerSelfHealSystem.md §6 | Detect step stuck states via wall-clock timeout, idle timeout, and repeated no-progress signatures so workers trigger recovery deterministically. |
| DOC-REQ-002 | §8 | Enforce bounded self-heal budgets (`step_max_attempts`, wall/idle timeouts, no-progress limit, job hard reset budget) with the documented defaults. |
| DOC-REQ-003 | §7 | Classify failures into transient/stuck/deterministic buckets that determine whether retries are allowed. |
| DOC-REQ-004 | §5-§9.1 | Provide soft reset retries that terminate the runtime, restart the same step in-place, and feed a compact retry prompt. |
| DOC-REQ-005 | §5-§9.2 | Provide a hard reset strategy that rebuilds the workspace from baseline, reapplies checkpoints, and reruns the failing step when soft resets cannot regain progress. |
| DOC-REQ-006 | §10 | Supply minimal retry context (objective, step metadata, failure summary, constraints, diff hash, changed files) instead of full transcripts for each retry attempt. |
| DOC-REQ-007 | §11 | Persist checkpoint artifacts (`patches/steps/*.patch`, `state/steps/*.json`) per successful step and support resume-from-step semantics. |
| DOC-REQ-008 | §12 | Execute each step within a bounded attempt loop that respects cancel/pause/takeover controls and escalates exhausted recoveries to queue-level retries. |
| DOC-REQ-009 | §13 | Extend queue control APIs and heartbeat live-control payloads with recovery actions such as `retry_step`, `hard_reset_step`, and `resume_from_step`. |
| DOC-REQ-010 | §14 | Emit the specified task events and record recovery artifacts with `stepId`, `stepIndex`, `attempt`, `failureClass`, `failureSignature`, `diffHash`, and `strategy`. |
| DOC-REQ-011 | §15 | Publish StatsD metrics for self-heal attempts, recoveries, exhaustion, durations, and timeout counters with class/strategy tags. |
| DOC-REQ-012 | §16 | Scrub secrets from failure signatures, events, and artifacts before persistence to avoid leaking credentials. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic Stuck-Step Recovery (Priority: P1)

Queue operators need the worker to self-detect stuck steps, bound retries, and resume work without manual intervention so critical orchestrations finish reliably.

**Why this priority**: Eliminates the top production blocker (silent looping) that consumes runtime credit and delays deliverables.

**Independent Test**: Inject a transient CLI hang into a single step and verify the worker trips the idle timeout, restarts via soft reset, and completes the step without human input.

**Acceptance Scenarios**:

1. **Given** an executing step with a 900-second wall-clock limit, **When** the runtime exceeds that limit, **Then** the worker records a stuck detection event, restarts the step with a fresh runtime, and logs the self-heal attempt.
2. **Given** repeated identical failure signatures with unchanged diffs, **When** the no-progress threshold is met, **Then** the worker escalates to the configured reset strategy instead of retrying endlessly.

---

### User Story 2 - Deterministic Checkpoint Resume (Priority: P2)

Operators must trust that successful work persists and that recovery resumes from the last completed step whenever a hard reset or queue retry occurs.

**Why this priority**: Without deterministic checkpoints, retries risk losing progress or replaying incorrect edits, undermining confidence in the automation.

**Independent Test**: Complete steps 1-3, fail step 4 repeatedly until hard reset triggers, and verify the worker rebuilds the workspace by replaying saved patches before re-attempting step 4.

**Acceptance Scenarios**:

1. **Given** steps 1-2 already succeeded, **When** the system performs a hard reset targeting step 3, **Then** it recreates the repo from the baseline ref, reapplies recorded patches for steps 1-2, and resumes at step 3 without missing files.
2. **Given** an operator issues a `resume_from_step` command for step 5, **When** the worker processes the live control payload, **Then** it rebuilds a workspace with checkpoints up to step 4 and continues at the requested step.

---

### User Story 3 - Operator-Controlled Recovery Actions (Priority: P3)

Operators need to pause, takeover, or force specific recovery actions so they can manage production incidents where automation must be coordinated with manual decisions.

**Why this priority**: Fast operator commands reduce downtime when automated recovery is insufficient, preserving SLAs and enabling safe experimentation with hard resets.

**Independent Test**: Pause a task mid-retry, issue `hard_reset_step` via the control API, and confirm the worker resumes using the requested strategy once unpaused.

**Acceptance Scenarios**:

1. **Given** a running retry attempt, **When** an operator pauses the task, **Then** the worker stops before launching the next attempt and only resumes when the pause flag clears.
2. **Given** self-heal exhaustion for a retryable class, **When** the worker marks the failure `retryable=true`, **Then** the queue re-enqueues the job with backoff so a fresh worker can continue.

---

Additional user stories can be captured if new personas emerge during planning.

### Edge Cases

- Deterministic configuration or policy failures must terminate immediately without consuming retry budget even if the runtime appears transient.
- Cancel or pause signals arriving during checkpoint replay must halt the replay and leave state consistent for the next resume.
- Hard reset attempts that cannot recreate recorded patches (for example missing files) must fail loudly with the original attempt logs preserved for debugging.
- Recovery metrics/events must still be emitted when the worker process crashes mid-attempt to avoid observability blind spots.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The worker shall detect stuck steps using wall-clock timeout, idle timeout, and no-progress signature comparisons, triggering self-heal as soon as any configured threshold is exceeded (maps to DOC-REQ-001).
- **FR-002**: The worker shall enforce configurable self-heal budgets with defaults of `step_max_attempts=3`, `step_timeout_seconds=900`, `step_idle_timeout_seconds=300`, `step_no_progress_limit=2`, and `job_self_heal_max_resets=1`, exposing these via runtime configuration (maps to DOC-REQ-002).
- **FR-003**: Each failed attempt shall be classified into `transient_runtime`, `stuck_no_progress`, or deterministic (`deterministic_contract`, `deterministic_policy`, `deterministic_repo`), and only retryable classes may trigger in-step retries (maps to DOC-REQ-003).
- **FR-004**: For retryable classes with budget remaining, the worker shall perform a soft reset: terminate the current runtime, start a fresh runtime for the same step in the same workspace, and send a compact retry prompt (maps to DOC-REQ-004).
- **FR-005**: When the no-progress limit or reset budget is reached yet the failure remains retryable, the worker shall perform a hard reset by recreating the workspace from the start ref, replaying checkpoints up to the prior step, and rerunning the failing step (maps to DOC-REQ-005).
- **FR-006**: All retry prompts shall include only the minimal context package (task objective, current step metadata, summarized failure, constraints, changed-file list, diff hash) instead of full transcripts (maps to DOC-REQ-006).
- **FR-007**: After each successful step, the worker shall persist patch artifacts and `state/steps/step-XXXX.json` metadata containing step index, attempt count, diff hash, changed files, and timestamps, enabling deterministic resume_from_step reconstruction (maps to DOC-REQ-007).
- **FR-008**: The step execution loop shall respect cancel, pause, and takeover signals before launching another attempt and flag exhausted retryable failures as `retryable=true` so queue-level `maxAttempts` can continue recovery (maps to DOC-REQ-008).
- **FR-009**: The queue control API and heartbeat payload shall expose recovery actions (`pause`, `resume`, `takeover`, `retry_step`, `hard_reset_step`, `resume_from_step`) and the worker shall honor them promptly (maps to DOC-REQ-009).
- **FR-010**: The system shall emit the new recovery events (`task.step.attempt.*`, `task.self_heal.*`, `task.resume.from_step`) and store accompanying artifacts that capture `stepId`, `stepIndex`, `attempt`, `failureClass`, `failureSignature`, `diffHash`, and the chosen strategy (maps to DOC-REQ-010).
- **FR-011**: StatsD metrics shall be produced for attempts, recoveries, exhausted retries, and per-step timers using the names documented in `docs/WorkerSelfHealSystem.md` (for example `task.self_heal.attempts_total`, `task.self_heal.recovered_total`, `task.self_heal.exhausted_total`, `task.step.duration_seconds`, `task.step.wall_timeout_total`, `task.step.idle_timeout_total`, and `task.step.no_progress_total`) with class/strategy tags for observability (maps to DOC-REQ-011).
- **FR-012**: Failure signatures, recovery summaries, and stored artifacts shall pass through the existing secret scrubber so no credentials or sensitive command strings are persisted (maps to DOC-REQ-012).
- **FR-013**: Production-ready automated tests (unit coverage for classifiers/detection/budgets and integration coverage for recovery flows listed in the Worker Self-Heal test plan) must accompany runtime code changes to validate the above behaviors, satisfying the runtime scope guard.

### Key Entities *(include if feature involves data)*

- **StepAttemptState**: Logical record describing one attempt of a step (step id, attempt number, failure class, timers, diff hash) persisted in `state/steps/*.json` and emitted via events.
- **SelfHealStrategy**: Encapsulates the logic for the next attempt (`soft_reset`, `hard_reset`, `queue_retry`) with associated budgets and reasons.
- **CheckpointArtifact**: Patch and metadata bundles replayed to reconstruct workspaces ahead of a resume or hard reset.
- **RecoveryControlCommand**: Operator-initiated command received through queue control or heartbeat live-control payloads, including action, target step, timestamps, and source actor.
- **TelemetryEnvelope**: Aggregated metrics and events (StatsD, task events) capturing counts and durations for reporting and alerting.

## Assumptions & Dependencies

- Celery queue infrastructure, RabbitMQ broker connectivity, and PostgreSQL persistence remain available so retryable jobs can re-enqueue after self-heal exhaustion.
- Secret scrubbing services already used by MoonMind events remain in place and can be extended to cover the new recovery artifacts without new compliance approvals.
- Existing patch artifact system (`patches/steps/*.patch`) is accurate and can be replayed deterministically; this feature extends it but does not re-architect diff generation.
- Operator tooling (CLI/API) can surface the new recovery controls and events without additional authentication scopes beyond what pause/resume already require.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 95% of `transient_runtime` or `stuck_no_progress` failures recover within the configured `step_max_attempts` without manual intervention, as proven by integration tests and metrics.
- **SC-002**: 100% of deterministic failures emit `task.self_heal.exhausted` (or equivalent) events within 60 seconds of detection, enabling operators to react with clear terminal reasons.
- **SC-003**: Resume-from-step operations rebuild a workspace and restart execution in under 5 minutes for runs with ≤10 completed steps, ensuring checkpoint replay remains practical.
- **SC-004**: StatsD metrics for self-heal attempts, recoveries, and timeouts are produced for every step attempt, and automated tests assert that counters increment for the scenarios enumerated in the test plan.
