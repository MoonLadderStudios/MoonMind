# Implementation Plan: Finish Codex OAuth Terminal Flow

**Branch**: `205-finish-codex-oauth-terminal` | **Date**: 2026-04-18 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/205-finish-codex-oauth-terminal/spec.md`

## Summary

Complete MM-402 as a runtime feature: an authorized operator starts Codex OAuth authentication from Settings, attaches to the first-party terminal transport, completes device-code login, finalizes verified auth material into an OAuth-backed Provider Profile, and receives clear cleanup/status behavior. Repo gap analysis shows OAuth session APIs, terminal attach, profile registration, and many backend tests already exist; remaining work is Settings orchestration/UI state, enabling Codex interactive transport by default for OAuth sessions, making the Codex bootstrap command explicitly use `--device-auth`, strengthening Codex verification beyond file existence, and adding end-to-end unit/integration coverage.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | `ProviderProfilesManager.tsx` lists Edit/Enable/Delete only | Add Settings Auth action for Codex OAuth-capable profiles | frontend unit |
| FR-002 | partial | `POST /api/v1/oauth-sessions` exists, but no Settings caller | Wire Auth action to OAuth session API and terminal launch | frontend unit + API unit |
| FR-003 | implemented_unverified | `oauth-terminal.tsx`, `oauth_sessions.py` attach/ws handlers, existing router tests | Add Settings-driven attach/session coverage; implementation contingency if attach metadata does not align | frontend unit + API unit |
| FR-004 | partial | `registry.py` uses `codex login`; local CLI help exposes `--device-auth` | Change Codex bootstrap command to `codex login --device-auth` | unit |
| FR-005 | partial | `volume_verifiers.py` checks file presence only | Add Codex-specific auth JSON/config validation or status probe without secret output | unit |
| FR-006 | implemented_unverified | finalize endpoint registers `oauth_volume`/`oauth_home` profile | Add Settings flow coverage for finalization result and cache refresh | frontend unit + API unit |
| FR-007 | implemented_unverified | finalize preserves metadata-submitted policy values | Add regression that existing profile policy is preserved or intentionally carried from session request | API unit |
| FR-008 | missing | Settings page has no OAuth session state model | Add state labels for starting, bridge ready, awaiting user, verifying, succeeded, failed, cancelled, expired | frontend unit |
| FR-009 | partial | workflow supports `moonmind_pty_ws`; service passes `session_model.session_transport or "none"` | Make interactive Codex OAuth sessions use terminal transport | API/workflow unit |
| FR-010 | implemented_unverified | managed-session auth-volume specs/tests exist | Preserve existing provider-profile volume semantics; final verification only unless regression appears | final verify |
| FR-011 | implemented_unverified | cancel/finalize cleanup helpers and workflow signals exist | Add Settings cancel/retry coverage and backend cleanup regression if needed | frontend unit + API unit |
| FR-012 | implemented_verified | terminal ws rejects generic exec frames in tests | No new implementation; keep regression coverage | existing unit + final verify |
| FR-013 | implemented_unverified | responses redact secret-like failure reason; verifier returns counts only | Add verifier tests for sanitized Codex validation failure | unit |
| FR-014 | missing | no Settings-to-OAuth end-to-end coverage | Add focused UI and API tests; document integration command and blocker handling | frontend unit + API unit + integration if Docker available |
| FR-015 | implemented_verified | `spec.md` preserves MM-402 brief | Preserve in tasks/verification/final report | final verify |
| DESIGN-REQ-001 | partial | backend path exists, Settings start missing | Settings Auth + transport enablement | frontend + API |
| DESIGN-REQ-002 | implemented_unverified | auth runner activity and cleanup helpers exist | Verify cleanup through session lifecycle tests | unit/integration |
| DESIGN-REQ-003 | implemented_verified | attach/ws handler tests reject generic frames and proxy PTY | Preserve existing tests | existing unit |
| DESIGN-REQ-004 | partial | status enum exists; Settings UI lacks state projection | Add frontend state model and display | frontend unit |
| DESIGN-REQ-005 | implemented_unverified | finalize writes profile fields | Add finalization/profile refresh coverage | API + frontend |
| DESIGN-REQ-006 | partial | verifier only checks file presence | Strengthen Codex verification and sanitization tests | unit |
| DESIGN-REQ-007 | implemented_unverified | existing managed-session volume tests | Preserve no generic terminal/task shell changes | final verify |
| DESIGN-REQ-008 | partial | Jira brief is preserved; runtime gaps remain | Complete implementation and coverage | all targeted tests |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Settings UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Temporal Python SDK, Pydantic v2, React, TanStack Query, xterm.js, existing OAuth session/runtime helpers  
**Storage**: Existing `managed_agent_oauth_sessions`, Provider Profile rows, Docker auth volumes; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh`; focused Python pytest and Vitest via `./tools/test_unit.sh --ui-args ...`  
**Integration Testing**: `./tools/test_integration.sh` for Docker-backed `integration_ci` coverage when Docker is available  
**Target Platform**: MoonMind API service, Temporal workers, auth-runner container boundary, Mission Control Settings UI  
**Project Type**: Full-stack runtime feature spanning API, workflow/runtime, provider registry, verification helper, and Settings UI  
**Performance Goals**: Terminal/status UI remains responsive during polling/attach; OAuth session creation and status refresh avoid blocking Settings interactions  
**Constraints**: No raw credentials in workflow history, logs, artifacts, or UI responses; use trusted existing OAuth/profile boundaries; no generic shell exposure; preserve in-flight Temporal payload compatibility for existing OAuth sessions  
**Scale/Scope**: One Codex OAuth enrollment story for operator-driven profile authentication; non-Codex provider-specific OAuth UI is out of scope except preserving existing abstractions

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Principle I Orchestrate, Don't Recreate: PASS. Uses existing Codex CLI, OAuth Session workflow, and adapter boundaries.
- Principle II One-Click Agent Deployment: PASS. No new required external service or persistent storage.
- Principle III Avoid Vendor Lock-In: PASS. Codex-specific behavior stays in provider registry/verifier branches, not core workflow semantics.
- Principle IV Own Your Data: PASS. Credential refs and artifacts stay on operator-controlled volumes/storage.
- Principle V Skills Are First-Class: PASS. No changes to agent-skill runtime.
- Principle VI Replaceable Scaffolding With Thick Contracts: PASS. Adds tests around API/UI/runtime contracts before implementation.
- Principle VII Runtime Configurability: PASS. Reuses provider/profile configuration and existing defaults.
- Principle VIII Modular Architecture: PASS. Changes remain in Settings component, OAuth API/service, provider registry, and verifier boundaries.
- Principle IX Resilient By Default: PASS. Lifecycle/cancel/retry/cleanup tests are required; workflow payload compatibility is preserved.
- Principle X Continuous Improvement: PASS. Verification output must preserve exact evidence/blockers.
- Principle XI Spec-Driven Development: PASS. This plan follows `spec.md` and will generate TDD tasks.
- Principle XII Canonical Docs vs Tmp: PASS. Runtime work stays in code/specs; no canonical docs migration narrative is added.
- Principle XIII Pre-release Compatibility Policy: PASS. No compatibility aliases planned; Codex inputs pass through explicit values.

## Project Structure

### Documentation (this feature)

```text
specs/205-finish-codex-oauth-terminal/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── oauth-terminal-settings-flow.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/oauth_sessions.py
├── api/schemas_oauth_sessions.py
└── services/oauth_session_service.py

moonmind/workflows/temporal/
├── runtime/providers/registry.py
├── runtime/providers/volume_verifiers.py
└── workflows/oauth_session.py

frontend/src/
├── components/settings/ProviderProfilesManager.tsx
├── components/settings/ProviderProfilesManager.test.tsx
└── entrypoints/oauth-terminal.tsx

tests/
├── unit/api_service/api/routers/test_oauth_sessions.py
├── unit/auth/test_oauth_provider_registry.py
├── unit/auth/test_volume_verifiers.py
└── integration/temporal/test_oauth_session.py
```

**Structure Decision**: Use existing full-stack MoonMind layout. Keep Settings UI orchestration in `ProviderProfilesManager`, OAuth lifecycle in existing API/service/workflow modules, Codex-specific auth behavior in provider registry/verifier modules, and tests at the real UI/API/runtime boundaries.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |
