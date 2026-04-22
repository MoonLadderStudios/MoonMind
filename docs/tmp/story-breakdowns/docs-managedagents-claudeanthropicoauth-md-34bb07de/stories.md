# Claude Anthropic OAuth Story Breakdown

Source design: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
Original source document reference path: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
Story extraction date: 2026-04-22T21:51:30Z
Requested output mode: `jira`

Coverage gate result:

```text
PASS - every major design point is owned by at least one story.
```

## Design Summary

ClaudeAnthropicOAuth.md defines the desired Settings and runtime contract for Claude Code authentication against Anthropic accounts. The design keeps Claude OAuth inside the existing Provider Profiles surface, separates OAuth-volume enrollment from API-key secrets, reuses the OAuth Session API and PTY/WebSocket browser terminal for Claude's URL-plus-pasted-code ceremony, verifies auth volumes without exposing credential contents, registers oauth_home provider profiles, and materializes the selected profile into Claude task launches with strict authorization and redaction boundaries.

## Coverage Points

| ID | Type | Source Section | Design Point |
| --- | --- | --- | --- |
| DESIGN-REQ-001 | requirement | 1. Product Intent; 3.1 Placement | Claude Anthropic credential configuration belongs in Settings -> Providers & Secrets -> Provider Profiles, not a separate Claude auth page. |
| DESIGN-REQ-002 | requirement | 1. Product Intent; 8. API-Key Auth Is Separate | Operators can choose either Connect with Claude OAuth or Use Anthropic API key from the same Settings surface, with API-key enrollment remaining separate from OAuth terminal sessions. |
| DESIGN-REQ-003 | state-model | 2. OAuth Profile Shape | The claude_anthropic profile uses runtime_id claude_code, provider_id anthropic, credential_source oauth_volume, runtime_materialization_mode oauth_home, claude_auth_volume, /home/app/.claude, enabled metadata, tags, and clear_env_keys. |
| DESIGN-REQ-004 | security | 2. OAuth Profile Shape; 7. Runtime Launch Behavior; 9. Security Requirements | Claude credential files, tokens, environment dumps, and raw volume listings must not appear in workflow payloads, logs, artifacts, browser responses, or provider profile rows. |
| DESIGN-REQ-005 | requirement | 3.2 Row Actions | Claude Anthropic rows expose Connect with Claude OAuth, Use Anthropic API key, Validate OAuth, and Disconnect OAuth where supported, using Claude-specific labels rather than Codex-specific copy. |
| DESIGN-REQ-006 | integration | 3.3 OAuth Terminal Flow; 4. OAuth Session Backend | Connect with Claude OAuth opens an OAuth session anchored to the provider profile row and flows through the OAuth Session API, MoonMind.OAuthSession workflow, auth-runner container, PTY/WebSocket bridge, browser terminal, claude login, volume verification, and profile registration/update. |
| DESIGN-REQ-007 | requirement | 3.4 Claude Sign-In Ceremony | Claude OAuth uses a URL plus pasted token or authorization code ceremony, not Codex device-code behavior, and the terminal remains attached while the operator completes external Anthropic sign-in. |
| DESIGN-REQ-008 | state-model | 3.4 Claude Sign-In Ceremony | The OAuth session remains in an operator-waiting state such as awaiting_user while the operator signs in externally and returns a token or code to the terminal. |
| DESIGN-REQ-009 | security | 3.4 Claude Sign-In Ceremony | The pasted token or code is transient PTY input only and is never stored as a Managed Secret, returned through APIs, written to artifacts, or persisted in provider profile rows. |
| DESIGN-REQ-010 | integration | 4. OAuth Session Backend | Claude OAuth reuses the existing OAuth Session API and workflow family, including create, terminal attach, WebSocket attach, finalize, cancel, retry, and history endpoints where available. |
| DESIGN-REQ-011 | integration | 4. OAuth Session Backend | The provider registry entry for runtime_id claude_code declares oauth mode, moonmind_pty_ws transport, claude_auth_volume, /home/app/.claude, Anthropic provider metadata, claude login bootstrap command, and claude_config_exists success check. |
| DESIGN-REQ-012 | requirement | 4. OAuth Session Backend | The auth runner sets HOME, CLAUDE_HOME, and CLAUDE_VOLUME_PATH to the mounted Claude home and clears ANTHROPIC_API_KEY and CLAUDE_API_KEY during OAuth enrollment. |
| DESIGN-REQ-013 | verification | 5. Verification | Finalization verifies Claude account-auth material in the mounted Claude home using known artifacts while returning only secret-free metadata such as verified, status, reason, counts, and timestamps. |
| DESIGN-REQ-014 | integration | 6. Profile Registration | After verification succeeds, MoonMind registers or updates claude_anthropic with oauth_volume materialization fields and syncs Provider Profile Manager for runtime_id claude_code. |
| DESIGN-REQ-015 | requirement | 7. Runtime Launch Behavior | Claude task launch resolves claude_anthropic, applies clear_env_keys, mounts or projects claude_auth_volume at the configured Claude home, sets Claude home environment variables, and launches claude_code without leaking credential file contents. |
| DESIGN-REQ-016 | security | 9. Security Requirements | Only authorized operators can start, attach to, cancel, finalize, or repair Claude OAuth sessions; browser terminal attach tokens are short-lived and single-use. |
| DESIGN-REQ-017 | constraint | 3.3 OAuth Terminal Flow; 9. Security Requirements | The OAuth terminal is only for credential enrollment and repair, not ordinary Claude task execution; OAuth auth volumes are credential stores, not task workspaces or audit artifacts. |
| DESIGN-REQ-018 | constraint | 10. Related Documents | Implementation must align with OAuth terminal, Provider Profiles, Settings tab, and Managed/External Agent execution model contracts. |

## Ordered Story Candidates

### STORY-001: Claude Settings Credential Method Actions

Short name: `claude-settings-actions`

Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`

Sections: 1. Product Intent, 3. Settings UX, 3.1 Placement, 3.2 Row Actions, 8. API-Key Auth Is Separate

Why: As an operator, I can manage Claude Anthropic credentials from the existing Provider Profiles table and choose either OAuth enrollment or API-key enrollment without confusing the two credential methods.

Scope:
- Keep Claude Anthropic credential setup inside the existing Provider Profiles table.
- Expose distinct OAuth and API-key enrollment actions for Claude Anthropic.
- Route API-key enrollment to Managed Secrets with ANTHROPIC_API_KEY materialization, not through the browser terminal.
- Use Claude-specific labels for Claude actions while preserving Codex OAuth behavior for codex_default.

Out of scope:
- A separate Claude auth page.
- Running OAuth terminal sessions for API-key enrollment.
- Changing Codex OAuth behavior.

Independent test: Render or query the Settings Provider Profiles surface with a Claude Anthropic profile and assert the row exposes Claude-specific OAuth, validation, disconnect, and API-key actions while API-key enrollment routes through Managed Secrets instead of OAuth session APIs.

Acceptance criteria:
- Given the operator opens Settings -> Providers & Secrets -> Provider Profiles, then the claude_anthropic row is available from that table rather than a separate Claude auth page.
- Given the Claude Anthropic row is rendered, then it exposes Connect with Claude OAuth and Use Anthropic API key as distinct first-class actions.
- Given an OAuth volume is present and provider policy permits checks, then Validate OAuth is available for the row.
- Given disconnect is supported by the provider-profile lifecycle policy, then Disconnect OAuth is available for the row.
- Given the operator chooses Use Anthropic API key, then the flow stores an Anthropic API key in Managed Secrets and does not create an OAuth terminal session.
- Given Claude-specific behavior is shown, then Codex-specific labels are not reused for the Claude Anthropic row.

Dependencies: None.

Assumptions:
- The existing Settings Provider Profiles table can support provider-specific action metadata.

Owned coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-005.

Handoff: As an operator, I can manage Claude Anthropic credentials from the existing Provider Profiles table and choose either OAuth enrollment or API-key enrollment without confusing the two credential methods.

### STORY-002: Claude OAuth Provider Registry and Session Backend

Short name: `claude-oauth-backend`

Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`

Sections: 2. OAuth Profile Shape, 3.3 OAuth Terminal Flow, 4. OAuth Session Backend

Why: As an operator, when I choose Connect with Claude OAuth, MoonMind creates a Claude-specific OAuth session that starts a short-lived auth runner with the correct mounted Claude home and bootstrap command.

Scope:
- Reuse the OAuth Session API and MoonMind.OAuthSession workflow family for Claude OAuth.
- Define a Claude provider registry entry for runtime_id claude_code with the documented transport, volume, provider metadata, bootstrap command, and success check.
- Start a short-lived auth runner with Claude-specific home environment variables and clear competing API-key variables.
- Keep OAuth terminal sessions scoped to credential enrollment and repair.

Out of scope:
- Browser ceremony UX polish beyond backend terminal/session readiness.
- Credential verification and profile registration.
- Normal Claude task execution.

Independent test: Create a Claude OAuth session through the OAuth Session API using runtime_id = claude_code and assert the workflow/activity payloads, registry lookup, runner command, volume mount, environment, and clear-env behavior match the Claude contract without starting an ordinary task run.

Acceptance criteria:
- Given Connect with Claude OAuth is invoked for claude_anthropic, then POST /api/v1/oauth-sessions creates a session anchored to that provider profile row.
- Given runtime_id = claude_code, then the provider registry resolves auth_mode oauth, session_transport moonmind_pty_ws, default volume claude_auth_volume, mount path /home/app/.claude, provider_id anthropic, provider_label Anthropic, bootstrap command claude login, and success_check claude_config_exists.
- Given the auth runner starts, then it mounts claude_auth_volume as the Claude home used for enrollment.
- Given the auth runner environment is built, then HOME=/home/app, CLAUDE_HOME=/home/app/.claude, and CLAUDE_VOLUME_PATH=/home/app/.claude are set.
- Given ambient API-key variables are present, then ANTHROPIC_API_KEY and CLAUDE_API_KEY are cleared for the OAuth enrollment runner.
- Given the session is for OAuth enrollment or repair, then it is not treated as the normal task execution surface for Claude runs.

Dependencies: STORY-001.

Assumptions:
- The existing OAuth session workflow can accept a provider registry entry for claude_code without a new workflow family.

Owned coverage: DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018.

Handoff: As an operator, when I choose Connect with Claude OAuth, MoonMind creates a Claude-specific OAuth session that starts a short-lived auth runner with the correct mounted Claude home and bootstrap command.

### STORY-003: Claude Browser Terminal Sign-In Ceremony

Short name: `claude-terminal-ceremony`

Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`

Sections: 3.3 OAuth Terminal Flow, 3.4 Claude Sign-In Ceremony, 4. OAuth Session Backend, 9. Security Requirements

Why: As an operator, I can complete Claude OAuth in a MoonMind browser terminal by opening Claude's authentication URL externally and pasting the returned token or code back into the terminal while the session waits for me.

Scope:
- Support Claude's URL plus pasted token or authorization-code ceremony instead of Codex device-code semantics.
- Keep the terminal attached while the operator completes external Anthropic sign-in.
- Represent operator wait time with an explicit awaiting_user-style state.
- Treat pasted tokens or codes as transient terminal input only.
- Enforce terminal attach token lifetime, single use, and session ownership.

Out of scope:
- Provider registry setup.
- Volume verification implementation.
- Using the terminal for ordinary task execution.

Independent test: Run a fake Claude login process through the PTY/WebSocket bridge that prints a URL, waits for pasted input, receives a synthetic code, and assert session status, terminal attachment lifetime, and persisted records treat the code as transient input only.

Acceptance criteria:
- Given a Claude OAuth session is created, then Mission Control opens the in-browser terminal view attached to the short-lived auth runner.
- Given Claude prints an authentication URL, then the terminal remains attached and the session moves to or remains in an operator-waiting state such as awaiting_user.
- Given the operator pastes a returned token or authorization code, then the PTY bridge forwards it only to the Claude CLI process.
- Given terminal input contains a token or code, then MoonMind does not store it as a Managed Secret, return it through an API response, write it to an artifact, or persist it in a provider profile row.
- Given attach tokens are issued for the browser terminal, then they are short-lived and single-use.
- Given the OAuth terminal is active, then it cannot be used as a generic Claude task execution terminal.

Dependencies: STORY-002.

Assumptions:
- Tests can use a fake Claude CLI process to avoid external Anthropic credentials.

Owned coverage: DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-017.

Handoff: As an operator, I can complete Claude OAuth in a MoonMind browser terminal by opening Claude's authentication URL externally and pasting the returned token or code back into the terminal while the session waits for me.

### STORY-004: Claude OAuth Verification and Profile Registration

Short name: `claude-oauth-verify-profile`

Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`

Sections: 5. Verification, 6. Profile Registration, 9. Security Requirements

Why: As an operator, I can finalize a Claude OAuth session and have MoonMind verify the auth volume, register or update the OAuth-backed provider profile, and expose only secret-free verification metadata.

Scope:
- Verify Claude account-auth material before registering or updating the provider profile.
- Accept only explicitly documented Claude credential artifacts as proof of account setup.
- Return and persist secret-free verification metadata only.
- Register or update the OAuth-backed claude_anthropic provider profile after successful verification.
- Sync Provider Profile Manager for claude_code after successful registration.

Out of scope:
- Settings row rendering.
- Runtime launch materialization.
- External Anthropic live-provider verification.

Independent test: Finalize a Claude OAuth session against a temporary Claude home containing representative credential artifacts and assert the verifier returns secret-free metadata, then assert the claude_anthropic profile is registered or updated with oauth_volume fields and Provider Profile Manager sync occurs.

Acceptance criteria:
- Given finalization runs after Claude login, then MoonMind verifies account-auth material under the mounted Claude home before profile registration.
- Given known artifacts such as credentials.json or qualifying settings.json are present, then verification can return verified status using only metadata.
- Given verification output is returned or persisted, then it includes only secret-free fields such as verified, status, reason, artifact counts, and timestamps.
- Given verification succeeds, then claude_anthropic is registered or updated with credential_source oauth_volume, runtime_materialization_mode oauth_home, volume_ref claude_auth_volume, and volume_mount_path /home/app/.claude.
- Given profile registration succeeds, then Provider Profile Manager is synced for runtime_id claude_code.
- Given an unauthorized operator attempts finalize or repair, then the operation is rejected.

Dependencies: STORY-002.

Assumptions:
- The runtime adapter will document the accepted stable Claude credential artifacts before implementation completes.

Needs clarification:
- [NEEDS CLARIFICATION] Which Claude CLI account-auth artifact set is stable enough for claude_config_exists verification?

Owned coverage: DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-018.

Handoff: As an operator, I can finalize a Claude OAuth session and have MoonMind verify the auth volume, register or update the OAuth-backed provider profile, and expose only secret-free verification metadata.

### STORY-005: Claude OAuth Runtime Launch Materialization

Short name: `claude-oauth-launch`

Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`

Sections: 2. OAuth Profile Shape, 7. Runtime Launch Behavior, 9. Security Requirements

Why: As a task operator, when a Claude run uses the OAuth-backed profile, MoonMind launches claude_code with the Claude auth volume materialized as the runtime home and competing API-key variables cleared.

Scope:
- Resolve claude_anthropic at Claude task launch.
- Apply clear_env_keys exactly as defined by the selected profile.
- Materialize claude_auth_volume at the configured Claude home path for oauth_home profiles.
- Set Claude home environment variables consistently before launching claude_code.
- Keep raw credential file contents out of workflow history, logs, and artifacts.

Out of scope:
- OAuth enrollment UI.
- Verification artifact discovery.
- Treating auth volumes as workspaces or audit records.

Independent test: Build a Claude task launch from a selected claude_anthropic profile and inspect the workflow/activity or adapter-bound payload to confirm clear_env_keys, auth volume mount/projection, Claude home environment variables, launch command, and redaction behavior.

Acceptance criteria:
- Given a Claude task selects claude_anthropic, then launch resolves that provider profile before container or runtime startup.
- Given the selected profile contains clear_env_keys, then ANTHROPIC_API_KEY, CLAUDE_API_KEY, and OPENAI_API_KEY are removed from the launch environment before claude_code starts.
- Given oauth_home materialization is selected, then claude_auth_volume is mounted or projected at /home/app/.claude according to the provider-profile materialization contract.
- Given the runtime environment is built, then Claude home environment variables are set consistently for the runtime.
- Given workflow history, logs, or artifacts are inspected after launch, then raw credential file contents are absent.
- Given a workload or audit artifact path is requested, then the auth volume is not treated as a task workspace or audit artifact.

Dependencies: STORY-004.

Assumptions:
- The provider-profile materialization contract already defines mount versus projection behavior for oauth_home profiles.

Owned coverage: DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-015, DESIGN-REQ-017, DESIGN-REQ-018.

Handoff: As a task operator, when a Claude run uses the OAuth-backed profile, MoonMind launches claude_code with the Claude auth volume materialized as the runtime home and competing API-key variables cleared.

### STORY-006: Claude OAuth Authorization and Redaction Guardrails

Short name: `claude-oauth-guardrails`

Source reference: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`

Sections: 2. OAuth Profile Shape, 3.4 Claude Sign-In Ceremony, 5. Verification, 7. Runtime Launch Behavior, 9. Security Requirements

Why: As an operator and platform maintainer, I can rely on Claude OAuth lifecycle operations, terminal output, errors, logs, artifacts, and profile rows to enforce authorization and redact secret-like values across the full flow.

Scope:
- Enforce authorization across Claude OAuth start, attach, cancel, finalize, and repair operations.
- Make browser terminal attach tokens short-lived and single-use.
- Redact secret-like values from terminal output, failure reasons, logs, and artifacts.
- Keep provider profile rows limited to refs and metadata.
- Treat OAuth auth volumes strictly as credential stores.

Out of scope:
- New credential storage backends.
- Provider-specific sign-in UX beyond enforcing security boundaries.

Independent test: Exercise unauthorized lifecycle calls and inject secret-like values into terminal output, failure reasons, logs, and artifact-producing paths, then assert access is denied where required and every externally visible surface is redacted.

Acceptance criteria:
- Given an unauthenticated or unauthorized operator attempts to start, attach to, cancel, finalize, or repair a Claude OAuth session, then MoonMind denies the operation.
- Given a browser terminal attach token is reused or expired, then attach fails.
- Given terminal output, failure reasons, logs, or artifacts contain secret-like values, then externally visible output is redacted.
- Given provider profile rows are read, then they contain refs and metadata only, never credential file contents.
- Given OAuth auth volume metadata is surfaced, then the volume is described as a credential store and not exposed as a task workspace or audit artifact.
- Given guardrail tests run, then they cover the real API/workflow/activity or adapter boundary rather than only isolated helpers.

Dependencies: STORY-002, STORY-003, STORY-004, STORY-005.

Assumptions:
- Existing redaction utilities can be reused for Claude OAuth surfaces.

Owned coverage: DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018.

Handoff: As an operator and platform maintainer, I can rely on Claude OAuth lifecycle operations, terminal output, errors, logs, artifacts, and profile rows to enforce authorization and redact secret-like values across the full flow.

## Coverage Matrix

| Coverage ID | Owning Stories |
| --- | --- |
| DESIGN-REQ-001 | STORY-001 |
| DESIGN-REQ-002 | STORY-001 |
| DESIGN-REQ-003 | STORY-002, STORY-004, STORY-005 |
| DESIGN-REQ-004 | STORY-004, STORY-005, STORY-006 |
| DESIGN-REQ-005 | STORY-001 |
| DESIGN-REQ-006 | STORY-002, STORY-003 |
| DESIGN-REQ-007 | STORY-003 |
| DESIGN-REQ-008 | STORY-003 |
| DESIGN-REQ-009 | STORY-003, STORY-006 |
| DESIGN-REQ-010 | STORY-002, STORY-003 |
| DESIGN-REQ-011 | STORY-002 |
| DESIGN-REQ-012 | STORY-002 |
| DESIGN-REQ-013 | STORY-004, STORY-006 |
| DESIGN-REQ-014 | STORY-004 |
| DESIGN-REQ-015 | STORY-005 |
| DESIGN-REQ-016 | STORY-003, STORY-004, STORY-006 |
| DESIGN-REQ-017 | STORY-002, STORY-003, STORY-005, STORY-006 |
| DESIGN-REQ-018 | STORY-002, STORY-004, STORY-005, STORY-006 |

## Dependencies

- STORY-001 depends on: None.
- STORY-002 depends on: STORY-001.
- STORY-003 depends on: STORY-002.
- STORY-004 depends on: STORY-002.
- STORY-005 depends on: STORY-004.
- STORY-006 depends on: STORY-002, STORY-003, STORY-004, STORY-005.

## Out Of Scope Items And Rationale

- Anthropic API-key enrollment implementation details beyond Settings routing and Managed Secrets binding are out of scope because the source document defines OAuth and mentions API keys only to preserve method choice.
- Creating `spec.md` files or directories under `specs/` is out of scope for breakdown and belongs to a later `/speckit.specify` run.
- Live external Anthropic credential checks are out of scope for story extraction; downstream implementation can use hermetic fake CLI/provider tests and reserve real credentials for provider verification.
- Ordinary Claude task terminal attachment is out of scope because the OAuth terminal is only for credential enrollment and repair.

## Coverage Gate

```text
PASS - every major design point is owned by at least one story.
```
