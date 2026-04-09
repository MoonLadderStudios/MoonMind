# Implementation Plan: codex-managed-session-cancel-hardening

**Branch**: `144-codex-managed-session-cancel-hardening` | **Date**: 2026-04-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/144-codex-managed-session-cancel-hardening/spec.md`

## Summary

Harden Codex managed-session cancellation by wiring the API/service cancel path to best-effort invoke `TerminateSession` on the task-scoped session workflow and by making the session workflow’s `TerminateSession` update execute the real `agent_runtime.terminate_session` activity when runtime handles still exist.

## Technical Context

**Language/Version**: Python 3.13  
**Primary Dependencies**: Temporal Python SDK, `TemporalExecutionService`, `ManagedSessionStore`, `MoonMind.AgentSession`, `TemporalClientAdapter`, `agent_runtime.terminate_session`  
**Storage**: Temporal workflow state plus file-backed `ManagedSessionStore` records under `/work/agent_jobs/managed_sessions`  
**Testing**: pytest unit tests plus final verification via `./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind API and Temporal workers  
**Constraints**: keep cleanup in the workflow/activity plane because the API container does not own Docker access; preserve task-scoped session identity and avoid new compatibility aliases

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Cleanup remains routed through Temporal workflow/activity boundaries.
- **II. One-Click Agent Deployment**: PASS. No new operator dependency is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The change stays inside the existing Codex-specific managed-session boundary.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The hardening strengthens existing cancel/terminate contracts instead of adding sidecar cleanup scripts.
- **IX. Resilient by Default**: PASS. Best-effort session teardown closes an orphaned-runtime failure mode; tests cover the boundary.
- **XI. Spec-Driven Development**: PASS. This hardening slice adds dedicated spec artifacts before implementation.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The change updates the active cancel path directly without compatibility shims.

## Implementation Plan

1. Add failing workflow tests for `TerminateSession` executing the managed-session terminate activity when runtime handles exist.
2. Add failing service tests for best-effort session-workflow teardown dispatch during execution cancellation.
3. Refactor `MoonMind.AgentSession.terminate_session_update()` to invoke `agent_runtime.terminate_session` using the current locator when handles are available.
4. Add a best-effort Codex session teardown helper in `TemporalExecutionService.cancel_execution()` using `ManagedSessionStore` and `TemporalClientAdapter.update_workflow("TerminateSession")`.
5. Run focused tests, then final verification via `./tools/test_unit.sh`.
