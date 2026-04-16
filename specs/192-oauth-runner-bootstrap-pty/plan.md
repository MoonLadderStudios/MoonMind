# Implementation Plan: OAuth Runner Bootstrap PTY

**Branch**: `192-oauth-runner-bootstrap-pty` | **Date**: 2026-04-16 | **Spec**: `specs/192-oauth-runner-bootstrap-pty/spec.md`
**Input**: Single-story feature specification from `specs/192-oauth-runner-bootstrap-pty/spec.md`

**Note**: The standard setup script initially rejected the managed branch name `mm-361-9a0dffbf`; it succeeded with `SPECIFY_FEATURE=192-oauth-runner-bootstrap-pty` and created this plan.

## Summary

MM-361 replaces the placeholder Codex OAuth auth runner with a short-lived, session-owned runner that executes the provider registry bootstrap command in a PTY-backed terminal lifecycle. The implementation should stay within the existing OAuth session workflow, activity, provider registry, and terminal bridge boundaries; avoid new persistent storage; keep OAuth terminal evidence separate from managed task execution; and prove behavior through red-first unit tests plus hermetic Temporal boundary coverage when Docker-backed integration is available.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, pytest, existing OAuth provider registry, existing OAuth session workflow/activity catalog, existing terminal bridge runtime helpers  
**Storage**: Existing OAuth session database row and workflow/activity payloads only; no new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` with focused pytest targets for OAuth activities, terminal bridge runtime, provider registry, and OAuth session workflow behavior  
**Integration Testing**: `./tools/test_integration.sh` for compose-backed `integration_ci` coverage, with focused Temporal OAuth session coverage in `tests/integration/temporal/test_oauth_session.py` when Docker is available  
**Target Platform**: MoonMind API service and Temporal worker/runtime containers on Linux with Docker-controlled workload boundaries  
**Project Type**: Backend orchestration/runtime feature with Temporal workflow and activity boundaries  
**Performance Goals**: Auth runner startup remains bounded by the existing OAuth runner activity timeout; cleanup remains idempotent and retry-safe; failure reporting remains compact and secret-free  
**Constraints**: Preserve `MM-361` traceability; do not expose generic Docker exec or ordinary managed task terminal access; do not leak raw credential contents into workflow history, browser responses, logs, or artifacts; avoid compatibility aliases for internal pre-release contracts  
**Scale/Scope**: One independently testable story focused on Codex OAuth auth runner bootstrap PTY lifecycle; managed Codex task execution and Claude/Gemini parity remain out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I Orchestrate, Don't Recreate: PASS. The plan orchestrates provider bootstrap execution through existing runtime boundaries instead of rebuilding provider auth behavior.
- II One-Click Agent Deployment: PASS. Uses existing local-first Docker and managed-agent test tooling with no new mandatory external service.
- III Avoid Vendor Lock-In: PASS. Provider command selection remains behind the existing OAuth provider registry and runtime IDs.
- IV Own Your Data: PASS. Credentials stay in operator-controlled auth volumes; workflow and artifacts carry refs and redacted metadata only.
- V Skills Are First-Class: PASS. No executable skill contracts or skill materialization behavior are changed.
- VI Bittersweet Lesson: PASS. The work tightens thin runtime contracts and tests around volatile OAuth runner mechanics.
- VII Runtime Configurability: PASS. Runner image and provider bootstrap command behavior remain driven by runtime/provider configuration.
- VIII Modular Architecture: PASS. Changes stay in existing provider registry, OAuth activity, workflow, terminal bridge, and runner helper modules.
- IX Resilient by Default: PASS. Cleanup is idempotent, failures are explicit, and activity retries remain bounded.
- X Continuous Improvement: PASS. MoonSpec artifacts and verification will record outcomes and blocked integration evidence when applicable.
- XI Spec-Driven Development: PASS. MM-361 has isolated spec and planning artifacts before implementation.
- XII Canonical Docs Separation: PASS. Implementation planning remains under `specs/` and `docs/tmp`; canonical docs are source requirements, not migration logs.
- XIII Pre-Release Compatibility: PASS. No compatibility aliases are planned; unsupported internal runtime values should fail fast. Existing Temporal activity invocation compatibility is preserved by keeping the worker-bound request shape compatible and resolving provider bootstrap data at the activity/runtime boundary.

## Project Structure

### Documentation (this feature)

```text
specs/192-oauth-runner-bootstrap-pty/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── oauth-runner-bootstrap-pty.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/runtime/
├── providers/
│   ├── base.py
│   └── registry.py
└── terminal_bridge.py

moonmind/workflows/temporal/activities/
└── oauth_session_activities.py

moonmind/workflows/temporal/workflows/
└── oauth_session.py

api_service/services/
└── oauth_auth_runner.py

tests/unit/auth/
├── test_oauth_provider_registry.py
└── test_oauth_session_activities.py

tests/unit/services/temporal/runtime/
└── test_terminal_bridge.py

tests/integration/temporal/
└── test_oauth_session.py
```

**Structure Decision**: Use the existing backend orchestration/runtime layout. MM-361 does not require a new package, router, database table, or frontend surface; the work belongs at the OAuth session activity and terminal bridge runtime boundary, with workflow and integration tests proving the existing Temporal invocation shape still works.

## Test Strategy

- Unit strategy: add red-first tests in `tests/unit/auth/test_oauth_provider_registry.py`, `tests/unit/auth/test_oauth_session_activities.py`, and `tests/unit/services/temporal/runtime/test_terminal_bridge.py` to prove provider bootstrap commands are non-empty, `oauth_session.start_auth_runner` supplies the provider bootstrap command to the terminal bridge, the bridge starts the runner without placeholder sleep behavior, startup failures are redacted, and stop/cleanup remains idempotent. Use `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` during focused iteration and `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification.
- Integration strategy: add or update hermetic Temporal OAuth session coverage in `tests/integration/temporal/test_oauth_session.py` to prove the workflow still invokes `oauth_session.start_auth_runner` with the worker-bound payload shape and that success, failure, cancellation, expiry, and API-finalize paths stop the runner consistently. Run `./tools/test_integration.sh` when Docker is available; if the managed container lacks `/var/run/docker.sock`, record the exact blocker in verification output rather than treating integration as passed.

## Complexity Tracking

None.
