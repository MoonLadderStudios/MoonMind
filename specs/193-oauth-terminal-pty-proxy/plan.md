# Implementation Plan: OAuth Terminal PTY Proxy

**Branch**: `193-oauth-terminal-pty-proxy` | **Date**: 2026-04-16 | **Spec**: `specs/193-oauth-terminal-pty-proxy/spec.md`  
**Input**: Single-story feature specification from `specs/193-oauth-terminal-pty-proxy/spec.md`

## Summary

This story changes the OAuth terminal attach WebSocket from metadata-only frame acknowledgement to real auth-runner PTY proxying. The implementation should reuse the existing Docker-backed terminal execution helper behavior while keeping the OAuth attach route as the only browser-facing OAuth terminal surface, enforcing one-time attach tokens, session ownership, TTL, resize/heartbeat handling, safe close metadata, redaction, and rejection of generic Docker exec or task-terminal frames. TDD coverage starts with focused unit tests for bridge forwarding and router metadata, then integration coverage targets the OAuth session WebSocket boundary when Docker is available.

## Technical Context

**Language/Version**: Python 3.12; TypeScript only if Mission Control terminal client behavior must change.  
**Primary Dependencies**: FastAPI WebSockets, SQLAlchemy async sessions, Pydantic v2 schemas, Docker SDK / Docker CLI behavior already used by terminal surfaces, Temporal OAuth session workflow metadata, pytest.  
**Storage**: Existing `ManagedAgentOAuthSession` rows and `metadata_json`; no new persistent storage.  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` with focused pytest targets for OAuth session router and terminal bridge runtime.  
**Integration Testing**: `./tools/test_integration.sh` for compose-backed `integration_ci` coverage when Docker is available; focused target is OAuth session workflow/terminal boundary.  
**Target Platform**: MoonMind API service, Temporal worker/runtime services, auth-runner container boundary, and Mission Control OAuth terminal.  
**Project Type**: Backend orchestration/runtime with browser WebSocket terminal surface.  
**Performance Goals**: Terminal forwarding should process input/output frames without material overhead compared with existing WebSocket terminal behavior and must not persist raw terminal scrollback.  
**Constraints**: Preserve `MM-362` traceability; keep raw credentials out of workflow history, browser non-terminal responses, logs, and artifacts; do not expose generic Docker exec through OAuth terminal; fail fast for unsupported frames and unsupported runtime values; preserve in-flight Temporal payload compatibility because only API/runtime bridge behavior changes.  
**Scale/Scope**: One OAuth terminal story, dependencies: MM-318; provider profile registration and workload credential inheritance are out of scope.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I Orchestrate, Don't Recreate: PASS. The plan proxies to the provider login process rather than rebuilding provider login behavior.
- II One-Click Agent Deployment: PASS. Uses existing Docker Compose/runtime dependencies and local test tools.
- III Avoid Vendor Lock-In: PASS. Runtime-specific bootstrap remains provider-registry driven and behind existing runtime IDs.
- IV Own Your Data: PASS. Credentials stay in operator-controlled auth volumes; workflow/API payloads carry refs and safe metadata only.
- V Skills Are First-Class: PASS. No executable skill contract changes.
- VI Bittersweet Lesson: PASS. Keeps volatile terminal implementation behind thin bridge helpers and tests the contract.
- VII Runtime Configurability: PASS. Runtime command and auth volume behavior remain provider/profile/config driven.
- VIII Modular Architecture: PASS. Work stays in existing OAuth router, terminal bridge runtime helper, schemas, and tests.
- IX Resilient by Default: PASS. Invalid states fail fast; attach tokens, TTLs, close metadata, and cleanup are explicit.
- X Continuous Improvement: PASS. Verification artifacts capture evidence and remaining blockers.
- XI Spec-Driven Development: PASS. Implementation proceeds from isolated spec, plan, contracts, tasks, and verification.
- XII Canonical Docs Separation: PASS. Work artifacts remain in `specs/` and `docs/tmp`; canonical docs are not rewritten.
- XIII Pre-Release Compatibility: PASS. No compatibility aliases or semantic transforms are introduced; unsupported frame/runtime values fail fast.

## Project Structure

### Documentation (this feature)

```text
specs/193-oauth-terminal-pty-proxy/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── oauth-terminal-pty-proxy.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/
│   ├── routers/oauth_sessions.py
│   └── websockets.py
└── services/oauth_auth_runner.py

moonmind/
└── workflows/temporal/runtime/terminal_bridge.py

tests/
├── unit/
│   ├── api_service/api/routers/test_oauth_sessions.py
│   ├── api_service/api/test_oauth_terminal_websocket.py
│   └── services/temporal/runtime/test_terminal_bridge.py
└── integration/temporal/test_oauth_session.py
```

**Structure Decision**: Implement the real PTY proxy at the OAuth session attach boundary and shared terminal bridge helper level. Keep `api_service/api/websockets.py` as the existing generic terminal path but do not route OAuth attach frames through generic task-terminal semantics.

## Test Strategy

- Unit strategy: add red-first tests for accepted input forwarding to an auth-runner PTY adapter, output streaming back to Mission Control, resize propagation, heartbeat acknowledgement, one-time token usage, safe close metadata, secret redaction, and rejection of generic Docker exec/task-terminal frames. Focused command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_terminal_bridge.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/test_oauth_terminal_websocket.py`.
- Integration strategy: add or update hermetic OAuth session boundary coverage where Docker is available. Command: `./tools/test_integration.sh`; focused file: `tests/integration/temporal/test_oauth_session.py`. If the managed container lacks Docker socket access, record that blocker and rely on unit boundary evidence for local verification.

## Complexity Tracking

None.
