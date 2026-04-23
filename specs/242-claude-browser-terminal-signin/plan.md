# Implementation Plan: Claude Browser Terminal Sign-In Ceremony

**Branch**: `242-claude-browser-terminal-signin` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/242-claude-browser-terminal-signin/spec.md`

## Summary

Implement MM-479 by verifying the Claude OAuth browser terminal ceremony. Repo inspection showed the shared OAuth terminal page, session attach route, WebSocket terminal bridge, `awaiting_user` attachability, hash-only one-time attach tokens, PTY input forwarding, and generic terminal-frame rejection already existed. This story is complete through test-first verification focused on a Claude `runtime_id = claude_code` scenario: a UI test waits until a Claude OAuth session reaches `awaiting_user` before attaching, a backend route test covers the Claude awaiting-user attach token contract, a terminal bridge test covers forwarding an authorization-code-like string without persisting raw input, and focused plus full unit validation passed.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| -- | -- | -- | -- | -- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/oauth-terminal.tsx`; Claude session UI verification in `frontend/src/entrypoints/mission-control.test.tsx` | completed | UI integration-style |
| FR-002 | implemented_verified | OAuth terminal page polls session status before attach; Claude-specific UI test covers `starting` to `awaiting_user` attach | completed | UI integration-style |
| FR-003 | implemented_verified | `api_service/api/routers/oauth_sessions.py`; Claude awaiting-user route test in `tests/unit/api_service/api/routers/test_oauth_sessions.py` | completed | route unit |
| FR-004 | implemented_verified | `_handle_oauth_terminal_ws_message`; Claude auth-code input test in `tests/unit/services/temporal/runtime/test_terminal_bridge.py` | completed | unit |
| FR-005 | implemented_verified | `TerminalBridgeConnection.safe_metadata()` exposes counters/resizes only; Claude auth-code input test confirms raw input is excluded | completed | unit |
| FR-006 | implemented_verified | OAuth session responses redact failure/profile data; terminal input is not stored in API response model; focused route and bridge tests preserve this | completed | route unit + unit |
| FR-007 | implemented_verified | attach route hashes token and WebSocket consumes single-use token; Claude awaiting-user route test verifies hash-only metadata | completed | route unit |
| FR-008 | implemented_verified | terminal bridge rejects `docker_exec` and `task_terminal` frames; unit test exists | no new implementation | unit |
| FR-009 | implemented_verified | existing Codex OAuth terminal tests remain in the same passing suites | completed | unit + UI |
| SC-001 | implemented_verified | Claude UI test waits through `starting` and attaches at `awaiting_user` | completed | UI integration-style |
| SC-002 | implemented_verified | Claude awaiting-user attach-token route test verifies hash-only token metadata | completed | route unit |
| SC-003 | implemented_verified | Claude auth-code PTY forwarding test verifies safe metadata excludes raw input | completed | unit |
| SC-004 | implemented_verified | terminal bridge generic exec rejection test exists | no new implementation | unit |
| SC-005 | implemented_verified | focused test command passed; full unit verification recorded in `verification.md` | completed | unit + UI |
| DESIGN-REQ-006 | implemented_verified | shared OAuth terminal path plus Claude UI boundary evidence | completed | UI integration-style |
| DESIGN-REQ-007 | implemented_verified | generic terminal-frame rejection and OAuth-only route scope exist with passing unit tests | completed | unit |
| DESIGN-REQ-008 | implemented_verified | Claude auth-code PTY forwarding test added | completed | unit |
| DESIGN-REQ-009 | implemented_verified | Claude awaiting-user route and UI tests added | completed | route + UI |
| DESIGN-REQ-010 | implemented_verified | terminal metadata and API response models avoid raw input persistence; focused tests passed | completed | unit + route |
| DESIGN-REQ-016 | implemented_verified | session queries scope attach to `requested_by_user_id`; route fixtures exercise the current user path | completed | route unit |
| DESIGN-REQ-017 | implemented_verified | Claude awaiting-user attach test verifies hash-only one-time token metadata | completed | route unit |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, React, xterm.js, Vitest, pytest  
**Storage**: Existing OAuth session row metadata only; no new persistent tables  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh <pytest targets>`  
**Integration Testing**: Existing route-level async pytest fixtures and Vitest Mission Control component harness  
**Target Platform**: Linux API/worker containers and browser Mission Control UI  
**Project Type**: FastAPI control plane plus React Mission Control frontend and Temporal-backed runtime services  
**Performance Goals**: OAuth terminal polling remains 1 second; attach validation remains a single session lookup/update  
**Constraints**: Do not persist pasted token/code content; attach tokens must remain short-lived and single-use; OAuth terminal must not become a generic task terminal; preserve Codex OAuth behavior  
**Scale/Scope**: One Claude OAuth browser-terminal ceremony for `runtime_id = claude_code`

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses existing OAuth session and PTY/WebSocket bridge.
- II. One-Click Agent Deployment: PASS. No new external dependency or storage.
- III. Avoid Vendor Lock-In: PASS. Claude ceremony remains runtime-specific behavior behind shared OAuth terminal boundaries.
- IV. Own Your Data: PASS. Credential material remains in the auth volume and transient PTY input.
- V. Skills Are First-Class: PASS. MoonSpec artifacts preserve MM-479 traceability.
- VI. Bittersweet Lesson: PASS. Verifies the replaceable shared terminal boundary rather than adding a bespoke Claude terminal stack.
- VII. Runtime Configurability: PASS. Uses existing runtime/profile-driven OAuth session fields.
- VIII. Modular Architecture: PASS. Work stays in route, terminal bridge, and UI tests unless verification exposes a gap.
- IX. Resilient by Default: PASS. Maintains explicit session states and fail-closed attach validation.
- X. Continuous Improvement: PASS. Verification evidence is captured in tests and artifacts.
- XI. Spec-Driven Development: PASS. This plan follows a single-story spec and TDD task list.
- XII. Canonical Docs vs Tmp: PASS. Jira orchestration input stays under `docs/tmp`; canonical source docs are read-only requirements.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases or hidden fallback semantics are introduced.

## Project Structure

### Documentation

```text
specs/242-claude-browser-terminal-signin/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-browser-terminal-signin.md
├── tasks.md
└── verification.md
```

### Source Code

```text
api_service/api/routers/oauth_sessions.py
frontend/src/entrypoints/oauth-terminal.tsx
moonmind/workflows/temporal/runtime/terminal_bridge.py
tests/unit/api_service/api/routers/test_oauth_sessions.py
tests/unit/services/temporal/runtime/test_terminal_bridge.py
frontend/src/entrypoints/mission-control.test.tsx
```

**Structure Decision**: Add focused verification tests first. Production changes are contingency work only if those tests expose a behavioral gap.

## Complexity Tracking

No constitution violations.
