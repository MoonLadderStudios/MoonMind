# Research: Claude Settings Credential Actions

## Classification

Decision: Single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-477-moonspec-orchestration-input.md` describes one operator-visible Settings behavior: choosing between Claude OAuth and Anthropic API-key credential methods from the Provider Profiles table.
Rationale: The request is independently testable through one Settings provider profile row and does not require splitting across backend launch behavior, OAuth backend implementation, or provider profile materialization.
Alternatives considered: Treating the source design as a broad declarative design was rejected because the Jira brief selected only sections 1, 3.1, 3.2, and 8 for one UI method-selection story.
Test implications: Unit and integration-style UI tests.

## FR-003 / DESIGN-REQ-002 OAuth Action Label

Decision: Partial. Add a `Connect with Claude OAuth` action for supported `claude_anthropic` rows.
Evidence: `ProviderProfilesManager.tsx` currently supports Codex OAuth via `isCodexOAuthProfile`; Claude manual rows use `Connect Claude`. The auto-seeded `claude_anthropic` profile in `api_service/main.py` has `credential_source=oauth_volume` and `runtime_materialization_mode=oauth_home`.
Rationale: The canonical OAuth profile shape is already enough trusted metadata to identify Claude OAuth support, while command behavior can provide explicit action flags where present.
Alternatives considered: Requiring new backend metadata before rendering the action was rejected because the OAuth profile shape already carries credential source, materialization mode, runtime, provider, and volume fields.
Test implications: Add UI test for action label and OAuth request payload.

## FR-004 / DESIGN-REQ-005 API-Key Action Label

Decision: Partial. Add `Use Anthropic API key` as a distinct Claude action that opens the existing API-key/manual-auth drawer.
Evidence: `ProviderProfilesManager.tsx` already posts to `/api/v1/provider-profiles/{profile_id}/manual-auth/commit`, and `provider_profiles.py` stores the token in Managed Secrets, sets `runtime_materialization_mode=api_key_env`, and configures `ANTHROPIC_API_KEY` materialization.
Rationale: The backend path already satisfies the storage/materialization target; the missing behavior is the row-level method distinction and label.
Alternatives considered: Creating a second API-key drawer was rejected as duplicate scope. Reusing the existing drawer keeps this story focused on method selection.
Test implications: Add UI test that the API-key action opens the drawer and does not call `/api/v1/oauth-sessions`.

## FR-005 OAuth Session Routing

Decision: Missing. Route the Claude OAuth action through the existing OAuth session mutation.
Evidence: `startOAuthMutation` is runtime-neutral in request construction, but the row currently sets `canStartOAuth` only for `authModel.kind === 'codex_oauth'`.
Rationale: The OAuth Session API accepts runtime/profile payloads and should remain the shared entrypoint for volume-backed CLI OAuth runtimes.
Alternatives considered: Adding a separate Claude OAuth frontend mutation was rejected because the existing mutation already carries runtime/profile/volume fields.
Test implications: Add integration-style UI test for `/api/v1/oauth-sessions` payload with `runtime_id=claude_code`.

## FR-007 / FR-008 OAuth Lifecycle Labels

Decision: Missing. Add metadata-driven `Validate OAuth` and `Disconnect OAuth` labels.
Evidence: Existing Claude manual action labels are `Validate` and `Disconnect`; the MM-477 brief requires OAuth-specific labels.
Rationale: The label must distinguish OAuth volume validation/disconnect from API-key/token lifecycle.
Alternatives considered: Reusing `Validate` and `Disconnect` was rejected because it conflicts with the source requirement to avoid confusing credential methods.
Test implications: Add row rendering tests for supported and unsupported metadata.

## FR-009 Unsupported Metadata

Decision: Implemented for the old manual action set, unverified for the new credential method action set.
Evidence: Existing tests hide Claude actions when `command_behavior` is empty. New OAuth/API-key action IDs need equivalent coverage.
Rationale: Unsupported profile rows should fail closed to avoid misleading credential operations.
Alternatives considered: Showing default actions for every Claude runtime/provider pair was rejected for non-`claude_anthropic` rows; the canonical `claude_anthropic` OAuth profile remains an explicit exception because it is the source-design target profile.
Test implications: Add unsupported row test.

## FR-013 Codex Regression

Decision: Implemented verified. Preserve existing Codex OAuth behavior and tests.
Evidence: Existing `ProviderProfilesManager.test.tsx` starts a Codex OAuth session, finalizes, retries, and asserts request payloads and terminal launch.
Rationale: MM-477 is Claude-specific and must not regress Codex OAuth.
Alternatives considered: Renaming Codex `Auth` was rejected as out of scope.
Test implications: Keep existing tests passing.
