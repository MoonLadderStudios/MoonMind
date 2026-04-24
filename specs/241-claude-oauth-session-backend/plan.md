# Implementation Plan: Claude OAuth Session Backend

**Branch**: `241-claude-oauth-session-backend` | **Date**: 2026-04-22 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/241-claude-oauth-session-backend/spec.md`

## Summary

Implement MM-478 by completing the Claude OAuth backend slice: `claude_code` provider registry defaults must use the PTY/WebSocket OAuth session path and `claude login`; the OAuth auth-runner must set Claude home variables and clear conflicting Anthropic/Claude API-key variables; and startup profile seeding must preserve an OAuth-volume `claude_anthropic` profile with Claude API-key conflict clearing. The implementation is narrow and test-first: add focused unit/API-boundary tests for provider defaults, session creation, runner arguments, and seeded profile shape, then update the registry, runner environment builder, and seed profile definition.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| -- | -- | -- | -- | -- |
| FR-001 | implemented_verified | `tests/unit/api_service/api/routers/test_oauth_sessions.py` verifies Claude session creation defaults | completed | route unit |
| FR-002 | implemented_verified | `moonmind/workflows/temporal/runtime/providers/registry.py`; `tests/unit/auth/test_oauth_provider_registry.py` | completed | unit |
| FR-003 | implemented_verified | `tests/unit/auth/test_oauth_session_activities.py` verifies `claude login` handoff | completed | unit |
| FR-004 | implemented_verified | `moonmind/workflows/temporal/runtime/terminal_bridge.py`; `tests/unit/services/temporal/runtime/test_terminal_bridge.py` | completed | unit |
| FR-005 | implemented_verified | terminal bridge test verifies empty `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY` args and no ambient values | completed | unit |
| FR-006 | implemented_verified | `api_service/main.py`; `tests/unit/api_service/test_provider_profile_auto_seed.py` | completed | unit |
| FR-007 | implemented_verified | seed profile test verifies `CLAUDE_API_KEY` in `clear_env_keys` | completed | unit |
| FR-008 | implemented_verified | route/activity/terminal tests preserve OAuth session terminal scope without changing task launch path | completed | unit + route |
| FR-009 | implemented_verified | terminal bridge tests verify bounded metadata, redaction, and no raw ambient key propagation | completed | unit |
| SC-001 | implemented_verified | Claude route test verifies response and captured session transport `moonmind_pty_ws` | completed | route unit |
| SC-002 | implemented_verified | provider registry test verifies exact Claude OAuth defaults | completed | unit |
| SC-003 | implemented_verified | terminal bridge test verifies Claude runner args | completed | unit |
| SC-004 | implemented_verified | seed profile test verifies OAuth-home shape and clear list | completed | unit |
| SC-005 | implemented_verified | focused and full unit suites pass with existing Codex OAuth tests | completed | unit |
| DESIGN-REQ-003 | implemented_verified | seed profile shape implemented and tested | completed | unit |
| DESIGN-REQ-006 | implemented_verified | OAuth route, activity, and terminal runner tests cover session and PTY path | completed | unit + route |
| DESIGN-REQ-010 | implemented_verified | provider registry implemented and tested | completed | unit |
| DESIGN-REQ-011 | implemented_verified | Claude runner home environment implemented and tested | completed | unit |
| DESIGN-REQ-012 | implemented_verified | Claude API-key clearing implemented and tested | completed | unit |
| DESIGN-REQ-017 | implemented_verified | OAuth-volume profile shape and session route coverage preserve registration/update expectations | completed | unit |
| DESIGN-REQ-018 | implemented_verified | profile clear list and runner environment covered; broader runtime launch remained out of scope per spec | completed | unit |

## Technical Context

**Language/Version**: Python 3.12 
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, pytest 
**Storage**: Existing SQLAlchemy/Alembic tables for provider profiles and OAuth sessions; no new persistent tables 
**Unit Testing**: `./tools/test_unit.sh` with pytest targets 
**Integration Testing**: Route-level async pytest fixtures and existing Temporal/OAuth unit boundary tests; full hermetic integration can run through `./tools/test_integration.sh` when Docker is available 
**Target Platform**: Linux worker/API containers 
**Project Type**: FastAPI control plane plus Temporal worker runtime services 
**Performance Goals**: OAuth session creation remains a single DB transaction plus workflow start; runner startup argument construction remains deterministic and bounded 
**Constraints**: No raw credential material in logs/artifacts/workflow payloads; preserve existing Codex OAuth behavior; no compatibility aliases for internal pre-release contracts 
**Scale/Scope**: One provider profile (`claude_anthropic`), one runtime (`claude_code`), one OAuth session backend path

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses OAuth Session workflow and terminal bridge instead of adding a Claude-only orchestration stack.
- II. One-Click Agent Deployment: PASS. Uses existing local Docker auth-runner pattern and defaults.
- III. Avoid Vendor Lock-In: PASS. Claude-specific behavior remains provider-registry data and runtime-specific runner environment selection.
- IV. Own Your Data: PASS. OAuth home remains a local volume and refs only; no external storage.
- V. Skills Are First-Class: PASS. MoonSpec artifacts preserve workflow traceability.
- VI. Bittersweet Lesson: PASS. Thin provider config and terminal runner contracts keep the slice replaceable.
- VII. Runtime Configurability: PASS. Volume defaults remain registry/env driven.
- VIII. Modular Architecture: PASS. Changes stay in provider registry, runner boundary, and seed profile.
- IX. Resilient by Default: PASS. OAuth session workflow and status model remain the retry/resume boundary; no workflow payload shape change is required.
- X. Continuous Improvement: PASS. Verification evidence is captured in artifacts.
- XI. Spec-Driven Development: PASS. This plan follows the single-story spec and TDD task list.
- XII. Canonical Docs vs Tmp: PASS. Jira orchestration input remains under `local-only handoffs`; canonical docs are read-only source requirements.
- XIII. Pre-Release Velocity: PASS. No compatibility shims or aliases are added.

## Project Structure

### Documentation (this feature)

```text
specs/241-claude-oauth-session-backend/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── claude-oauth-session-backend.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/oauth_sessions.py
└── main.py

moonmind/workflows/temporal/
├── activities/oauth_session_activities.py
└── runtime/
 ├── providers/registry.py
 └── terminal_bridge.py

tests/
├── unit/auth/test_oauth_provider_registry.py
├── unit/auth/test_oauth_session_activities.py
├── unit/api_service/api/routers/test_oauth_sessions.py
├── unit/api_service/test_provider_profile_auto_seed.py
└── unit/services/temporal/runtime/test_terminal_bridge.py
```

**Structure Decision**: Use existing backend and worker-runtime modules. No frontend or schema migration is required for MM-478.

## Complexity Tracking

No constitution violations.
