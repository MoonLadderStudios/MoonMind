# Implementation Plan: Claude OAuth Verification and Profile Registration

**Branch**: `243-claude-oauth-verification` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/243-claude-oauth-verification/spec.md`

## Summary

Implement MM-480 by completing the Claude OAuth finalization boundary. The existing OAuth finalize route already verifies the auth volume before profile mutation, skips mutation on failed verification, writes OAuth-home provider profile fields, syncs Provider Profile Manager, and scopes finalization to the owning user. Repo gap analysis found the Claude verifier itself is incomplete: `claude_code` currently checks paths that do not match the mounted Claude home contract and does not validate qualifying `settings.json` evidence. The implementation will add failing unit tests for Claude credential artifact detection and focused route tests for successful Claude finalization, then update the verifier and any required finalization metadata handling.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| -- | -- | -- | -- | -- |
| FR-001 | implemented_verified | `api_service/api/routers/oauth_sessions.py`; `test_finalize_oauth_session_registers_claude_oauth_profile` proves verification happens before profile registration | completed | route unit |
| FR-002 | implemented_verified | `volume_verifiers.py`; `test_claude_verification_checks_credentials_at_mounted_home` verifies mounted-home path | completed | unit |
| FR-003 | implemented_verified | `volume_verifiers.py`; Claude verifier tests cover `credentials.json`, qualifying `settings.json`, and non-qualifying settings | completed | unit |
| FR-004 | implemented_verified | `_verification_result`; Claude verifier no-leak assertions prove compact metadata | completed | unit |
| FR-005 | implemented_verified | verifier and route tests assert no raw artifact paths/values or profile credential contents are exposed | completed | unit + route unit |
| FR-006 | implemented_verified | `test_finalize_oauth_session_rejects_failed_volume_verification` covers failed verification path | no new implementation | existing route unit |
| FR-007 | implemented_verified | `test_finalize_oauth_session_registers_claude_oauth_profile` asserts required OAuth-volume profile fields | completed | route unit |
| FR-008 | implemented_verified | `test_finalize_oauth_session_registers_claude_oauth_profile` asserts manager sync for `claude_code` | completed | route unit |
| FR-009 | implemented_verified | `test_finalize_oauth_session_rejects_other_users_claude_session_before_verify` proves unauthorized finalize stops before verification or mutation | completed | route unit |
| FR-010 | implemented_verified | Claude finalization route test asserts profile stores refs/metadata only | completed | route unit |
| FR-011 | implemented_verified | MoonSpec artifacts and final verification preserve MM-480 traceability | completed | verification |
| SC-001 | implemented_verified | Claude verifier tests cover documented artifacts and rejection of non-qualifying settings | completed | unit |
| SC-002 | implemented_verified | Claude verifier tests prove compact secret-free metadata | completed | unit |
| SC-003 | implemented_verified | route test proves verify-before-registration and failed verification test preserves no-mutation behavior | completed | route unit |
| SC-004 | implemented_verified | route test proves `claude_anthropic` registration/update fields and `claude_code` sync | completed | route unit |
| SC-005 | implemented_verified | unauthorized route test proves reject-before-verify behavior | completed | route unit |
| SC-006 | implemented_verified | focused and full unit suites passed | completed | unit |
| DESIGN-REQ-003 | implemented_verified | finalization route plus Claude route test verify before mutation | completed | route unit |
| DESIGN-REQ-004 | implemented_verified | verifier implementation and tests cover documented Claude artifacts | completed | unit |
| DESIGN-REQ-013 | implemented_verified | verifier implementation and tests prove secret-free metadata | completed | unit |
| DESIGN-REQ-014 | implemented_verified | Claude route test proves OAuth-backed profile fields | completed | route unit |
| DESIGN-REQ-016 | implemented_verified | Claude route test proves Provider Profile Manager sync | completed | route unit |
| DESIGN-REQ-018 | implemented_verified | unauthorized route test and ref-only assertions cover auth/ref requirements | completed | route unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, pytest  
**Storage**: Existing OAuth session and managed provider profile tables; no new persistent tables  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh <pytest targets>`  
**Integration Testing**: Route-level async pytest fixtures; hermetic integration through `./tools/test_integration.sh` if API or artifact lifecycle behavior changes  
**Target Platform**: Linux API and worker containers  
**Project Type**: FastAPI control plane plus Temporal-backed runtime services  
**Performance Goals**: Verification remains a bounded Docker volume check with a 30 second timeout; finalization remains a single route-level transaction path plus manager sync  
**Constraints**: No raw credential contents, tokens, environment dumps, raw auth-volume listings, or secret-bearing paths in browser-visible responses, artifacts, logs, workflow payloads, or provider profile rows; preserve existing Codex/Gemini OAuth verification behavior  
**Scale/Scope**: One runtime (`claude_code`), one provider profile (`claude_anthropic`), one OAuth finalization path

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Extends the existing OAuth session finalization and verifier boundary.
- II. One-Click Agent Deployment: PASS. Uses existing local Docker volume verification; no new external dependency.
- III. Avoid Vendor Lock-In: PASS. Claude-specific artifact checks stay in provider/runtime verifier configuration.
- IV. Own Your Data: PASS. Credential material stays in the operator-managed auth volume; only metadata leaves the verifier.
- V. Skills Are First-Class: PASS. MoonSpec artifacts preserve MM-480 traceability.
- VI. Bittersweet Lesson: PASS. Keeps the verifier and finalization contract thin and replaceable.
- VII. Runtime Configurability: PASS. Uses existing runtime registry defaults and session volume refs.
- VIII. Modular Architecture: PASS. Work stays in OAuth session route tests and provider volume verifier code.
- IX. Resilient by Default: PASS. Failed verification fails closed and skips profile mutation.
- X. Continuous Improvement: PASS. Verification evidence will be captured in tests and final report.
- XI. Spec-Driven Development: PASS. This plan follows a single-story spec.
- XII. Canonical Docs vs Tmp: PASS. Canonical docs are source requirements; Jira brief remains under `docs/tmp`.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases or hidden fallback semantics are introduced.

## Project Structure

### Documentation

```text
specs/243-claude-oauth-verification/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-oauth-verification.md
└── tasks.md
```

### Source Code

```text
api_service/api/routers/oauth_sessions.py
moonmind/workflows/temporal/runtime/providers/volume_verifiers.py
tests/unit/auth/test_volume_verifiers.py
tests/unit/api_service/api/routers/test_oauth_sessions.py
```

**Structure Decision**: Add focused verifier and route-boundary tests first. Production changes are expected in `volume_verifiers.py`; route code changes are contingency only if the Claude finalization test exposes a finalize/profile-registration gap.

## Complexity Tracking

No constitution violations.
