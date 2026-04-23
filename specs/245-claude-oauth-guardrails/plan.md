# Implementation Plan: Claude OAuth Authorization and Redaction Guardrails

**Branch**: `245-claude-oauth-guardrails` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/245-claude-oauth-guardrails/spec.md`

## Summary

Implement MM-482 by hardening the cross-cutting Claude OAuth guardrails after the session-backend, browser-sign-in, verification, and runtime-launch stories. Repo inspection shows significant coverage already exists: owner-scoped create/cancel/finalize/reconnect routes, one-time hashed attach tokens, redacted OAuth failure reasons, provider-profile response redaction, Claude verification/profile registration, Claude launch-home materialization, and no-leak diagnostics around auth paths. The remaining delivery risk is boundary completeness rather than greenfield functionality: Claude-specific proof is missing for reconnect-as-repair authorization, terminal WebSocket rejection after single-use token consumption, auth-volume treatment as credential-store-only across operator-visible surfaces, and end-to-end redaction/authorization evidence that spans the full Claude OAuth lifecycle. The plan is therefore test-first around the real API, WebSocket, provider-profile, workflow/activity, and launch boundaries, with production changes only where those tests expose a gap.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| -- | -- | -- | -- | -- |
| FR-001 | partial | `api_service/api/routers/oauth_sessions.py` scopes create/cancel/finalize/reconnect to `requested_by_user_id`; `tests/unit/api_service/api/routers/test_oauth_sessions.py::test_finalize_oauth_session_rejects_other_users_claude_session_before_verify` proves finalize ownership only | add Claude-focused authorization tests covering create/attach/cancel/reconnect-as-repair; implement missing owner or fail-closed checks only if tests expose a gap | unit + integration contingency |
| FR-002 | implemented_unverified | attach/finalize/reconnect routes all filter by `requested_by_user_id`; WebSocket bridge binds `owner_user_id` from session metadata | add direct boundary proof that Claude terminal attachment is owner-scoped end to end and not bypassable through the WebSocket attach path | unit |
| FR-003 | partial | `attach_oauth_terminal` hashes tokens and marks `terminal_attach_token_used=False`; `oauth_terminal_websocket` rejects missing/used/expired token; tests cover hash-only token issuance and expired-session rejection, but not successful consume-then-reuse denial for Claude | add WebSocket or route-boundary tests for single-use consumption and replay rejection; implement only if the consumed-token path leaks | unit |
| FR-004 | implemented_unverified | `moonmind/utils/logging.py`, `api_service/api/routers/oauth_sessions.py`, `api_service/api/routers/provider_profiles.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/temporal/runtime/launcher.py` already redact secret-like text and auth paths | add Claude-focused no-leak tests spanning failure reasons, terminal metadata, provider-profile payloads, verification output, and launch errors; patch any unredacted operator-visible surface | unit + integration contingency |
| FR-005 | implemented_unverified | `tests/unit/api_service/api/routers/test_provider_profiles.py::test_provider_profile_response_redacts_secret_like_runtime_fields`; Claude finalize test proves ref-only profile persistence | add Claude-specific profile and verification-surface assertions so MM-482 proves refs/metadata-only behavior for Claude OAuth surfaces rather than relying on generic profile redaction alone | unit |
| FR-006 | partial | `MANAGED_AUTH_VOLUME_PATH`, `CLAUDE_HOME`, and `CLAUDE_VOLUME_PATH` separation exists in launcher/materializer code; `tests/unit/services/temporal/runtime/test_launcher.py` and `test_agent_runtime_activities.py` prove mount-path separation for launch; no direct Claude OAuth proof spans verification, validation, audit, and artifact-adjacent surfaces | add tests proving auth volume is treated as credential storage only across launch diagnostics, provider validation, and terminal/session metadata; implement explicit exclusion if any surface treats it as workspace/artifact content | unit + integration contingency |
| FR-007 | partial | existing tests cover route boundaries, terminal frame filtering, provider-profile redaction, Claude verification, and Claude launch materialization, but not yet as one MM-482 guardrail matrix | create the MM-482 guardrail test plan covering API route, WebSocket/terminal bridge, provider-profile, verification, and runtime-launch boundaries; add production fixes only where those tests fail | unit + integration contingency |
| FR-008 | implemented_verified | `specs/245-claude-oauth-guardrails/spec.md` preserves MM-482 and the original Jira preset brief | preserve MM-482 through tasks, verification, and any implementation notes | none beyond final verify |
| SC-001 | implemented_unverified | Claude finalize ownership test exists; create/cancel/reconnect/attach authorization coverage is incomplete for Claude | add focused route/WebSocket tests before code changes | unit |
| SC-002 | partial | attach route and WebSocket logic support single-use tokens; tests do not yet prove post-consumption replay rejection for Claude | add token-consumption and replay tests; fix only if replay succeeds unexpectedly | unit |
| SC-003 | implemented_unverified | generic and Claude-specific redaction tests exist across several surfaces but not yet as a guardrail set for Claude OAuth lifecycle outputs | add focused redaction tests for Claude terminal, route, verification, and launch outputs | unit |
| SC-004 | implemented_unverified | generic provider-profile redaction and Claude finalize/profile registration tests exist | add Claude verification/profile payload assertions that prove refs-and-metadata-only behavior | unit |
| SC-005 | partial | Claude launch tests prove auth mount separation at launch time; auth-volume treatment across validation/audit/operator metadata is not yet fully proven | add cross-surface credential-store tests and implement exclusion guards if needed | unit + integration contingency |
| SC-006 | partial | coverage exists at several real boundaries but not yet organized to prove every MM-482 guardrail at a real boundary | write MM-482 tasks that keep tests at routes, WebSocket bridge, provider-profile, workflow/activity, and launcher boundaries | unit + integration contingency |
| DESIGN-REQ-004 | implemented_unverified | provider-profile rows, redaction helpers, and Claude finalize behavior already avoid credential-bearing profile payloads | add Claude-specific verification/profile surface proof | unit |
| DESIGN-REQ-009 | partial | OAuth session workflow enters `awaiting_user`; attach route supports `AWAITING_USER`; token hashing exists; replay-proof evidence is incomplete | add attach-token lifecycle tests at route/WebSocket boundary | unit |
| DESIGN-REQ-013 | implemented_unverified | verifier, launch, and API surfaces use secret redaction helpers | add MM-482-specific no-leak tests for all Claude operator-visible outputs in scope | unit |
| DESIGN-REQ-016 | partial | owner checks exist for create/cancel/finalize/reconnect and profile management; Claude coverage is incomplete across all lifecycle actions | add Claude-focused auth tests for the remaining lifecycle actions and repair-like reconnect path | unit |
| DESIGN-REQ-017 | partial | token hashing and consume-on-connect logic exist; no direct test yet proves replay is denied after first successful connect | add WebSocket replay-denial test and implement only if current logic fails | unit |
| DESIGN-REQ-018 | partial | Claude launch and provider-profile surfaces already keep auth paths sanitized and separate from workspaces, but cross-surface credential-store-only proof remains incomplete | add tests for auth-volume treatment across validation, launch diagnostics, and operator-visible metadata; implement explicit guards if any surface leaks workspace/artifact semantics | unit + integration contingency |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, pytest, existing OAuth session/terminal bridge/runtime-launch services  
**Storage**: Existing OAuth session rows, provider-profile rows, managed-session diagnostics, artifact metadata, and workflow history; no new persistent tables planned  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` with focused targets under `tests/unit/api_service/api/routers/`, `tests/unit/services/temporal/runtime/`, and `tests/unit/workflows/temporal/`  
**Integration Testing**: `./tools/test_integration.sh` only if changes affect hermetic API/artifact/worker seams beyond current in-process boundary tests  
**Target Platform**: Linux API and worker containers in MoonMind-managed runtime environments  
**Project Type**: FastAPI control plane plus Temporal-backed OAuth/session/runtime orchestration  
**Performance Goals**: Guardrail enforcement remains bounded to existing route, WebSocket, verification, and launch surfaces without introducing new persistent storage or extra provider round-trips  
**Constraints**: No raw credential contents, raw auth-volume paths below the mount root, token values, environment dumps, or durable terminal-input storage in operator-visible surfaces; preserve existing MM-478 through MM-481 behavior while strengthening guardrails; no compatibility aliases or hidden fallback semantics  
**Scale/Scope**: One runtime (`claude_code`), one OAuth-backed profile (`claude_anthropic`), one OAuth session/terminal flow, one verification boundary, one runtime launch path, one cross-cutting guardrail story  

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan strengthens existing OAuth session, terminal, provider-profile, and launch boundaries rather than introducing a parallel auth system.
- II. One-Click Agent Deployment: PASS. No new deployment dependencies or operator-managed services are introduced.
- III. Avoid Vendor Lock-In: PASS. Claude-specific rules remain inside runtime/provider boundaries and shared redaction utilities.
- IV. Own Your Data: PASS. Credential material remains in operator-controlled auth volumes; only redacted metadata crosses boundaries.
- V. Skills Are First-Class: PASS. MoonSpec artifacts preserve MM-482 traceability for downstream automation.
- VI. Bittersweet Lesson: PASS. The work is test-first around stable contracts and keeps scaffolding thin.
- VII. Runtime Configurability: PASS. Existing profile/env-driven control surfaces remain the source of truth.
- VIII. Modular Architecture: PASS. Planned work stays inside routes, terminal bridge, redaction helpers, provider-profile serialization, and launch/session activities.
- IX. Resilient by Default: PASS. The plan emphasizes fail-closed authorization, one-time token use, and sanitized operator-visible failures.
- X. Continuous Improvement: PASS. The requirement-status table and quickstart commands preserve verification evidence.
- XI. Spec-Driven Development: PASS. This plan follows one independently testable MM-482 story.
- XII. Canonical Docs vs Tmp: PASS. `docs/ManagedAgents/ClaudeAnthropicOAuth.md` remains the source design and the Jira brief stays under `docs/tmp`.
- XIII. Pre-Release Velocity: PASS. No compatibility wrappers or partial migrations are proposed.

## Project Structure

### Documentation

```text
specs/245-claude-oauth-guardrails/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-oauth-guardrails.md
└── tasks.md
```

### Source Code

```text
api_service/api/routers/oauth_sessions.py
api_service/api/routers/provider_profiles.py
api_service/services/provider_profile_service.py
moonmind/utils/logging.py
moonmind/workflows/temporal/workflows/oauth_session.py
moonmind/workflows/temporal/runtime/terminal_bridge.py
moonmind/workflows/temporal/activity_runtime.py
moonmind/workflows/adapters/materializer.py
moonmind/workflows/adapters/managed_agent_adapter.py
moonmind/workflows/temporal/runtime/launcher.py

 tests/unit/api_service/api/routers/test_oauth_sessions.py
 tests/unit/api_service/api/routers/test_provider_profiles.py
 tests/unit/services/temporal/runtime/test_terminal_bridge.py
 tests/unit/workflows/temporal/test_agent_runtime_activities.py
 tests/unit/services/temporal/runtime/test_launcher.py
```

**Structure Decision**: Add failing MM-482 guardrail tests first in the existing route, provider-profile, terminal-bridge, launch, and activity suites, then make the smallest production changes in shared authorization/redaction/materialization boundaries only where those tests expose a gap.

## Complexity Tracking

No constitution violations.
