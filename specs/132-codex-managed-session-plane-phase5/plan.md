# Implementation Plan: codex-managed-session-plane-phase5

**Branch**: `132-codex-managed-session-plane-phase5` | **Date**: 2026-04-06 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/132-codex-managed-session-plane-phase5/spec.md`

## Summary

Implement the Phase 5 Codex session adapter slice by adding a workflow-side `CodexSessionAdapter`, persisting canonical step results for session-backed managed Codex turns, and wiring `MoonMind.AgentRun` to choose the adapter whenever a managed Codex request already carries a task-scoped managed-session binding. The new path must stay container-first and reuse the Phase 3 session activity contracts plus the Phase 4 Docker-backed controller without falling back to the worker-local managed-runtime launcher.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: existing Pydantic models, Temporal workflow/activity primitives, current managed run store, Phase 4 managed-session activity/controller surface
**Testing**: focused pytest suites plus final verification with `./tools/test_unit.sh`
**Project Type**: Temporal backend workflow + adapter integration
**Constraints**: no worker-local Codex execution for the session-backed path; preserve Phase 3 typed session contracts; keep image selection deployment-configurable; keep non-Codex managed runtimes on the existing adapter path

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. MoonMind stays the orchestrator and the adapter only translates workflow intent into the existing remote session control surface.
- **II. One-Click Agent Deployment**: PASS. The implementation reuses the current managed worker/session-image assumptions and does not introduce new operator prerequisites.
- **III. Avoid Vendor Lock-In**: PASS. The adapter is Codex-specific, but isolated behind the `AgentAdapter` boundary and deployment-configurable image selection.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The change adds a narrow adapter layer over the already-typed session contracts instead of expanding workflow-side provider logic.
- **VII. Powerful Runtime Configurability**: PASS. Session image/path defaults remain config-derived and request overrides stay bounded.
- **VIII. Modular and Extensible Architecture**: PASS. The workflow picks an adapter; the adapter owns session transport details; the controller/runtime remain unchanged.
- **IX. Resilient by Default**: PASS. Session-backed managed runs persist canonical result state and add workflow-boundary tests so step completion does not depend on adapter-local memory only.
- **XI. Spec-Driven Development**: PASS. This spec/plan/tasks set tracks the Phase 5 adapter slice explicitly.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Desired-state docs stay canonical; this implementation detail remains in the spec set.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The new path chooses the session adapter directly for session-bound Codex steps instead of adding compatibility shims around the worker-local launcher.

## Research

- The current codebase already provides the Phase 2 task-scoped `MoonMind.AgentSession` workflow, the Phase 3 session activity family, and the Phase 4 Docker-backed managed-session controller/runtime.
- `MoonMind.Run` already binds managed Codex steps to one task-scoped `CodexManagedSessionBinding`, but `MoonMind.AgentRun` still routes all managed runtimes through `ManagedAgentAdapter`.
- The session runtime’s `send_turn`, `clear_session`, `interrupt_turn`, `fetch_session_summary`, and `terminate_session` contracts are already typed and container-first; Phase 5 only needs an adapter that consumes them from workflow code.
- The existing managed run store is the lightest place to persist canonical step status/result data for session-backed managed runs without redesigning the broader managed-runtime result flow.

## Project Structure

- Add `moonmind/workflows/adapters/codex_session_adapter.py` for the new workflow-side adapter.
- Keep profile resolution/env shaping helpers in `moonmind/workflows/adapters/managed_agent_adapter.py` reusable rather than duplicating session-specific provider-profile logic.
- Update `moonmind/workflows/temporal/workflows/agent_run.py` to select the session adapter for managed Codex requests with `managedSession`.
- Add focused adapter tests under `tests/unit/workflows/adapters/`.
- Add workflow-boundary regression tests under `tests/unit/workflows/temporal/workflows/`.

## Data Model

- Persist one session-backed managed step record in the existing managed run store plus a small sidecar payload for the canonical `AgentRunResult` and session locator metadata needed for adapter `status`, `fetch_result`, and `cancel`.
- The sidecar payload records:
  - managed `run_id`
  - session locator (`session_id`, `session_epoch`, `container_id`, `thread_id`)
  - latest turn id
  - canonical `AgentRunResult`
  - optional latest session summary/control refs
- This state is step-scoped execution metadata, not durable task continuity truth. `MoonMind.AgentSession` and artifacts remain authoritative for session continuity.

## Implementation Plan

1. Add failing tests for `CodexSessionAdapter` start/reuse/clear/cancel/terminate behavior and for `MoonMind.AgentRun` adapter selection.
2. Extract or reuse the managed provider-profile resolution/env-shaping helpers needed by both `ManagedAgentAdapter` and `CodexSessionAdapter`.
3. Implement `CodexSessionAdapter` so it:
   - resolves the managed provider profile,
   - queries/signals `MoonMind.AgentSession`,
   - launches or reuses the task-scoped session container,
   - sends step instructions through `agent_runtime.send_turn`,
   - persists canonical run/result state for later `status` and `fetch_result`,
   - exposes explicit clear/interrupt/summary/terminate methods.
4. Update `MoonMind.AgentRun` to instantiate `CodexSessionAdapter` for managed Codex requests with `managedSession` and to keep the existing adapter path for all other managed runtimes.
5. Run focused tests, run full unit verification, update `tasks.md`, and validate implementation scope.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py`
2. `./tools/test_unit.sh`
3. `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
4. `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

### Manual Validation

1. Submit a managed Codex task with at least two steps and confirm the second step reuses the existing task-scoped session binding.
2. Verify `MoonMind.AgentSession` query state reflects launched container/thread handles after the first step.
3. Clear/reset the session through the adapter boundary and verify the session epoch/thread boundary changes while the session container identity stays stable.
