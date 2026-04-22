# Story Breakdown: Claude Anthropic Settings Authentication

- Source design: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source reference path: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Extraction date: `2026-04-22T07:15:57Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design moves claude_anthropic authentication into the existing Settings -> Providers & Secrets provider-profile surface as a manual token enrollment flow. Instead of forcing Anthropic paste-back authentication through the current volume-shaped OAuth terminal session path, MoonMind should validate a pasted token, store it in Managed Secrets, bind the claude_anthropic profile through secret_ref, and launch Claude Code by materializing ANTHROPIC_API_KEY through api_key_env. The design preserves existing Provider Profiles architecture, separates Claude from Codex terminal OAuth semantics, requires secret-safe readiness feedback and logging behavior, and leaves session-history parity as optional manual-token audit work.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Settings Providers & Secrets remains the home** — Claude Anthropic authentication must live inside Settings -> Providers & Secrets -> Provider Profiles, not in a separate Claude auth page. Source: 1. Why this design is needed; 2.1 Settings surface; 5.1 Placement.
- `DESIGN-REQ-002` (state-model): **Provider Profiles model already supports target shape** — The implementation should use the existing provider profile concepts for runtime_id, provider_id, credential_source, runtime_materialization_mode, secret_refs, env_template, clear_env_keys, tags, priority, and account label. Source: 2.2 Provider profile architecture; 3. Design decision; 4. Desired profile shape.
- `DESIGN-REQ-003` (requirement): **Claude must become Settings-auth capable** — ProviderProfilesManager must stop limiting inline auth actions to Codex and must dispatch auth actions by runtime, provider, and credential strategy so claude_anthropic can expose its own enrollment flow. Source: 2.3 Current Settings UI implementation; 10.1 Frontend.
- `DESIGN-REQ-004` (constraint): **Do not reuse volume-first OAuth session finalization** — The Anthropic paste-back-token flow must not be forced through a backend path that requires Docker volumes, mounted credential files, oauth_volume, or oauth_home finalization. Source: 2.4 Current OAuth session backend; 3. Design decision; 6.1 Do not reuse /api/v1/oauth-sessions as-is.
- `DESIGN-REQ-005` (requirement): **Manual token enrollment is the primary Claude flow** — Mission Control should guide the operator through opening the external Anthropic flow, pasting the returned token, validating it, saving it, updating the profile, and showing readiness or failure. Source: 3. Design decision; 5.3 Modal / drawer flow; 6.2 Add a separate manual-auth path.
- `DESIGN-REQ-006` (state-model): **Target profile uses secret_ref and api_key_env** — The resulting claude_anthropic profile should use credential_source=secret_ref, runtime_materialization_mode=api_key_env, a secret ref such as anthropic_api_key, clear_env_keys, and an env template that injects ANTHROPIC_API_KEY from the secret. Source: 4. Desired profile shape; 8. Runtime launch behavior.
- `DESIGN-REQ-007` (requirement): **Claude-specific row actions and labels** — The row action should use Claude-specific labels and behavior such as Connect Claude, Replace token, Validate, and Disconnect instead of a generic or Codex-labeled Auth flow. Source: 5.2 Row-level action model; 10.1 Frontend.
- `DESIGN-REQ-008` (state-model): **Enrollment UI state machine** — The Settings flow should expose states including not_connected, awaiting_external_step, awaiting_token_paste, validating_token, saving_secret, updating_profile, ready, and failed without presenting them as terminal OAuth session states. Source: 5.3 Modal / drawer flow.
- `DESIGN-REQ-009` (observability): **Validation and readiness feedback** — Settings must show connected state, last validated timestamp, failure reason, backing secret existence, and whether the profile is launch-ready. Source: 5.4 Validation feedback; 10.1 Frontend; 10.2 Backend.
- `DESIGN-REQ-010` (integration): **Manual-auth API validates and commits secret-free readiness** — A dedicated provider-profile manual auth endpoint or equivalent service path must validate caller permission and token format, run a safe validation probe, write/update Managed Secrets, create/update the provider profile, sync ProviderProfileManager, and return a secret-free readiness summary. Source: 6.2 Add a separate manual-auth path; 10.2 Backend.
- `DESIGN-REQ-011` (artifact): **Optional manual-token audit/session kind** — If session-history parity is included, auth sessions should distinguish auth_kind values such as oauth_terminal and manual_token instead of reusing PTY or terminal assumptions. Source: 6.3 Optional audit/session record; 10.3 Optional schema/session work.
- `DESIGN-REQ-012` (security): **Strict secret handling** — The token must live in the Secrets System, never in provider profile rows, never be returned to the browser after submission, never enter workflow payloads, and must be redacted from logs, notices, validation failures, and artifacts. Source: 7. Secrets handling; 7.1 Required rules; 7.2 Secret ownership.
- `DESIGN-REQ-013` (integration): **Profile-driven Claude launch** — Launching Claude Code should resolve the provider profile, apply clear_env_keys, resolve secret_refs, inject ANTHROPIC_API_KEY, and launch claude_code without introducing a new runtime-selection concept. Source: 8. Runtime launch behavior.
- `DESIGN-REQ-014` (constraint): **Avoid private Claude home format coupling** — The Settings flow should avoid generating or treating Claude home-directory files as authoritative for paste-back tokens, because that would couple MoonMind to Claude private file layout and muddy source-of-truth semantics. Source: 9. Why this is better than extending the current Claude volume flow.
- `DESIGN-REQ-015` (artifact): **Documentation updates explain the new flow** — The relevant Settings, Provider Profiles, OAuth Terminal, and focused Claude Settings Auth documentation should describe the manual-token flow, profile shape, separation from Codex terminal OAuth, and operator behavior. Source: 10.4 Docs; 11. Final recommendation.
- `DESIGN-REQ-016` (non-goal): **Existing Claude volume tooling remains secondary** — The existing tools/auth-claude-volume.sh path may remain useful for local/operator tooling but should not define the primary Settings auth experience for Anthropic paste-back token enrollment. Source: 2.5 Current Claude helper path; 9. Why this is better than extending the current Claude volume flow.

## Ordered Story Candidates

### STORY-001: Route claude_anthropic Settings auth actions to a Claude enrollment flow

- Short name: `claude-settings-auth`
- Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source sections: 2.1 Settings surface, 2.3 Current Settings UI implementation, 5.1 Placement, 5.2 Row-level action model, 10.1 Frontend
- Coverage: DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007
- Why: This is the smallest visible change that exposes claude_anthropic as an auth-capable profile in the canonical Settings surface.
- Independent test: Render ProviderProfilesManager with codex_default, claude_anthropic, and a non-auth-capable profile; verify only the correct rows expose the appropriate action labels and clicking claude_anthropic opens the Claude enrollment surface.
- Dependencies: None

Acceptance criteria:
- claude_anthropic exposes a Connect Claude action when not connected.
- Connected claude_anthropic rows expose Replace token, Validate, and Disconnect actions where supported by returned capability/readiness metadata.
- Codex OAuth behavior remains available for codex_default without reusing Codex labels for Claude.
- No new standalone Claude auth page or specs directory is created by this story.

Scope:
- Update ProviderProfilesManager auth capability detection to use runtime/provider/credential strategy instead of Codex-only logic.
- Render Claude-specific row actions for claude_anthropic, including Connect Claude before enrollment and Replace token, Validate, and Disconnect after enrollment.
- Keep the flow inside Settings -> Providers & Secrets -> Provider Profiles.

Out of scope:
- Backend token validation and secret writes.
- Runtime credential materialization.
- Creating a separate Claude authentication page.

### STORY-002: Provide a Claude manual token enrollment drawer with explicit lifecycle states

- Short name: `claude-token-drawer`
- Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source sections: 3. Design decision, 5.3 Modal / drawer flow, 5.4 Validation feedback, 10.1 Frontend
- Coverage: DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-009
- Why: The manual-token UX is different from terminal OAuth and needs an independently testable operator flow before the backend is wired end to end.
- Independent test: Using frontend tests, simulate opening Connect Claude, entering a token, mocking validate/commit responses, and verifying state transitions, secure token clearing, row status updates, and failure feedback.
- Dependencies: STORY-001

Acceptance criteria:
- The modal or drawer includes states equivalent to not_connected, awaiting_external_step, awaiting_token_paste, validating_token, saving_secret, updating_profile, ready, and failed.
- The UI does not describe Claude manual enrollment as a terminal OAuth session.
- Validation failures show a redacted failure reason without echoing the submitted token.
- The status column can display connected/not connected, last validated timestamp, failure reason, backing secret existence, and launch readiness when provided by the backend.

Scope:
- Add a focused modal or drawer for claude_anthropic manual token enrollment.
- Show the external-step instruction/link area, secure token paste field, validation progress, save/update progress, ready state, and failure state.
- Reflect readiness metadata in the Provider Profiles Status column.

Out of scope:
- Choosing the final upstream Anthropic validation implementation.
- Persisting audit/session history.
- Changing provider profile materialization semantics.

Needs clarification:
- [NEEDS CLARIFICATION] Exact Anthropic enrollment URL or command text may need product confirmation before final UI copy is locked.

### STORY-003: Add secret-safe Claude manual auth API and service behavior

- Short name: `claude-manual-auth-api`
- Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source sections: 3. Design decision, 4. Desired profile shape, 6.1 Do not reuse /api/v1/oauth-sessions as-is, 6.2 Add a separate manual-auth path, 7. Secrets handling, 10.2 Backend
- Coverage: DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-014
- Why: The backend is the security and source-of-truth boundary for the paste-back token workflow.
- Independent test: Call the new route or service with mocked permission, validation probe, secret store, and profile manager; verify persisted profile shape, secret write/update, sync call, redacted responses/errors, and rejection of invalid token or unauthorized caller.
- Dependencies: None

Acceptance criteria:
- The Claude manual auth path does not require volume_ref, volume_mount_path, mounted Docker files, oauth_volume, or oauth_home finalization.
- Successful commit stores the token only in Managed Secrets and stores only secret references in the provider profile row.
- The returned response contains readiness, validation timestamp/status, secret existence, and profile readiness without returning the token.
- Invalid tokens, failed upstream validation, and unauthorized callers fail without leaking submitted token material.
- Tests prove no raw token appears in profile rows, workflow-shaped payloads, route responses, logs captured by the test, or validation failure messages.

Scope:
- Add a dedicated provider-profile manual-auth endpoint or equivalent collapsed connect-claude-anthropic endpoint.
- Validate caller permission, token format, and the token through a safe upstream probe.
- Write or update the token in Managed Secrets under the applicable operator or workspace scope.
- Create or update claude_anthropic as secret_ref + api_key_env with the expected secret_refs, env_template, clear_env_keys, labels/tags, and enabled state.
- Sync ProviderProfileManager and return secret-free readiness metadata.
- Ensure logs, errors, artifacts, and browser responses never include the raw token.

Out of scope:
- Volume-backed credential finalization.
- Generating Claude home-directory credential files.
- Runtime launch execution beyond profile state needed for materialization.

Needs clarification:
- [NEEDS CLARIFICATION] The exact safe Anthropic validation probe may need confirmation against provider API behavior and rate limits.

### STORY-004: Launch Claude Code from the secret_ref provider profile

- Short name: `claude-secret-launch`
- Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source sections: 4. Desired profile shape, 8. Runtime launch behavior, 10.2 Backend, 11. Final recommendation
- Coverage: DESIGN-REQ-006, DESIGN-REQ-013
- Why: The auth work is only complete when the saved provider profile can actually materialize credentials for Claude runtime launch.
- Independent test: Use unit or adapter-boundary tests around the launch/materialization service with a claude_anthropic secret_ref profile and mocked secret resolver; assert clear_env_keys behavior, ANTHROPIC_API_KEY injection, missing-secret failure, and absence of raw secrets in workflow payload fixtures.
- Dependencies: STORY-003

Acceptance criteria:
- claude_anthropic launches through the existing profile-driven materialization path.
- ANTHROPIC_API_KEY is injected from the managed secret referenced by anthropic_api_key.
- ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, and OPENAI_API_KEY are cleared before launch when configured.
- No new runtime-selection concept is introduced.
- Missing or unreadable secret bindings produce actionable failure output without exposing secret values.

Scope:
- Verify or update profile-driven materialization so claude_anthropic secret_refs produce ANTHROPIC_API_KEY for claude_code.
- Apply clear_env_keys before injecting the secret-derived environment variable.
- Fail fast if the profile is missing the expected secret binding or the secret cannot be resolved.
- Keep runtime selection based on the existing profile-driven launch path.

Out of scope:
- Adding a new runtime selector concept.
- Changing Codex or volume-backed OAuth materialization behavior except where shared tests need updated fixtures.
- Running live Claude provider verification by default.

### STORY-005: Document Claude Anthropic manual-token Settings authentication

- Short name: `claude-auth-docs`
- Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source sections: 2.5 Current Claude helper path, 9. Why this is better than extending the current Claude volume flow, 10.4 Docs, 11. Final recommendation
- Coverage: DESIGN-REQ-015, DESIGN-REQ-016
- Why: The feature changes operator guidance and architecture semantics, so canonical docs need to distinguish desired state from old helper paths and optional local tooling.
- Independent test: Run documentation checks or targeted link/grep review to confirm all referenced docs describe manual token enrollment, secret_ref profile binding, separation from Codex terminal OAuth, and secondary status of auth-claude-volume.sh without contradictory volume-first Settings guidance.
- Dependencies: STORY-003, STORY-004

Acceptance criteria:
- Docs show claude_anthropic as a provider-profile-backed manual token enrollment flow in Settings -> Providers & Secrets.
- Docs show the target profile shape using secret_ref and api_key_env without raw credentials in profile rows.
- OAuth terminal docs no longer imply Claude paste-back tokens should use the volume-based oauth-sessions finalize path.
- Docs identify the Claude volume helper as optional/local tooling, not the primary Settings UX.
- Canonical docs remain desired-state focused; any phased migration details stay under docs/tmp if needed.

Scope:
- Update docs/UI/SettingsTab.md with Claude manual enrollment behavior and readiness feedback.
- Update docs/Security/ProviderProfiles.md with the secret_ref/api_key_env claude_anthropic shape.
- Update docs/ManagedAgents/OAuthTerminal.md to clarify that Claude manual token enrollment is separate from terminal OAuth.
- Add or update a focused Claude Anthropic Settings Auth doc.
- State that tools/auth-claude-volume.sh remains secondary local/operator tooling and not the primary Settings flow.

Out of scope:
- Migration checklist content in canonical docs beyond concise desired-state notes.
- Spec creation under specs/.
- Live provider instructions that require undocumented provider behavior.

### STORY-006: Optionally record Claude manual-token auth history without terminal assumptions

- Short name: `manual-auth-history`
- Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source sections: 6.3 Optional audit/session record, 10.3 Optional schema/session work
- Coverage: DESIGN-REQ-011
- Why: The design marks session-history parity as optional, but if implemented it needs explicit ownership so it does not distort OAuth terminal semantics.
- Independent test: If implemented, run backend schema/service tests that create both oauth_terminal and manual_token records and verify Claude manual-token events appear in history with redacted metadata and no terminal-specific fields required.
- Dependencies: STORY-003

Acceptance criteria:
- Manual-token history, when enabled, uses an explicit manual_token auth kind or equivalent discriminator.
- History records never store or return the raw token.
- Codex oauth_terminal history remains distinguishable from Claude manual-token history.
- The core Claude manual auth flow can ship without this story if product chooses to defer audit/session parity.

Scope:
- Introduce or extend auth-session metadata with an auth_kind that can distinguish oauth_terminal from manual_token.
- Record Claude manual enrollment start, validation, commit, failure, replace, and disconnect events without storing token values.
- Expose history in the existing session-history UX only when the product includes that parity surface.

Out of scope:
- Making audit/session history required for the first usable Claude connection.
- Reusing PTY, terminal bridge, or Docker-volume assumptions for manual token history.
- Persisting raw tokens or full provider responses in history records.

Needs clarification:
- [NEEDS CLARIFICATION] Whether auth-history parity is required for initial release or should remain a follow-up.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-003
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-003
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-003, STORY-004
- `DESIGN-REQ-007` -> STORY-001
- `DESIGN-REQ-008` -> STORY-002
- `DESIGN-REQ-009` -> STORY-002
- `DESIGN-REQ-010` -> STORY-003
- `DESIGN-REQ-011` -> STORY-006
- `DESIGN-REQ-012` -> STORY-003
- `DESIGN-REQ-013` -> STORY-004
- `DESIGN-REQ-014` -> STORY-003
- `DESIGN-REQ-015` -> STORY-005
- `DESIGN-REQ-016` -> STORY-005

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: None
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-003, STORY-004
- `STORY-006` depends on: STORY-003

## Out Of Scope

- Creating spec.md files or specs/ directories during breakdown: Breakdown output feeds later /speckit.specify runs; specify owns spec creation.
- Forcing Claude paste-back tokens into Docker auth volumes or generated Claude home files: The design explicitly rejects private file-layout coupling for this Settings flow.
- Live provider verification during breakdown: Breakdown is read-only analysis of the declarative design and does not require external credentials.

## Recommended First Story

`STORY-003` is the recommended first `/speckit.specify` target because the backend contract and secret-safe provider-profile source of truth unlock the UI, launch materialization, and docs stories while avoiding the rejected volume-first path.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
