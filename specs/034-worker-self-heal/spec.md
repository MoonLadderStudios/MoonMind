# Feature Specification: Worker Self-Heal System (Phase 1 Alignment)

**Feature Branch**: `034-worker-self-heal`  
**Created**: 2026-02-20  
**Updated**: 2026-03-02  
**Status**: Implemented (Phase 1), with follow-up phases deferred  
**Input**: User description: "Update specs/034-worker-self-heal to make it align with the current state and strategy of the MoonMind project. Implement all of the updated tasks when done. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Strategy Alignment

MoonMind is shipping worker self-heal in phased increments:

1. Phase 1 (current): in-step bounded retries, timeout/no-progress detection, retryable exhaustion signaling to queue, artifacts/events/metrics/redaction.
2. Phase 2 (deferred): hard reset replay wired into active execution path.
3. Phase 3 (deferred): operator recovery actions (`retry_step`, `hard_reset_step`, `resume_from_step`) across API/service/dashboard.

This spec now covers Phase 1 as the executable contract.

## Source Requirement Alignment

| ID | Source | Requirement | Mapped Functional Requirement(s) | Phase 1 Status |
| --- | --- | --- | --- | --- |
| DOC-REQ-001 | `docs/WorkerSelfHealSystem.md` §6 | Detect stuck steps via wall timeout, idle timeout, and no-progress signatures. | FR-001, FR-002 | Implemented |
| DOC-REQ-002 | §8 | Enforce bounded self-heal budgets and defaults. | FR-001 | Implemented |
| DOC-REQ-003 | §7 | Classify failures into retryable vs deterministic classes. | FR-003 | Implemented |
| DOC-REQ-004 | §5-§9.1 | Soft reset retries in-place with bounded attempts. | FR-004 | Implemented |
| DOC-REQ-005 | §5-§9.2 | Hard reset workspace replay and resume. | FR-013 | Deferred (Phase 2) |
| DOC-REQ-006 | §10 | Minimal retry context envelope in retry prompts. | FR-014 | Deferred (Phase 2+ activation gate) |
| DOC-REQ-007 | §11 | Persist per-step checkpoint metadata and self-heal attempt artifacts. | FR-006 | Implemented |
| DOC-REQ-008 | §12 | Attempt loop respects cancel/pause/takeover and escalates retryable exhaustion to queue retries. | FR-005, FR-010 | Implemented |
| DOC-REQ-009 | §13 | API/live-control recovery actions (`retry_step`/`hard_reset_step`/`resume_from_step`). | FR-015, FR-016 | Deferred (Phase 3) |
| DOC-REQ-010 | §14 | Emit self-heal attempt/recovery events with key metadata. | FR-007 | Implemented (Phase 1 event set) |
| DOC-REQ-011 | §15 | Publish self-heal StatsD counters/timers. | FR-008 | Implemented |
| DOC-REQ-012 | §16 | Secret scrubbing for signatures/events/artifacts. | FR-009 | Implemented |
| DOC-REQ-013 | Task objective runtime scope guard | Deliverables must include runtime code updates plus validation tests (not docs/spec-only). | FR-011, FR-012 | Implemented |

## User Scenarios & Testing

### User Story 1 - Automatic Stuck-Step Recovery (Priority: P1)

Workers should recover retryable stuck/transient failures in-step without operator intervention.

**Independent Test**: Simulate a transient runtime failure for a step and verify attempt 1 fails, `task.self_heal.triggered` fires, attempt 2 succeeds, and the job completes.

**Acceptance Scenarios**:

1. **Given** a codex step exceeds wall timeout or idle timeout, **When** the attempt is classified retryable, **Then** the worker emits attempt failure telemetry and retries with `strategy=soft_reset` while budget remains.
2. **Given** repeated identical failure signatures with unchanged diff hash, **When** no-progress threshold is reached, **Then** worker emits escalation/exhaustion and stops in-step retries.
3. **Given** retryable exhaustion and remaining queue attempts, **When** the job fails, **Then** it is marked `retryable=true` via structured `run_quality_reason` category `self_heal`.

---

### User Story 2 - Deterministic Checkpoint Metadata (Priority: P2)

Successful steps must persist deterministic state artifacts that support future replay strategies.

**Independent Test**: Run a successful step and verify `state/steps/step-XXXX.json` contains step id/index, attempt, diff hash, and changed files; verify failed-step integrity/gate paths remove stale step-state artifacts.

**Acceptance Scenarios**:

1. **Given** a successful step, **When** artifacts are finalized, **Then** `state/steps/step-XXXX.json` is emitted and uploaded with redacted payload fields.
2. **Given** a failed step attempt, **When** self-heal metadata is written, **Then** `state/self_heal/attempt-XXXX-XXXX.json` is emitted with failure class, strategy, signature hash, and diff hash.

---

### User Story 3 - Operator Compatibility During Retries (Priority: P3)

Self-heal retries must not bypass existing operator controls.

**Independent Test**: Trigger self-heal retry conditions, apply pause/takeover, and verify worker blocks before launching the next attempt until resumed.

**Acceptance Scenarios**:

1. **Given** pause or takeover is active, **When** a step is about to start another attempt, **Then** worker waits for pause clear before continuing.
2. **Given** cancellation is requested during an attempt, **When** runtime cancellation propagates, **Then** worker exits with cancellation semantics instead of launching another retry.

### Edge Cases

- Deterministic validation/policy/repo failures fail fast (no in-step retry).
- Run-quality transcript integrity failures are returned directly and must not be wrapped in additional self-heal retries.
- Retryable self-heal exhaustion must preserve attempt artifacts and emit terminal `task.self_heal.exhausted`.
- Redaction must not over-mask short non-secret tokens in terminal error summaries.

## Requirements

### Functional Requirements

- **FR-001**: Worker shall execute each codex step through a bounded attempt loop controlled by `SelfHealConfig` budgets (`STEP_MAX_ATTEMPTS`, `STEP_TIMEOUT_SECONDS`, `STEP_IDLE_TIMEOUT_SECONDS`, `STEP_NO_PROGRESS_LIMIT`, `JOB_SELF_HEAL_MAX_RESETS`).
- **FR-002**: Worker shall detect retry-worthy stuck states through wall-timeout, idle-timeout, and repeated no-progress signature/diff combinations.
- **FR-003**: Worker shall classify failures into `transient_runtime`, `stuck_no_progress`, `deterministic_contract`, `deterministic_policy`, and `deterministic_repo` classes and use class-driven retry policy.
- **FR-004**: Retryable failures with remaining attempt budget shall trigger `soft_reset` strategy and start another attempt in the same workspace.
- **FR-005**: Non-retryable failures or retryable exhaustion shall terminate the step; retryable exhaustion shall emit structured self-heal retry metadata so queue-level attempts can continue.
- **FR-006**: Worker shall persist step checkpoint metadata (`state/steps/step-XXXX.json`) and per-attempt self-heal artifacts (`state/self_heal/attempt-XXXX-XXXX.json`).
- **FR-007**: Worker shall emit `task.step.attempt.started`, `task.step.attempt.finished`, `task.step.attempt.failed`, `task.self_heal.triggered`, `task.self_heal.escalated`, and `task.self_heal.exhausted` events with step/attempt metadata.
- **FR-008**: Worker shall emit StatsD metrics `task.self_heal.attempts_total`, `task.self_heal.recovered_total`, `task.self_heal.exhausted_total`, `task.step.duration_seconds`, `task.step.wall_timeout_total`, `task.step.idle_timeout_total`, and `task.step.no_progress_total`.
- **FR-009**: Failure signatures, event payloads, and persisted artifacts shall be redacted using worker secret-redaction controls before upload/persistence.
- **FR-010**: Pause/takeover/cancel controls shall be honored before each attempt launch.
- **FR-011**: Delivery shall include production runtime code changes for the Phase 1 self-heal loop, telemetry, artifact persistence, and retryable exhaustion signaling; docs/spec-only output is insufficient.
- **FR-012**: Delivery shall include validation tests that exercise retry recovery, retryable exhaustion, and deterministic fail-fast behavior.
- **FR-013**: [Deferred, Phase 2] Worker shall support hard reset workspace replay/resume in the active runtime path using `HardResetWorkspaceBuilder` and checkpoint replay.
- **FR-014**: [Deferred, Phase 2+] Retry attempts shall include a minimal retry-context envelope contract instead of reusing only base step instructions.
- **FR-015**: [Deferred, Phase 3] Task-run control APIs and live-control payloads shall support `retry_step`, `hard_reset_step`, and `resume_from_step`.
- **FR-016**: [Deferred, Phase 3] Runtime shall emit `task.resume.from_step` and `task.control.recovery.*` events for operator-driven recovery commands.

### Deferred Requirements (Future Phases)

- **DR-001**: Activate FR-013 (hard reset replay strategy in active runtime path).
- **DR-002**: Activate FR-015 (task-run control API/live-control recovery actions).
- **DR-003**: Activate FR-016 (operator recovery event lifecycle).
- **DR-004**: Activate FR-014 (minimal retry context envelope contract).

## Key Entities

- **StepAttemptState**: In-memory counter/signature state per step attempt loop.
- **SelfHealAttemptArtifact**: Persisted JSON artifact for each failed attempt classification/result.
- **StepCheckpointState**: Persisted JSON artifact for each successful step.
- **SelfHealRetryReason**: Structured `run_quality_reason` payload (`category=self_heal`) used to mark queue-retryable exhaustion.
- **WorkerMetricsTags**: Stable tags for self-heal counters/timers (`step`, `attempt`, `class`, `strategy`, `outcome`).

## Assumptions & Dependencies

- Queue retry/backoff is already implemented and honors `retryable=true` on failure transitions.
- Existing pause/resume/takeover controls remain the only supported task-run control actions in current phase.
- Artifact upload/storage path conventions under `var/artifacts/agent_jobs/<run_id>/` remain unchanged.
- No database schema changes are required for Phase 1 self-heal delivery.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Retryable transient/self-heal failures can recover within in-step attempt budget in unit tests (`test_run_once_self_heal_soft_resets_retryable_step_and_recovers`).
- **SC-002**: Retryable exhaustion marks queue failure as retryable and emits terminal self-heal event in unit tests (`test_run_once_self_heal_exhaustion_marks_retryable_failure`).
- **SC-003**: Deterministic failures do not loop and fail after first attempt (`test_run_once_self_heal_deterministic_failure_does_not_retry`).
- **SC-004**: Full unit suite passes via `./tools/test_unit.sh` after implementation updates.
- **SC-005**: Implementation diff for this feature includes production runtime code changes under worker execution paths and related runtime telemetry/artifact handling.
