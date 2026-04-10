# Implementation Plan: live-logs-session-timeline

**Branch**: `138-live-logs-session-timeline` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/138-live-logs-session-timeline/spec.md`

## Summary

Implement the Phase 0 and Phase 1 slice of the session-aware Live Logs migration by: 1) replacing the outdated tmp implementation tracker with the new session-aware rollout baseline, 2) wiring a dedicated session-timeline rollout flag into settings and the task-dashboard boot payload, and 3) introducing one canonical structured observability event contract plus durable JSONL history artifacts referenced from `ManagedRunRecord`. The backend will continue to use the existing spool/SSE transport, but the persisted and API-facing source-of-truth becomes one normalized event model that covers stdout, stderr, system, and session rows.

## Technical Context

**Language/Version**: Python 3.12, TypeScript for existing dashboard boot payload types  
**Primary Dependencies**: FastAPI task-runs router, Pydantic schemas, `ManagedRunRecord` / `ManagedRunStore`, `RuntimeLogStreamer`, spool transport, Mission Control runtime-config boot payload  
**Storage**: file-backed managed-run JSON records plus artifact-backed JSONL observability history  
**Testing**: pytest unit tests plus final verification via `./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind services with artifact-first managed-run observability  
**Project Type**: backend runtime observability contract + API/config wiring  
**Performance Goals**: preserve current live-log behavior while making historical timeline reconstruction deterministic for completed runs  
**Constraints**: keep spool/SSE transport, keep artifact-first semantics, avoid provider-native payloads, keep existing live-log consumers readable during migration, do not break non-session-managed runs  
**Scale/Scope**: Phase 0 and Phase 1 only; no frontend timeline renderer changes beyond boot-payload flag exposure

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The implementation keeps MoonMind-owned observability contracts instead of leaking provider-specific events into the browser.
- **II. One-Click Agent Deployment**: PASS. No new infrastructure dependency is introduced; the change stays within existing file/artifact storage and API surfaces.
- **III. Avoid Vendor Lock-In**: PASS. The canonical event model is runtime-neutral even though Codex session fields are optionally populated.
- **IV. Own Your Data**: PASS. Structured observability history is persisted as MoonMind-owned JSONL artifacts and run metadata.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill-system behavior changes are introduced.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The main change is the normalized event contract and durable event ref, not new ad hoc parsing or UI heuristics.
- **VII. Powerful Runtime Configurability**: PASS. The rollout boundary is expressed as a first-class feature flag setting and boot payload field.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within config, schema, transport, runtime persistence, and task-runs observability boundaries.
- **IX. Resilient by Default**: PASS. Structured history persistence must remain non-fatal to runtime control, and ended runs become more observable, not less.
- **XI. Spec-Driven Development**: PASS. Spec, plan, tasks, and tests are defined before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs stay declarative; only `docs/tmp/009-LiveLogsPlan.md` is rewritten as the migration tracker.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. We will replace old-only internal assumptions directly rather than adding a second long-lived event model.

## Research

- `docs/ManagedAgents/LiveLogs.md` already defines the target `RunObservabilityEvent` shape and explicitly keeps spool/SSE as the live transport boundary.
- The current code already emits session-aware rows through `RuntimeLogStreamer.emit_observability_event`, but the canonical persisted/API contract is still centered on `LiveLogChunk` and diagnostics fallback.
- The task-runs router already has `GET /api/task-runs/{id}/observability/events`, so the Phase 1 gap is not a brand-new endpoint; it is the durable canonical event contract and event-history persistence.
- `ManagedRunRecord` does not yet persist `observability_events_ref` or inline session snapshot fields, so history and summary still depend on spool files and separate session-store lookups.
- The dashboard boot payload already exposes `logStreamingEnabled` through ad hoc env parsing; Phase 0 can add a dedicated session-timeline rollout field without changing the current log-streaming gate.
- To respect the repo’s compatibility policy, payload compatibility should come from the new canonical event shape remaining a superset of the current live-log fields, not from maintaining a second old-only internal contract.

## Project Structure

- Update `docs/tmp/009-LiveLogsPlan.md` to the new session-aware rollout tracker.
- Extend `moonmind/config/settings.py` with a session-timeline rollout flag and expose it from `api_service/api/routers/task_dashboard_view_model.py`.
- Replace chunk-centric internal event typing in `moonmind/schemas/agent_runtime_models.py`, `moonmind/observability/transport.py`, `moonmind/workflows/temporal/runtime/log_streamer.py`, and `api_service/api/routers/task_runs.py`.
- Extend `moonmind/workflows/temporal/runtime/store.py`, `moonmind/workflows/temporal/runtime/supervisor.py`, and `moonmind/workflows/temporal/runtime/managed_session_supervisor.py` to persist durable event-history refs and latest session snapshot fields on `ManagedRunRecord`.
- Add or update tests in `tests/unit/services/temporal/runtime/test_log_streamer.py`, `tests/unit/services/temporal/runtime/test_store.py`, `tests/unit/services/temporal/runtime/test_supervisor_live_output.py`, `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`, and `tests/unit/api/routers/test_task_runs.py`.

## Data Model

- See [data-model.md](./data-model.md) for the canonical event, managed-run record extensions, and rollout-flag model.

## Contracts

- [contracts/run-observability-event.md](./contracts/run-observability-event.md)
- [contracts/task-dashboard-session-timeline-flag.md](./contracts/task-dashboard-session-timeline-flag.md)

## Implementation Plan

1. Add failing tests for the Phase 0 and Phase 1 contract changes:
   - settings + boot payload expose the session-timeline rollout flag,
   - the canonical event model supports the full stream/kind/session field set,
   - managed-run persistence stores a durable observability-history ref and session snapshot fields,
   - task-runs history retrieval prefers structured event history when present.
2. Replace `docs/tmp/009-LiveLogsPlan.md` with the new session-aware rollout tracker from the user’s plan and align rollout wording with the shipped baseline.
3. Introduce the session-timeline rollout setting in `FeatureFlagsSettings` and expose both rollout scope and enabled/disabled state from the task-dashboard runtime config.
4. Introduce `RunObservabilityEvent` as the canonical internal contract, update transport and runtime streamer code to publish/read that model, and keep payload compatibility by preserving the current event fields on the wire.
5. Persist structured observability history as `observability.events.jsonl` artifacts during run/session publication and store the resulting ref plus latest session snapshot fields on `ManagedRunRecord`.
6. Update summary/history readers to surface the new record fields and prefer the durable event-history ref before falling back to spool/artifact synthesis.
7. Run focused tests, Spec Kit scope validation, the full unit suite, then mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_log_streamer.py tests/unit/services/temporal/runtime/test_store.py tests/unit/services/temporal/runtime/test_supervisor_live_output.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/api/routers/test_task_runs.py`
2. `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Inspect the boot payload and confirm `liveLogsSessionTimelineEnabled` and its rollout scope are present without removing `logStreamingEnabled`.
2. Execute or simulate a managed run that emits stdout, stderr, system, and session rows; confirm a durable `observability.events.jsonl` artifact is written and referenced by the managed-run record.
3. Load `/api/task-runs/{taskRunId}/observability-summary` and `/api/task-runs/{taskRunId}/observability/events` for a completed run and confirm they surface the durable session snapshot and structured event history without requiring live streaming.
