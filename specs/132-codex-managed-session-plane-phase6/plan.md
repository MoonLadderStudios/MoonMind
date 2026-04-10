# Implementation Plan: codex-managed-session-plane-phase6

**Branch**: `132-codex-managed-session-plane-phase6` | **Date**: 2026-04-06 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/132-codex-managed-session-plane-phase6/spec.md`

## Summary

Extend the Codex managed-session plane from a launch/controller slice into a durable supervised session slice. This phase adds a session record model and store, a session-level supervisor that watches session spool files and publishes artifact refs, controller wiring that persists and serves those records, and worker startup reconciliation that reattaches or degrades active sessions after restart.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: existing stdlib asyncio/pathlib/json facilities, current managed runtime `RuntimeLogStreamer`, Temporal runtime worker bootstrap, Docker-backed managed-session controller from Phase 4
**Testing**: focused pytest suites plus final verification via `./tools/test_unit.sh`
**Project Type**: Temporal backend runtime and worker bootstrap
**Constraints**: preserve existing managed-session activity contracts; keep logs/diagnostics artifact-first; do not route through `ManagedRuntimeLauncher`; use per-session durable metadata for reconciliation

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Temporal and worker services remain the orchestrator; the container still does not own lifecycle truth.
- **II. One-Click Agent Deployment**: PASS. The phase reuses the existing Docker/session image assumptions and adds no new deployment prerequisite.
- **III. Avoid Vendor Lock-In**: PASS. The slice is Codex-specific, but constrained behind session store/supervisor/controller boundaries.
- **IV. Own Your Data**: PASS. Durable session records plus artifact refs become the authoritative presentation and recovery surface.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The phase adds explicit session record models and worker-boundary tests instead of implicit controller state.
- **VII. Powerful Runtime Configurability**: PASS. Store roots, artifact roots, and Docker details remain configurable through existing worker settings/env.
- **VIII. Modular and Extensible Architecture**: PASS. Session supervision is isolated beside the existing managed-run store/supervisor rather than merged into workflow code.
- **IX. Resilient by Default**: PASS. Worker startup reconciliation and degraded-state handling are first-class acceptance criteria.
- **XI. Spec-Driven Development**: PASS. This feature slice defines the Phase 6 implementation before code changes land.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Phase sequencing stays under `specs/`; canonical docs need only targeted alignment if behavior wording changes.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The phase introduces one clear session supervision path and avoids compatibility wrappers around the older run-level store.

## Research

- The existing `RuntimeLogStreamer` already provides durable stdout/stderr artifact creation, diagnostics bundle generation, and bounded annotation support; Phase 6 should reuse that artifact path instead of inventing a parallel format.
- Phase 4 already mounts a task-scoped artifact spool path into the session container, which provides a restart-safe host-visible boundary for session observability without depending on a live container shell.
- Worker startup already performs managed-run reconciliation through `ManagedRunSupervisor.reconcile()`, so session reconciliation should follow the same bootstrap pattern and remain outside workflow code.

## Project Structure

- Extend `moonmind/schemas/managed_session_models.py` with a durable session record model and status values.
- Add a session record store under `moonmind/workflows/temporal/runtime/`.
- Add a session supervisor under `moonmind/workflows/temporal/runtime/` that reuses `RuntimeLogStreamer`.
- Update `moonmind/workflows/temporal/runtime/codex_session_runtime.py` to emit append-only session spool files for stdout/stderr-oriented supervision.
- Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` to persist records, delegate supervision, and source summary/publication responses from durable records.
- Update `moonmind/workflows/temporal/worker_runtime.py` to construct the new session store/supervisor and run reconciliation during startup.

## Data Model

- **CodexManagedSessionRecord**
  - Required: `session_id`, `session_epoch`, `task_run_id`, `container_id`, `thread_id`, `runtime_id`, `image_ref`, `status`
  - Durability metadata: `stdout_artifact_ref`, `stderr_artifact_ref`, `diagnostics_ref`, `last_log_at`, `last_log_offset`, `error_message`
  - Recovery inputs: `artifact_spool_path`, `workspace_path`, `session_workspace_path`, `control_url`
- **CodexManagedSessionStatus**
  - Planned values: `launching`, `ready`, `busy`, `terminating`, `terminated`, `degraded`, `failed`

## Implementation Plan

1. Add failing tests for the durable session record/store, session-level artifact publication, controller summary/reconcile behavior, and worker startup reconciliation.
2. Implement the durable session record schema and JSON-backed session store.
3. Implement a session supervisor that tails restart-safe session spool files, updates log offsets, publishes stdout/stderr/diagnostics artifacts, and finalizes durable session metadata.
4. Update the container-side runtime to append session lifecycle and turn output to stdout/stderr spool files under the mounted artifact path.
5. Wire the controller to persist launch/control transitions, start/stop supervision, answer summary/publication requests from the durable record, and reconcile active sessions on worker startup.
6. Run focused tests, update task status, run scope validation, then run `./tools/test_unit.sh`.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_store.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
2. `SPECIFY_FEATURE=132-codex-managed-session-plane-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
3. `SPECIFY_FEATURE=132-codex-managed-session-plane-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
4. `./tools/test_unit.sh`

### Manual Validation

1. Launch a managed session and verify a durable session record appears under the configured session store root.
2. Exercise at least one session turn, then confirm stdout/stderr/diagnostics artifact refs are available via session summary/publication calls.
3. Restart the worker process, run startup reconciliation, and confirm the session either resumes supervision or is marked degraded with an explicit error.
