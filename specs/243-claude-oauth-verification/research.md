# Research: Claude OAuth Verification and Profile Registration

## FR-001 / DESIGN-REQ-003

Decision: implemented_unverified; add Claude route-boundary coverage.
Evidence: `api_service/api/routers/oauth_sessions.py` calls `verify_volume_credentials` before building or mutating `ManagedAgentProviderProfile`.
Rationale: The ordering is visible in code, but existing successful registration coverage is Codex-specific.
Alternatives considered: Treat generic Codex route test as sufficient. Rejected because MM-480 requires Claude profile fields and runtime sync evidence.
Test implications: Route unit test.

## FR-002 / FR-003 / DESIGN-REQ-004

Decision: partial; update Claude credential artifact detection.
Evidence: `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py` currently defines Claude paths as `.claude/credentials.json` and `.claude.json`, while MM-480 requires verification under mounted Claude home using `credentials.json` and qualifying `settings.json`.
Rationale: The auth volume is mounted at `/home/app/.claude`, so checks should evaluate artifacts relative to that mounted home, not a nested `.claude` path.
Alternatives considered: Accept current `.claude/credentials.json` as a compatible extra path. Rejected for this story because internal pre-release policy favors the superseded pattern being replaced rather than hidden compatibility aliases.
Test implications: Unit tests for `credentials.json`, qualifying `settings.json`, and non-qualifying `settings.json`.

## FR-004 / FR-005 / DESIGN-REQ-013

Decision: implemented_unverified; add Claude-specific no-leak tests.
Evidence: `_verification_result` returns only verified state, status, runtime ID, reason, and counts. Existing tests assert `found` and `missing` are not returned for generic providers and Codex.
Rationale: The result shape is already compact, but settings qualification adds a new parsing path that must not leak values.
Alternatives considered: Rely on existing generic no-leak tests. Rejected because the new Claude settings check is the highest-risk path for accidentally exposing auth material.
Test implications: Unit tests assert no raw setting values, artifact paths, or token-like content appear in verifier results.

## FR-006

Decision: implemented_verified; no new implementation planned.
Evidence: `tests/unit/api_service/api/routers/test_oauth_sessions.py::test_finalize_oauth_session_rejects_failed_volume_verification` verifies failed volume verification marks the session failed, stops the auth runner, signals workflow failure, and does not register a profile.
Rationale: The failure behavior is runtime-neutral and already covered at the route boundary.
Alternatives considered: Duplicate the failure test for Claude. Deferred unless implementation changes route behavior.
Test implications: Existing route unit plus final focused suite.

## FR-007 / FR-008 / DESIGN-REQ-014 / DESIGN-REQ-016

Decision: implemented_unverified; add Claude successful finalization route test.
Evidence: Finalize route writes `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, session volume refs, and calls `sync_provider_profile_manager(runtime_id=session_obj.runtime_id)`. Existing successful test covers Codex only.
Rationale: Claude profile defaults and sync target must be proven for `claude_code` and `claude_anthropic`.
Alternatives considered: Trust the generic route. Rejected because MM-480 explicitly names Claude profile shape and manager sync.
Test implications: Route unit test with mocked verifier and manager sync.

## FR-009 / DESIGN-REQ-018

Decision: implemented_unverified; add focused authorization or owner-scope assertion.
Evidence: Finalize route fetches sessions by `session_id` and `requested_by_user_id`; provider update checks owner before mutation.
Rationale: Existing route scope suggests unauthorized attempts fail, but MM-480 requires explicit finalize or repair rejection evidence.
Alternatives considered: Use provider profile auth tests only. Rejected because finalization is the requested boundary.
Test implications: Route unit test for another user's session or unauthorized profile update.

## FR-010

Decision: implemented_unverified; assert ref-only profile persistence in Claude route test.
Evidence: Provider profile model stores refs and metadata, not file contents. Finalize route response is `{"status": "succeeded"}`.
Rationale: Claude-specific test should confirm stored OAuth profile fields are refs only.
Alternatives considered: Rely on schema shape. Rejected to keep MM-480 evidence concrete.
Test implications: Route unit test.

## FR-011

Decision: missing; preserve traceability through remaining artifacts and final report.
Evidence: `spec.md` preserves MM-480 and original Jira input.
Rationale: Tasks, verification, commit/PR metadata, and final report still need to carry the issue key.
Alternatives considered: Limit traceability to spec. Rejected because the Jira brief explicitly requires downstream references.
Test implications: Final verification.
