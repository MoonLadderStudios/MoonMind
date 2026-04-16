# Implementation Plan: OAuth Terminal Enrollment Flow

**Branch**: `183-oauth-terminal-flow` | **Date**: 2026-04-16 | **Spec**: `specs/183-oauth-terminal-flow/spec.md`

## Input

Single-story feature specification from `specs/183-oauth-terminal-flow/spec.md` generated from Jira issue `MM-358` and the preserved preset brief `MM-358: OAuth Terminal Enrollment Flow`.

## Summary

This story implements first-party OAuth terminal session, auth runner, and PTY/WebSocket bridge lifecycle. The implementation should stay inside the existing MoonMind runtime boundaries and validate behavior through TDD: focused unit tests first, then hermetic integration coverage where the behavior crosses workflow, API, Docker, or browser boundaries.

## Technical Context

- Language/version: Python 3.12; TypeScript only when the story touches Mission Control UI behavior.
- Primary dependencies: Pydantic v2 models, FastAPI routers, Temporal Python SDK workflows/activities, pytest, and existing Docker/runtime helpers.
- Storage: existing database/workflow/profile/session/workload records only; no new persistent storage unless a later plan revision explicitly proves it is required.
- Unit testing: `./tools/test_unit.sh` with `MOONMIND_FORCE_LOCAL_TESTS=1`; focused frontend tests use `./tools/test_unit.sh --dashboard-only --ui-args <path>` in managed-agent paths.
- Integration testing: `./tools/test_integration.sh` for compose-backed `integration_ci` coverage when Docker is available.
- Target platform: MoonMind API service, Temporal worker/runtime services, managed Codex session containers, and Mission Control where applicable.
- Project type: backend orchestration/runtime with optional browser surface for OAuth terminal flow.
- Performance goals: validation and serialization must be deterministic and low-overhead relative to existing workflow/API calls.
- Constraints: preserve `MM-358` traceability; keep raw credential contents out of workflow history, browser responses, logs, and artifacts; fail fast for unsupported or unsafe runtime values.
- Scale/scope: one independently testable story, dependencies: STORY-001, STORY-005.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. The plan controls MoonMind boundaries without rebuilding provider runtimes.
- II One-Click Agent Deployment: PASS. Uses existing local-first Docker Compose and test tooling.
- III Avoid Vendor Lock-In: PASS. Runtime-specific behavior remains behind existing profile, session, terminal, or workload boundaries.
- IV Own Your Data: PASS. Credentials and artifacts remain operator-controlled, with refs instead of raw secret payloads.
- V Skills Are First-Class: PASS. No executable skill contract changes.
- VI Bittersweet Lesson: PASS. Planning targets thin contracts and tests around volatile runtime behavior.
- VII Runtime Configurability: PASS. Behavior is driven by profile metadata, launch requests, policies, and runtime configuration.
- VIII Modular Architecture: PASS. Work stays in established routers, schemas, workflow activities, adapters, runtime, or workload modules.
- IX Resilient by Default: PASS. Invalid states fail fast and long-running workflow boundaries remain explicit.
- X Continuous Improvement: PASS. Verification evidence and remaining blockers are captured in MoonSpec artifacts.
- XI Spec-Driven Development: PASS. Each story has isolated spec, plan, design artifacts, and future tasks.
- XII Canonical Docs Separation: PASS. Migration/work tracking remains in specs and docs/tmp, not canonical docs.
- XIII Pre-Release Compatibility: PASS. Unsupported internal contract values fail fast; no compatibility aliases are planned.

## Project Structure

- Spec: `specs/183-oauth-terminal-flow/spec.md`
- Plan: `specs/183-oauth-terminal-flow/plan.md`
- Research: `specs/183-oauth-terminal-flow/research.md`
- Data model: `specs/183-oauth-terminal-flow/data-model.md`
- Contract: `specs/183-oauth-terminal-flow/contracts/oauth-terminal-flow.md`
- Quickstart: `specs/183-oauth-terminal-flow/quickstart.md`
- Likely production touchpoints: api_service/api/routers/oauth_sessions.py, api_service/api/schemas_oauth_sessions.py, moonmind/workflows/temporal/runtime/terminal_bridge.py, moonmind/workflows/temporal/workflows/oauth_session.py, frontend/src/entrypoints/oauth-terminal.tsx, frontend/src/entrypoints/mission-control-app.tsx
- Unit test targets: tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py frontend/src/entrypoints/mission-control.test.tsx
- Integration test targets: tests/integration/temporal/test_oauth_session.py

## Test Strategy

- Unit strategy: add red-first tests around validation, serialization, state transitions, redaction, terminal bridge frame handling, Mission Control attach behavior, and boundary payload construction for this story. Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` and `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx` during focused iteration and `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification.
- Integration strategy: add or update hermetic integration tests for the real API/workflow/runtime/container/browser boundary when this story crosses one. Run `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`; record the exact blocker if the Docker socket is unavailable in a managed-agent container.

## Complexity Tracking

None.
