# Research: Live Task Handoff

## Decision 1: Persist live-session lifecycle state in dedicated task-run table
- Decision: Add `task_run_live_sessions` with one row per `agent_jobs` task run to track provider, status, attach metadata, heartbeat, TTL, and terminal diagnostics.
- Rationale: Keeps lifecycle and access metadata queryable/auditable without overloading queue event payloads.
- Alternatives considered:
  - Store all live-session state only in job payload JSON: rejected due to weak schema/audit guarantees.
  - Store only in transient worker memory: rejected because dashboard/API need durable state.

## Decision 2: Persist operator controls as append-only audit events
- Decision: Add `task_run_control_events` to record pause/resume/takeover/grant/revoke/send-message actions.
- Rationale: Supports explicit operator traceability and incident review.
- Alternatives considered:
  - Reuse `agent_job_events` only: rejected because actor/audit metadata needs first-class structure.

## Decision 3: Expose a dedicated task-runs API surface
- Decision: Add `/api/task-runs/{id}/...` endpoints for live-session lifecycle, control actions, and operator messages.
- Rationale: Separates task-run handoff concerns from generic queue job CRUD while retaining queue auth/error mapping.
- Alternatives considered:
  - Extend `/api/queue/jobs/{id}` with many live endpoints: rejected due to API surface coupling and clarity loss.

## Decision 4: Keep RO attach visible and protect RW attach at rest
- Decision: Store RO attach data plain for display, keep RW attach/web RW encrypted at rest and only reveal via explicit grant-write endpoint.
- Rationale: Matches RO-first security model while enabling time-bounded takeover flows.
- Alternatives considered:
  - Expose RW endpoints directly in GET response: rejected by least-privilege requirements.

## Decision 5: Implement worker bootstrap as best-effort tmate manager
- Decision: Add worker-side `_ensure_live_session_started` and `_teardown_live_session` paths that report `starting`/`ready`/`error`/`ended` and never block baseline task execution.
- Rationale: Live handoff is additive; task execution must proceed even if tmate cannot start.
- Alternatives considered:
  - Fail task run if live session fails: rejected by documented failure-mode expectations.

## Decision 6: Implement pause/resume/takeover via heartbeat-visible control payload
- Decision: Persist control flags under `job.payload.liveControl` and have worker heartbeat + safe checkpoints honor pause state.
- Rationale: Reuses existing heartbeat path for coordination without introducing a new control channel.
- Alternatives considered:
  - Direct signals/process control only: rejected as too brittle for routine soft-pause behavior.

## Decision 7: Surface live handoff controls in queue detail dashboard
- Decision: Extend queue detail UI with a Live Session card for status/attach details, grant/revoke, pause/resume/takeover, and operator messaging.
- Rationale: Keeps operator workflow centralized in existing queue task detail.
- Alternatives considered:
  - Separate standalone handoff page: rejected due to context switching and duplicated state handling.

## Decision 8: Make live-session runtime configurable via environment
- Decision: Add settings/env for enablement, provider, session TTL, RW grant TTL, web exposure, relay host, and concurrency limits.
- Rationale: Allows safe rollout and environment-specific behavior without code changes.
- Alternatives considered:
  - Hard-code policy values: rejected due to operational inflexibility.

## Decision 9: Validate with targeted router/service/worker/dashboard/config tests plus full unit suite
- Decision: Add/extend unit coverage for new API endpoints, settings parsing, worker runtime behavior, and dashboard endpoint wiring; run full `./tools/test_unit.sh`.
- Rationale: Changes span API, worker, persistence, and UI integration boundaries; broad regression confidence is required.
- Alternatives considered:
  - Manual-only validation: rejected due to high regression risk across multiple subsystems.
