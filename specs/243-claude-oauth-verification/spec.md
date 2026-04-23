# Feature Specification: Claude OAuth Verification and Profile Registration

**Feature Branch**: `243-claude-oauth-verification`
**Created**: 2026-04-23
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-480 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-480-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.

## Original Preset Brief

```text
# MM-480 MoonSpec Orchestration Input

## Source

- Jira issue: MM-480
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude OAuth Verification and Profile Registration
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-480 from MM project
Summary: Claude OAuth Verification and Profile Registration
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-480 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-480: Claude OAuth Verification and Profile Registration

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: Claude Anthropic OAuth in Settings
- Source Sections:
  - 5. Verification
  - 6. Profile Registration
  - 9. Security Requirements
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-016
  - DESIGN-REQ-018

User Story
As an operator, I can finalize a Claude OAuth session and have MoonMind verify the auth volume, register or update the OAuth-backed provider profile, and expose only secret-free verification metadata.

Acceptance Criteria
- Given finalization runs after Claude login, then MoonMind verifies account-auth material under the mounted Claude home before profile registration.
- Given known artifacts such as credentials.json or qualifying settings.json are present, then verification can return verified status using only metadata.
- Given verification output is returned or persisted, then it includes only secret-free fields such as verified, status, reason, artifact counts, and timestamps.
- Given verification succeeds, then claude_anthropic is registered or updated with credential_source oauth_volume, runtime_materialization_mode oauth_home, volume_ref claude_auth_volume, and volume_mount_path /home/app/.claude.
- Given profile registration succeeds, then Provider Profile Manager is synced for runtime_id claude_code.
- Given an unauthorized operator attempts finalize or repair, then the operation is rejected.

Requirements
- Verify Claude account-auth material before registering or updating the provider profile.
- Accept only explicitly documented Claude credential artifacts as proof of account setup.
- Return and persist secret-free verification metadata only.
- Register or update the OAuth-backed claude_anthropic provider profile after successful verification.
- Sync Provider Profile Manager for claude_code after successful registration.

Relevant Implementation Notes
- Preserve MM-480 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for Claude OAuth verification, profile registration, and security requirements.
- Verification must inspect account-auth material under the mounted Claude home before profile registration and must not expose raw credential contents.
- Treat `credentials.json` and qualifying `settings.json` data as proof only through secret-free metadata such as verified state, status, reason, artifact counts, and timestamps.
- On successful verification, register or update `claude_anthropic` with `credential_source` `oauth_volume`, `runtime_materialization_mode` `oauth_home`, `volume_ref` `claude_auth_volume`, and `volume_mount_path` `/home/app/.claude`.
- Sync Provider Profile Manager for `runtime_id` `claude_code` after profile registration succeeds.
- Reject unauthorized finalize or repair attempts.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-480 blocks MM-479, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-480 is blocked by MM-481, whose embedded status is Backlog.

Needs Clarification
- None
```

## User Story - Claude OAuth Verification and Profile Registration

**Summary**: As an operator, I can finalize a Claude OAuth session and have MoonMind verify the auth volume, register or update the OAuth-backed provider profile, and expose only secret-free verification metadata.

**Goal**: Claude OAuth finalization proves account-auth material exists without exposing secrets, then registers or updates the `claude_anthropic` provider profile so later Claude runs can select the OAuth-backed profile safely.

**Independent Test**: Complete or simulate finalization for a Claude OAuth session with known account-auth artifacts under the mounted Claude home, verify that only secret-free metadata is returned or persisted, confirm `claude_anthropic` is registered or updated with OAuth-volume fields, and verify unauthorized finalize or repair attempts are rejected.

**Acceptance Scenarios**:

1. **Given** a Claude OAuth session has completed login and mounted the Claude home, **When** finalization runs, **Then** MoonMind verifies Claude account-auth material under the mounted home before registering or updating any provider profile.
2. **Given** known Claude account-auth artifacts such as `credentials.json` or qualifying `settings.json` are present, **When** verification evaluates the mounted home, **Then** it can return a verified result using only secret-free metadata.
3. **Given** verification output is returned, persisted, logged, or shown in Mission Control, **When** the result is inspected, **Then** it contains only fields such as verified state, status, reason, artifact counts, and timestamps, and never credential contents, tokens, environment dumps, or raw directory listings.
4. **Given** verification succeeds, **When** finalization completes, **Then** MoonMind registers or updates `claude_anthropic` with OAuth-volume credential metadata using `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `volume_ref = claude_auth_volume`, and `volume_mount_path = /home/app/.claude`.
5. **Given** provider profile registration succeeds, **When** finalization completes, **Then** Provider Profile Manager is synced for `runtime_id = claude_code` so new runs can select the updated profile.
6. **Given** an operator lacks provider-profile management permission, **When** they attempt to finalize or repair a Claude OAuth session, **Then** MoonMind rejects the operation before verification, profile registration, or profile mutation occurs.

### Edge Cases

- The mounted Claude home contains no accepted account-auth artifacts.
- `settings.json` exists but does not contain qualifying evidence that Claude account setup completed.
- Verification succeeds but provider profile registration fails.
- Provider Profile Manager sync fails after profile registration succeeds.
- Verification encounters token-like or secret-like content while producing metadata.
- A stale, cancelled, expired, or already-finalized OAuth session is finalized again.
- A repair attempt targets an OAuth auth volume owned by another profile or unauthorized operator.

## Assumptions

- MM-478 covers Claude provider registry defaults and auth-runner volume mounting; MM-479 covers the browser terminal sign-in ceremony. MM-480 starts from the post-login finalization boundary.
- `credentials.json` and qualifying `settings.json` are the currently documented Claude account-auth artifacts. Additional artifacts are out of scope unless explicitly documented by the runtime adapter.
- Runtime launch behavior after profile selection is covered by separate Claude launch materialization work; this story validates finalization-time verification and provider profile registration.

## Source Design Requirements

- **DESIGN-REQ-003** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 5): Finalization must verify the Claude auth volume before registering or updating the provider profile. Scope: in scope, mapped to FR-001 and FR-006.
- **DESIGN-REQ-004** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 5): Verification may accept only known Claude account-auth artifacts such as `credentials.json`, qualifying `settings.json`, or other explicitly documented adapter artifacts. Scope: in scope, mapped to FR-002 and FR-003.
- **DESIGN-REQ-013** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 5): Verification metadata must be secret-free and must not return credential file contents, tokens, environment dumps, or raw directory listings. Scope: in scope, mapped to FR-004 and FR-005.
- **DESIGN-REQ-014** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 6): After successful verification, MoonMind must register or update the OAuth-backed provider profile with `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `volume_ref = claude_auth_volume`, and `volume_mount_path = /home/app/.claude`. Scope: in scope, mapped to FR-006 and FR-007.
- **DESIGN-REQ-016** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 6): Provider Profile Manager must be synced for `runtime_id = claude_code` after registration succeeds. Scope: in scope, mapped to FR-008.
- **DESIGN-REQ-018** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 9): Only authorized operators can finalize or repair Claude OAuth sessions, and provider profile rows store refs and metadata only, never credential contents. Scope: in scope, mapped to FR-005, FR-009, and FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST perform Claude OAuth verification before registering or updating the `claude_anthropic` provider profile.
- **FR-002**: System MUST verify account-auth material under the mounted Claude home associated with the OAuth session.
- **FR-003**: System MUST treat only `credentials.json`, qualifying `settings.json`, or runtime-adapter-documented account-auth artifacts as proof of Claude account setup.
- **FR-004**: System MUST return and persist verification metadata containing only secret-free fields such as verified state, status, reason, artifact counts, and timestamps.
- **FR-005**: System MUST prevent credential file contents, token values, environment dumps, and raw auth-volume directory listings from appearing in API responses, workflow payloads, logs, artifacts, browser-visible data, and provider profile rows.
- **FR-006**: System MUST skip provider profile mutation when Claude OAuth verification fails.
- **FR-007**: System MUST register or update `claude_anthropic` after successful verification with `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `volume_ref = claude_auth_volume`, and `volume_mount_path = /home/app/.claude`.
- **FR-008**: System MUST sync Provider Profile Manager for `runtime_id = claude_code` after successful profile registration or update.
- **FR-009**: System MUST reject unauthorized Claude OAuth finalize or repair attempts before verification, profile registration, or profile mutation occurs.
- **FR-010**: System MUST ensure provider profile rows store only credential refs and metadata for OAuth-backed Claude auth, never credential file contents.
- **FR-011**: System MUST preserve MM-480 in implementation notes, verification output, commit text, and pull request metadata for traceability.

### Key Entities

- **Claude OAuth Session**: Operator-owned enrollment or repair session that reaches finalization after Claude login and references the mounted Claude home.
- **Claude Auth Verification Result**: Secret-free metadata indicating verified state, status, reason, accepted artifact counts, and timestamps.
- **OAuth-backed Provider Profile**: `claude_anthropic` profile row that stores OAuth-volume refs and materialization metadata for Claude runtime selection.
- **Provider Profile Manager Sync**: Runtime profile refresh action that makes the updated `claude_code` profile available to new runs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove Claude OAuth verification accepts documented account-auth artifacts and rejects missing or non-qualifying artifacts without exposing raw file contents.
- **SC-002**: Unit or API tests prove verification responses and persisted artifacts include only secret-free metadata fields.
- **SC-003**: Workflow or activity-boundary tests prove finalization verifies the auth volume before profile registration and skips profile mutation when verification fails.
- **SC-004**: API or service tests prove successful finalization registers or updates `claude_anthropic` with the required OAuth-volume profile fields and syncs `runtime_id = claude_code`.
- **SC-005**: Authorization tests prove unauthorized finalize or repair attempts are rejected before verification or provider profile mutation.
- **SC-006**: Focused validation passes for the Claude OAuth finalization, provider profile registration, and authorization paths touched by this story.
