# Implementation Plan: Run Authenticated OAuth Terminal Sessions

**Branch**: `174-run-authenticated-oauth-terminal-sessions` | **Date**: 2026-04-15 | **Spec**: `specs/174-run-authenticated-oauth-terminal-sessions/spec.md`

## Technical Context

- API surface: `api_service/api/routers/oauth_sessions.py`, `api_service/api/schemas_oauth_sessions.py`, `api_service/api/websockets.py`
- Workflow/activity boundary: `moonmind/workflows/temporal/workflows/oauth_session.py`, `moonmind/workflows/temporal/activities/oauth_session_activities.py`
- Runtime bridge: `moonmind/workflows/temporal/runtime/terminal_bridge.py`
- Provider registry: `moonmind/workflows/temporal/runtime/providers/registry.py`
- Tests: `tests/unit/api_service/api/routers/test_oauth_sessions.py`, `tests/unit/auth/test_oauth_session_activities.py`, new terminal bridge tests

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. Uses provider CLI bootstrap commands behind registry contracts.
- II One-Click Agent Deployment: PASS. Keeps Docker-backed local behavior and fails fast when Docker is unavailable.
- III Avoid Vendor Lock-In: PASS. Runtime-specific bootstrap command lookup remains behind provider registry.
- IV Own Your Data: PASS. Credential material remains in operator-owned auth volumes.
- V Skills Are First-Class: PASS. No skill contract changes.
- VI Bittersweet Lesson: PASS. Keeps implementation behind thin API/activity/runtime seams with tests.
- VII Runtime Configurability: PASS. Uses provider registry/env volume defaults.
- VIII Modular Architecture: PASS. Changes stay within OAuth API, activity, and bridge boundaries.
- IX Resilient by Default: PASS. Terminal outcomes stop the auth runner and persist terminal state.
- X Continuous Improvement: PASS. Terminal outcomes have observable states and failure reasons.
- XI Spec-Driven Development: PASS. This spec/plan/tasks directory tracks the change.
- XII Canonical Docs Separation: PASS. No migration checklist added to canonical docs.
- XIII Pre-Release Compatibility: PASS. No internal compatibility alias or semantic fallback is introduced.

## Implementation Strategy

1. Extend OAuth session responses with terminal refs and transport metadata.
2. Persist auth runner metadata from `oauth_session.start_auth_runner` through `oauth_session.update_terminal_session`.
3. Harden the WebSocket bridge around owner, active status, TTL, transport scope, provider bootstrap command execution, heartbeat/resize handling, and connection timestamps.
4. Stop the auth runner from API-driven finalize paths so the browser terminal closes deterministically even when the API performs synchronous verification.
5. Add focused unit tests for API, activity, and bridge behavior.

## Test Strategy

- Unit: targeted pytest tests for schemas/router behavior, activity metadata persistence, terminal authorization helper behavior, and runner stop behavior.
- Integration: existing Temporal OAuth workflow tests cover workflow success and cancellation locally; not marked `integration_ci` due Temporal test-server timeout policy.

## Risks

- Full browser PTY behavior still depends on Docker availability and provider CLIs being present in the auth runner image. This story hardens MoonMind ownership, transport metadata, and deterministic cleanup around the existing Docker-backed runner.
