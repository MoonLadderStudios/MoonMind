# Research: Claude Settings Credential Actions

## Classification

Decision: Single-story runtime feature request.
Evidence: `spec.md` (Input) describes one operator-visible Settings behavior: choosing between Claude OAuth and Anthropic API-key credential methods from the Provider Profiles table.
Rationale: The request is independently testable through one Settings provider profile row and does not require splitting across backend launch behavior, OAuth backend implementation, or provider profile materialization.
Alternatives considered: Treating the source design as a broad declarative design was rejected because the Jira brief selected only sections 1, 3.1, 3.2, and 8 for one UI method-selection story.
Test implications: Unit tests, backend provider-profile route tests, and integration-style UI tests.

## FR-003 / DESIGN-REQ-002 OAuth Action Label

Decision: Implemented verified. Preserve a `Connect with Claude OAuth` action for supported `claude_anthropic` rows.
Evidence: `ProviderProfilesManager.tsx` supports Codex OAuth via `isCodexOAuthProfile` and now treats trusted Claude credential-method metadata or the canonical `claude_anthropic` OAuth profile shape as Claude OAuth support. The auto-seeded `claude_anthropic` profile in `api_service/main.py` has `credential_source=oauth_volume` and `runtime_materialization_mode=oauth_home`.
Rationale: The canonical OAuth profile shape is already enough trusted metadata to identify Claude OAuth support, while command behavior can provide explicit action flags where present.
Alternatives considered: Requiring new backend metadata before rendering the action was rejected because the OAuth profile shape already carries credential source, materialization mode, runtime, provider, and volume fields.
Test implications: Preserve UI tests for the action label and OAuth request payload.

## FR-004 / DESIGN-REQ-005 API-Key Action Label

Decision: Implemented verified. Preserve `Use Anthropic API key` as a distinct Claude action that opens the existing API-key/manual-auth drawer.
Evidence: `ProviderProfilesManager.tsx` posts to `/api/v1/provider-profiles/{profile_id}/manual-auth/commit`, and `provider_profiles.py` stores the token in Managed Secrets, sets `runtime_materialization_mode=api_key_env`, configures `ANTHROPIC_API_KEY` materialization, and returns Claude credential-method status metadata.
Rationale: The backend path already satisfied the storage/materialization target; the original implementation gap was the row-level method distinction and label.
Alternatives considered: Creating a second API-key drawer was rejected as duplicate scope. Reusing the existing drawer keeps this story focused on method selection.
Test implications: Preserve UI tests that the API-key action opens the drawer and does not call `/api/v1/oauth-sessions`, plus backend route tests for `ANTHROPIC_API_KEY` materialization metadata.

## FR-005 OAuth Session Routing

Decision: Implemented verified. Preserve routing of the Claude OAuth action through the existing OAuth session mutation.
Evidence: `startOAuthMutation` is runtime-neutral in request construction, and the Claude credential-method action model now routes `connect_oauth` through the same OAuth session mutation for the selected provider profile.
Rationale: The OAuth Session API accepts runtime/profile payloads and should remain the shared entrypoint for volume-backed CLI OAuth runtimes.
Alternatives considered: Adding a separate Claude OAuth frontend mutation was rejected because the existing mutation already carries runtime/profile/volume fields.
Test implications: Preserve integration-style UI test coverage for the `/api/v1/oauth-sessions` payload with `runtime_id=claude_code`.

## FR-007 / FR-008 OAuth Lifecycle Labels

Decision: Implemented verified. Preserve metadata-driven `Validate OAuth` and `Disconnect OAuth` labels.
Evidence: Claude credential-method actions now map validation and disconnect metadata to OAuth-specific labels required by the MM-477 brief.
Rationale: The label must distinguish OAuth volume validation/disconnect from API-key/token lifecycle.
Alternatives considered: Reusing `Validate` and `Disconnect` was rejected because it conflicts with the source requirement to avoid confusing credential methods.
Test implications: Preserve row rendering tests for supported and unsupported metadata.

## FR-009 Unsupported Metadata

Decision: Implemented verified. Preserve fail-closed behavior for unsupported or metadata-free Claude rows.
Evidence: Tests hide Claude credential-method actions when trusted credential-method metadata is absent.
Rationale: Unsupported profile rows should fail closed to avoid misleading credential operations.
Alternatives considered: Showing default actions for every Claude runtime/provider pair was rejected for non-`claude_anthropic` rows; the canonical `claude_anthropic` OAuth profile remains an explicit exception because it is the source-design target profile.
Test implications: Preserve unsupported row test coverage.

## FR-013 Codex Regression

Decision: Implemented verified. Preserve existing Codex OAuth behavior and tests.
Evidence: Existing `ProviderProfilesManager.test.tsx` starts a Codex OAuth session, finalizes, retries, and asserts request payloads and terminal launch.
Rationale: MM-477 is Claude-specific and must not regress Codex OAuth.
Alternatives considered: Renaming Codex `Auth` was rejected as out of scope.
Test implications: Keep existing tests passing.
