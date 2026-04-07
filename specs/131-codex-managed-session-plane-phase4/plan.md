# Implementation Plan: codex-managed-session-plane-phase4

**Branch**: `131-codex-managed-session-plane-phase4` | **Date**: 2026-04-06 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/131-codex-managed-session-plane-phase4/spec.md`

## Summary

Implement the Phase 4 transitional launcher for the Codex managed session plane by adding a concrete Docker-backed managed-session controller, a container-side Codex app-server bridge that runs inside the launched session container, and worker bootstrap wiring that injects the controller into the `agent_runtime` activity family. The implementation stays on the container boundary and uses the current MoonMind image as a transitional session image.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: existing stdlib subprocess/json/http facilities, Temporal runtime activity infrastructure, Docker CLI available in the managed worker image, Codex CLI `app-server`
**Testing**: focused pytest suites plus repo-required final verification via `./tools/test_unit.sh`
**Project Type**: Temporal backend runtime and worker bootstrap
**Constraints**: no worker-local Codex execution for the new path; no new Python dependencies for transport; preserve Phase 3 typed managed-session contracts

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. MoonMind remains the orchestrator while the container owns only runtime-local Codex session state.
- **II. One-Click Agent Deployment**: PASS. The phase reuses the existing MoonMind image and Docker proxy assumptions instead of adding a new packaging prerequisite.
- **III. Avoid Vendor Lock-In**: PASS. The slice is Codex-specific, but isolated behind the managed-session controller boundary and image reference.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The implementation preserves the Phase 3 typed activity contracts and adds a small, replaceable session launcher/control layer.
- **VII. Powerful Runtime Configurability**: PASS. The launched image remains a request/config input, and later switching to a dedicated image is a deployment change.
- **VIII. Modular and Extensible Architecture**: PASS. The worker depends on a concrete controller boundary, while the container-side bridge encapsulates Codex app-server details.
- **IX. Resilient by Default**: PASS. Launch readiness, status inspection, and termination failures remain explicit and typed; worker bootstrap tests cover the injected boundary.
- **XI. Spec-Driven Development**: PASS. This spec/plan/tasks set tracks the Phase 4 launcher slice.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs stay desired-state only; this phase-specific plan lives under `specs/`.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The new path does not add compatibility wrappers around the local managed-runtime launcher; it adds a separate container-first implementation.

## Research

- `codex app-server` supports a line-delimited JSON-RPC stdio transport and can service `initialize`, `thread/start`, `turn/start`, `turn/steer`, `turn/interrupt`, and `thread/read` without extra Python websocket dependencies.
- The current worker image already includes Docker CLI and Codex CLI, so the transitional session container can reuse the MoonMind image while keeping the session execution loop outside the main worker process.
- Worker bootstrap currently injects `run_store`, `run_supervisor`, and `run_launcher` only. Phase 4 needs a fourth dependency: the concrete session controller.

## Project Structure

- Add the concrete session controller under `moonmind/workflows/temporal/runtime/` so it lives beside the existing launcher/supervisor infrastructure.
- Add the container-side session bridge under the same runtime package because it is specific to the managed-runtime activity boundary and transitional session image.
- Keep worker wiring in `moonmind/workflows/temporal/worker_runtime.py`.
- Keep tests at the activity/runtime boundary and worker bootstrap boundary.

## Data Model

- The session runtime persists a small session-state document in the mounted session workspace.
- The state document records:
  - MoonMind `sessionId`
  - current `sessionEpoch`
  - logical MoonMind `threadId`
  - mapped vendor-native Codex thread id
  - current active turn id when one exists
  - timestamps for launch and last control action
- This state stays inside the container/session workspace and is continuity cache only, not durable truth.

## Implementation Plan

1. Add failing tests for a Docker-backed managed-session controller, the container-side Codex app-server bridge, and worker bootstrap injection.
2. Implement a container-side session runtime command that:
   - validates the mount contract on startup,
   - writes readiness metadata,
   - translates MoonMind control requests into `codex app-server` stdio requests,
   - persists logical-to-vendor thread mapping in the session workspace.
3. Implement a Docker-backed managed-session controller that:
   - launches the session container from the provided image ref,
   - polls readiness,
   - executes control actions in the container boundary,
   - maps results back into typed Phase 3 managed-session models,
   - stops/removes the container on termination.
4. Update worker bootstrap so `_build_agent_runtime_deps()` returns the concrete session controller and `_build_runtime_activities()` injects it into `TemporalAgentRuntimeActivities`.
5. Run focused tests, then `./tools/test_unit.sh`, then update tasks and analysis artifacts.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
2. `./tools/test_unit.sh`

### Manual Validation

1. Launch a session through the activity/controller boundary in a Docker-enabled environment and confirm the returned handle contains a distinct container id.
2. Send a trivial turn and confirm the controller returns a typed response sourced from in-container Codex app-server control.
3. Clear the session and verify the epoch advances while the container id remains unchanged.
4. Terminate the session and verify the container is removed.
