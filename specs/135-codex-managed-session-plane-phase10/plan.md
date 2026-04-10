# Implementation Plan: codex-managed-session-plane-phase10

**Branch**: `135-codex-managed-session-plane-phase10` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/135-codex-managed-session-plane-phase10/spec.md`

## Summary

Implement Phase 10 by making Codex managed-session steps reuse the existing managed-run observability model. The session adapter will persist a normal `ManagedRunRecord` for each session-backed step run using durable stdout/stderr/diagnostics refs already produced by the managed-session publication path, allowing the current `/api/task-runs/{taskRunId}/...` APIs to remain artifact-first without inventing a parallel session observability surface.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: existing `ManagedRunRecord` / `ManagedRunStore`, `CodexSessionAdapter`, task-runs observability router, managed-session publication metadata
**Testing**: focused pytest suites plus final verification via `./tools/test_unit.sh`
**Project Type**: Temporal backend runtime adapter and observability API reuse
**Constraints**: keep observability artifact-first; reuse the existing task-runs API; do not add live-terminal semantics; do not require live session-container access to inspect completed runs

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. This reuses the established observability plane instead of adding a Codex-specific side channel.
- **II. One-Click Agent Deployment**: PASS. No new deployment dependency or operator setup is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The shape is the generic managed-run observability record, not a Codex-only API.
- **IV. Own Your Data**: PASS. Observability comes from durable artifacts and persisted records rather than session liveness.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The implementation extends the current record contract instead of introducing terminal scraping or UI-only heuristics.
- **VII. Powerful Runtime Configurability**: PASS. No new runtime knobs are needed.
- **VIII. Modular and Extensible Architecture**: PASS. The change stays inside adapter and observability boundaries and remains reusable for later managed-session runtimes.
- **IX. Resilient by Default**: PASS. Operators can inspect completed runs after the session container is gone.
- **XI. Spec-Driven Development**: PASS. Phase 10 artifacts are defined before code changes.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. The desired-state observability behavior already lives in canonical docs; this plan only covers the implementation slice.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The work reuses the current path directly and adds no compatibility alias.

## Research

- The task-runs observability router already serves artifact-backed managed-run records and does not need a new session-specific endpoint.
- Managed-session publication already yields durable stdout/stderr/diagnostics refs, so the missing Phase 10 behavior is wiring those refs into a persisted `ManagedRunRecord`.
- `CodexSessionAdapter` currently keeps step execution state in memory, which is enough for result delivery inside one workflow turn but not enough for the API-side observability record expected by Mission Control.
- Live streaming is already optional in the observability model, so Phase 10 can intentionally set `liveStreamCapable=false` for session-backed runs until a later live-follow slice exists.

## Project Structure

- Update `moonmind/workflows/adapters/codex_session_adapter.py` to persist session-backed `ManagedRunRecord` entries.
- Reuse `moonmind/schemas/agent_runtime_models.py` / `ManagedRunStore` without adding a new observability schema.
- Extend `tests/unit/workflows/adapters/test_codex_session_adapter.py` to verify record persistence.
- Extend `tests/unit/api/routers/test_task_runs.py` to verify the existing observability summary contract works for a session-backed record.

## Data Model

- **Session-Backed ManagedRunRecord**
  - `run_id`: step `taskRunId`
  - `workflow_id`: producing `MoonMind.AgentRun` workflow id
  - `agent_id`: managed agent id (Codex)
  - `runtime_id`: `codex_cli`
  - `status`: terminal managed-run status
  - `workspace_path`: task-scoped workspace root used for artifact-backed fallbacks
  - `stdout_artifact_ref`, `stderr_artifact_ref`, `diagnostics_ref`: copied from durable session publication metadata
  - `live_stream_capable`: `false` for this phase

## Contracts

- Existing API surface reused: [contracts/observability-summary-session-backed.md](./contracts/observability-summary-session-backed.md)

## Implementation Plan

1. Add failing tests proving session-backed runs do not currently persist a managed-run observability record and that the existing observability summary contract should work once such a record exists.
2. Update `CodexSessionAdapter` to persist a `ManagedRunRecord` whenever it finalizes a session-backed step result.
3. Copy durable stdout/stderr/diagnostics refs from session publication metadata into the managed-run record and mark live streaming unavailable for this path.
4. Verify the task-runs observability summary route serves the persisted session-backed record without router changes or live-session dependencies.
5. Run focused tests, run Spec Kit scope validation, rerun the full unit suite, and mark completed tasks in `tasks.md`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py`
2. `SPECIFY_FEATURE=135-codex-managed-session-plane-phase10 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `SPECIFY_FEATURE=135-codex-managed-session-plane-phase10 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Execute a Codex managed-session step and confirm Mission Control can load `/api/task-runs/{taskRunId}/observability-summary`.
2. Confirm stdout, stderr, and diagnostics remain readable after the session container exits.
3. Confirm the observability summary does not advertise live streaming or terminal attach for the managed-session path.
