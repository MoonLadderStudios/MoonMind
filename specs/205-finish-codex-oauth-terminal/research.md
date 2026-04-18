# Research: Finish Codex OAuth Terminal Flow

## FR-001 Settings Auth Action

Decision: Missing. Add an Auth action for Codex OAuth-capable provider profiles in Settings.
Evidence: `frontend/src/components/settings/ProviderProfilesManager.tsx` currently renders Edit, Enable/Disable, and Delete actions only.
Rationale: MM-402 requires Settings -> select profile -> Auth as the operator entry point.
Alternatives considered: Keep OAuth terminal accessible only through direct URL or API calls; rejected because the brief explicitly requires no manual API calls.
Test implications: Frontend unit tests.

## FR-002 OAuth Session Creation From Settings

Decision: Partial. Backend session creation exists; Settings caller is missing.
Evidence: `api_service/api/routers/oauth_sessions.py` exposes `POST /api/v1/oauth-sessions`; `ProviderProfilesManager.tsx` does not call it.
Rationale: Reuse existing API instead of adding a parallel auth path.
Alternatives considered: New settings-specific OAuth endpoint; rejected because existing endpoint already models the session contract.
Test implications: Frontend unit tests with fetch mocks; existing API unit tests remain relevant.

## FR-003 Terminal Attach Transport

Decision: Implemented but unverified for Settings flow. OAuth terminal entrypoint and attach endpoints exist.
Evidence: `frontend/src/entrypoints/oauth-terminal.tsx`; `api_service/api/routers/oauth_sessions.py` terminal attach/ws handlers; `tests/unit/api_service/api/routers/test_oauth_sessions.py` terminal attach/message tests.
Rationale: Use the existing terminal page by opening it for the created session and polling/finalizing from Settings.
Alternatives considered: Embed xterm directly inside Settings; deferred unless needed, because the existing OAuth terminal entrypoint already owns terminal attach behavior.
Test implications: Frontend unit test should verify Settings opens the terminal/session handoff and tracks status.

## FR-004 Codex Device-Code Bootstrap

Decision: Partial. Local Codex CLI exposes `codex login --device-auth`, while the registry uses `codex login`.
Evidence: `codex login --help` output includes `--device-auth`; `moonmind/workflows/temporal/runtime/providers/registry.py` uses `bootstrap_command=["codex", "login"]`.
Rationale: MM-402 requires deterministic device-code auth for Codex enrollment.
Alternatives considered: Continue relying on default `codex login`; rejected because default may prefer local browser callback behavior.
Test implications: Unit tests in `tests/unit/auth/test_oauth_provider_registry.py` and auth runner activity expectations.

## FR-005 Codex Verification Strength

Decision: Partial. Verifier checks expected file presence only.
Evidence: `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py` returns verified when any expected Codex credential file exists; tests assert file-count behavior.
Rationale: MM-402 asks for a stronger Codex-specific usable-auth check while still sanitizing output.
Alternatives considered: Run a live provider call; rejected for required CI because it needs external credentials/network. Prefer structural auth/config validation and optional documented live check.
Test implications: Unit tests for valid/invalid `auth.json`/`config.toml` shapes and redaction.

## FR-006/FR-007 Provider Profile Finalization

Decision: Implemented but unverified from Settings. Finalize endpoint writes OAuth-volume/OAuth-home fields and preserves policy values from session metadata.
Evidence: `api_service/api/routers/oauth_sessions.py` finalize endpoint; `tests/unit/api_service/api/routers/test_oauth_sessions.py` finalization tests.
Rationale: Keep Provider Profile as the source of truth and refresh Settings cache after success.
Alternatives considered: Store new auth records separately; rejected by source design.
Test implications: API regression plus frontend cache invalidation/notice tests.

## FR-008 Session State UX

Decision: Missing in Settings. Backend statuses exist but Settings has no OAuth session state model.
Evidence: `OAuthSessionStatus` enum and `OAuthSessionResponse`; no ProviderProfilesManager OAuth state.
Rationale: Operator needs clear progress/failure/cancel states from Settings.
Alternatives considered: Only show terminal window status; insufficient for finalization/retry/cancel from Settings.
Test implications: Frontend unit tests.

## FR-009 Interactive Transport Activation

Decision: Partial. Workflow supports `moonmind_pty_ws`, but API service passes `session_model.session_transport or "none"`, and created sessions default to `none`.
Evidence: `api_service/services/oauth_session_service.py`; `ManagedAgentOAuthSession.session_transport` default; `MoonMindOAuthSessionWorkflow` only launches runner when transport != `none`.
Rationale: Codex Settings Auth must activate terminal transport end to end.
Alternatives considered: Let workflow treat `none` as interactive for Codex; rejected because explicit transport metadata is clearer and already modeled.
Test implications: API/service unit tests and workflow boundary tests.

## FR-010/FR-012 Managed Runtime Boundary

Decision: Implemented/unverified for this story. Existing docs/specs/tests cover volume targeting and no generic terminal exposure.
Evidence: `docs/ManagedAgents/OAuthTerminal.md`; `tests/unit/api_service/api/routers/test_oauth_sessions.py` rejects generic exec frames; managed-session controller tests cover auth volume targeting.
Rationale: Implementation must avoid changing task execution transport.
Alternatives considered: none.
Test implications: Existing tests plus final verification.

## FR-011 Cleanup And Retry

Decision: Implemented but needs Settings coverage. Backend cancel/finalize/reconnect helpers exist.
Evidence: `cancel_oauth_session`, `_stop_oauth_auth_runner`, `reconnect_oauth_session` in OAuth router.
Rationale: Settings must expose and handle cancel/retry outcomes.
Alternatives considered: Force users to reload or use API; rejected by MM-402.
Test implications: Frontend unit plus API unit if gaps appear.

## FR-013 Secret-Free Output

Decision: Implemented/unverified for strengthened Codex verification. Existing response redaction and verifier count-only metadata exist.
Evidence: `test_oauth_session_response_redacts_secret_like_failure_reason`; `volume_verifiers.py` omits raw found/missing path lists.
Rationale: New Codex verification must preserve this boundary.
Alternatives considered: Return parsed credential metadata; only safe account labels may be returned later, not raw tokens.
Test implications: Unit tests with token-like invalid content.

## FR-014 End-to-End Evidence

Decision: Missing. Existing tests cover pieces, but no Settings-to-OAuth flow test.
Evidence: Mission Control OAuth terminal page test exists; ProviderProfilesManager tests focus profile CRUD.
Rationale: MM-402 requires end-to-end proof or a repeatable documented path.
Alternatives considered: Manual-only verification; allowed only as blocker documentation when Docker is unavailable, not as the only expected evidence.
Test implications: Focused Vitest, Python unit, and Docker-backed integration when available.
