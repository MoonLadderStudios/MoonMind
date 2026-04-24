# Research: Claude Manual Auth API

## FR-001 / DESIGN-REQ-002 - Dedicated Manual Auth Commit Path

Decision: implemented_verified.
Evidence: `api_service/api/routers/provider_profiles.py` defines `POST /{profile_id}/manual-auth/commit` and `tests/unit/api_service/api/routers/test_provider_profiles.py` posts to that route.
Rationale: The route lives in the provider profile API boundary rather than the OAuth session router, matching the manual token enrollment design.
Alternatives considered: Reusing `/api/v1/oauth-sessions` was rejected by the source design because that path assumes Docker auth volumes and `oauth_home` finalization.
Test implications: Route/API unit coverage plus final verification.

## FR-002 - Provider Profile Authorization

Decision: implemented_verified.
Evidence: `commit_claude_manual_auth` calls `_require_profile_management`; `test_claude_manual_auth_commit_rejects_non_owner_without_validating_or_persisting` verifies unauthorized callers fail before validation or secret persistence.
Rationale: The story should use the established provider profile ownership boundary instead of creating a separate authorization model.
Alternatives considered: Adding a dedicated manual-auth authorization helper was rejected because it would duplicate profile management policy.
Test implications: Existing route authorization style plus final verification.

## FR-003 / SC-004 - Unsupported Profile Guard

Decision: implemented_verified.
Evidence: `_require_claude_anthropic_profile` rejects rows whose `runtime_id` is not `claude_code` or `provider_id` is not `anthropic`; `test_claude_manual_auth_commit_rejects_unsupported_profile_without_persisting` verifies unsupported profiles fail before validation or secret persistence.
Rationale: Fail-fast unsupported-profile behavior prevents accidental mutation of unrelated providers.
Alternatives considered: Accepting any provider profile with token-shaped input was rejected because it would blur provider-specific semantics.
Test implications: Route/API unit coverage and final verification should preserve this guard.

## FR-004 - Token Validation Boundary

Decision: implemented_verified.
Evidence: `_looks_like_claude_manual_token` performs local shape validation and `validate_claude_manual_token` performs a safe Anthropic model-list probe; the success route test monkeypatches validation and asserts the submitted token is passed to the validation boundary.
Rationale: The route should validate before storing a token while keeping tests hermetic.
Alternatives considered: Persisting first and validating later was rejected because invalid tokens should not create a ready binding.
Test implications: Route/API unit tests mock upstream validation; live provider checks remain outside required CI.

## FR-005 / DESIGN-REQ-012 - Managed Secret Storage Only

Decision: implemented_verified.
Evidence: `_upsert_managed_secret` stores the token in `ManagedSecret`; the success route test verifies the managed secret contains the submitted token while route response and profile response do not.
Rationale: The existing Managed Secrets table is the canonical local credential storage mechanism.
Alternatives considered: Storing a token directly in provider profile metadata was rejected by the source design and security guardrails.
Test implications: Route/API unit coverage proves token placement and response redaction.

## FR-006 / FR-007 / DESIGN-REQ-004 - Profile Launch Shape

Decision: implemented_verified.
Evidence: The route sets `credential_source=secret_ref`, `runtime_materialization_mode=api_key_env`, clears volume fields, writes `secret_refs`, writes conflict-clearing `clear_env_keys`, and sets an Anthropic API key `env_template`; the success route test verifies these fields.
Rationale: This matches the source design's desired profile shape while using the repository's valid `db://` slug reference format.
Alternatives considered: Preserving `oauth_volume` and writing generated Claude home files was rejected because it would couple MoonMind to private Claude home formats.
Test implications: Route/API unit coverage plus final verification.

## FR-008 / DESIGN-REQ-014 - ProviderProfileManager Sync

Decision: implemented_verified.
Evidence: The route calls `sync_provider_profile_manager`; the success route test monkeypatches the sync helper and verifies `claude_code` was synced.
Rationale: Runtime-visible provider profile state must be updated after profile mutation.
Alternatives considered: Relying on eventual polling was rejected because the existing provider profile manager exposes an explicit sync boundary.
Test implications: Route/API unit coverage verifies the sync call without requiring Temporal in unit tests.

## FR-009 - Secret-Free Readiness Response

Decision: implemented_verified.
Evidence: `ClaudeManualAuthCommitResponse` includes status, profile id, secret ref, and readiness metadata; the success route test verifies readiness flags and no submitted token in response text.
Rationale: Mission Control needs readiness metadata, not raw credentials.
Alternatives considered: Returning full profile rows directly was rejected because the response should stay narrow and secret-safe.
Test implications: Route/API unit coverage.

## FR-010 / DESIGN-REQ-006 - OAuth Session Separation

Decision: implemented_verified.
Evidence: The manual-auth route is implemented in `provider_profiles.py`; it does not call `oauth_sessions.py` and does not require volume refs or `oauth_home` finalization.
Rationale: The source design explicitly rejects forcing paste-back tokens through the volume-first OAuth session pipeline.
Alternatives considered: Extending OAuth session finalization was rejected for semantic mismatch.
Test implications: Final verification should confirm this separation remains true.

## FR-011 / FR-012 - Secret-Free Failure And Projection Behavior

Decision: implemented_verified.
Evidence: The malformed-token test verifies generic failure text, no persistence, and no raw token in response. The success route test verifies no raw token in fetched profile text. `moonmind/utils/logging.py` preserves non-secret `auth_*` metadata while continuing to redact actual token/key fields.
Rationale: Secret safety must cover success and failure paths.
Alternatives considered: Returning provider error bodies directly was rejected because upstream failures can include sensitive text.
Test implications: Route/API unit coverage plus final verification of traceability.

## FR-013 - Runtime `db://` Secret Resolution

Decision: implemented_verified.
Evidence: `moonmind/workflows/adapters/secret_boundary.py` parses `db://` refs and resolves by `ManagedSecret.slug`; `tests/unit/workflows/adapters/test_secret_redaction.py` verifies slug resolution.
Rationale: The profile binding produced by the route must be consumable by runtime materialization.
Alternatives considered: Storing legacy UUID-only references was rejected because provider profile validation already accepts normalized secret-ref strings.
Test implications: Adapter unit test coverage.

## FR-014 / SC-007 - Traceability

Decision: implemented_verified.
Evidence: `spec.md` (Input) and `specs/236-claude-manual-auth-api/spec.md` preserve MM-447 and source design mappings.
Rationale: MoonSpec verification and pull request metadata need the originating issue key.
Alternatives considered: Using only a branch name was rejected because issue traceability must be explicit in artifacts.
Test implications: Final MoonSpec verification.
