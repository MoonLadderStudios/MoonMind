# Feature Specification: Claude OAuth Session Backend

**Feature Branch**: `241-claude-oauth-session-backend`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-478 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `spec.md` (Input).
Classification: single-story runtime feature request.

## Original Preset Brief

```text
# MM-478 MoonSpec Orchestration Input

## Source

- Jira issue: MM-478
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude OAuth Provider Registry and Session Backend
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-478 from MM project
Summary: Claude OAuth Provider Registry and Session Backend
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-478 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-478: Claude OAuth Provider Registry and Session Backend

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: Claude Anthropic OAuth in Settings
- Source Sections:
  - 2. OAuth Profile Shape
  - 3.3 OAuth Terminal Flow
  - 4. OAuth Session Backend
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-006
  - DESIGN-REQ-010
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-017
  - DESIGN-REQ-018

User Story
As an operator, when I choose Connect with Claude OAuth, MoonMind creates a Claude-specific OAuth session that starts a short-lived auth runner with the correct mounted Claude home and bootstrap command.

Acceptance Criteria
- Given Connect with Claude OAuth is invoked for claude_anthropic, then POST /api/v1/oauth-sessions creates a session anchored to that provider profile row.
- Given runtime_id = claude_code, then the provider registry resolves auth_mode oauth, session_transport moonmind_pty_ws, default volume claude_auth_volume, mount path /home/app/.claude, provider_id anthropic, provider_label Anthropic, bootstrap command claude login, and success_check claude_config_exists.
- Given the auth runner starts, then it mounts claude_auth_volume as the Claude home used for enrollment.
- Given the auth runner environment is built, then HOME=/home/app, CLAUDE_HOME=/home/app/.claude, and CLAUDE_VOLUME_PATH=/home/app/.claude are set.
- Given ambient API-key variables are present, then ANTHROPIC_API_KEY and CLAUDE_API_KEY are cleared for the OAuth enrollment runner.
- Given the session is for OAuth enrollment or repair, then it is not treated as the normal task execution surface for Claude runs.

Requirements
- Reuse the OAuth Session API and MoonMind.OAuthSession workflow family for Claude OAuth.
- Define a Claude provider registry entry for runtime_id claude_code with the documented transport, volume, provider metadata, bootstrap command, and success check.
- Start a short-lived auth runner with Claude-specific home environment variables and clear competing API-key variables.
- Keep OAuth terminal sessions scoped to credential enrollment and repair.
```

## User Story - Claude OAuth Session Backend

**Summary**: As an operator, when I choose Connect with Claude OAuth, MoonMind creates a Claude-specific OAuth session that starts a short-lived auth runner with the correct mounted Claude home and bootstrap command.

**Goal**: Claude OAuth enrollment uses the shared OAuth Session API and workflow while applying Claude-specific provider registry defaults, mounted home paths, bootstrap command, and secret-safe runner environment.

**Independent Test**: Create or simulate an OAuth session for `profile_id = claude_anthropic` and `runtime_id = claude_code`, then verify the session is anchored to that profile, resolves Claude OAuth provider defaults, starts a PTY-backed auth runner with `claude login`, mounts `claude_auth_volume` at `/home/app/.claude`, sets Claude home environment variables, and clears competing API-key variables.

**Acceptance Scenarios**:

1. **Given** an operator starts OAuth enrollment for `claude_anthropic`, **When** the OAuth Session API creates the session, **Then** the session is anchored to the `claude_anthropic` provider profile row and uses provider defaults for Claude OAuth.
2. **Given** `runtime_id = claude_code`, **When** provider registry defaults are resolved, **Then** the registry exposes OAuth mode, PTY/WebSocket transport, `claude_auth_volume`, `/home/app/.claude`, Anthropic provider metadata, `claude login`, and `claude_config_exists`.
3. **Given** the Claude OAuth auth runner starts, **When** MoonMind launches the short-lived runner, **Then** it mounts `claude_auth_volume` at `/home/app/.claude` and prepares the runner for `claude login`.
4. **Given** the auth runner environment is built, **When** the runner starts, **Then** `HOME=/home/app`, `CLAUDE_HOME=/home/app/.claude`, and `CLAUDE_VOLUME_PATH=/home/app/.claude` are set.
5. **Given** ambient Anthropic or Claude API-key variables are present in the worker environment, **When** the OAuth enrollment runner starts, **Then** `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY` are cleared from the runner environment.
6. **Given** a Claude OAuth session is active, **When** the operator interacts through the terminal, **Then** the session remains scoped to credential enrollment or repair and is not treated as the normal Claude task execution surface.

### Edge Cases

- A stale or partial `claude_anthropic` seeded profile is present without `CLAUDE_API_KEY` in its conflict-clearing list.
- The OAuth runner image does not contain the `claude` executable.
- A non-Claude OAuth runtime still needs the existing Codex and Gemini behavior unchanged.
- The worker process has ambient Anthropic or Claude API keys set before starting enrollment.

## Assumptions

- The existing OAuth Session API, `MoonMind.OAuthSession` workflow family, and PTY/WebSocket terminal bridge are the canonical backend for volume-backed CLI OAuth runtimes.
- `Connect with Claude OAuth` is initiated from the provider profile row; frontend action routing is covered by earlier stories and remains in scope only as an integration boundary to the backend session payload.

## Source Design Requirements

- **DESIGN-REQ-003** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 2): The `claude_anthropic` OAuth-backed provider profile uses `runtime_id = claude_code`, `provider_id = anthropic`, `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `volume_ref = claude_auth_volume`, `volume_mount_path = /home/app/.claude`, and clears conflicting Anthropic, Claude, and OpenAI API-key environment variables. Scope: in scope, mapped to FR-001, FR-002, FR-006, and FR-007.
- **DESIGN-REQ-006** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3.3): Choosing `Connect with Claude OAuth` must create an OAuth session that runs through the OAuth Session API, `MoonMind.OAuthSession`, a short-lived Claude auth-runner container, the PTY/WebSocket bridge, `claude login`, and `claude_auth_volume` mounted as Claude home. Scope: in scope, mapped to FR-001, FR-003, FR-004, FR-005, and FR-008.
- **DESIGN-REQ-010** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 4): For `runtime_id = claude_code`, the provider registry must define OAuth mode, `moonmind_pty_ws`, `claude_auth_volume`, `/home/app/.claude`, Anthropic provider metadata, `claude login`, and `claude_config_exists`. Scope: in scope, mapped to FR-002 and FR-003.
- **DESIGN-REQ-011** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 4): The Claude auth runner must set `HOME=/home/app`, `CLAUDE_HOME=/home/app/.claude`, and `CLAUDE_VOLUME_PATH=/home/app/.claude`. Scope: in scope, mapped to FR-004.
- **DESIGN-REQ-012** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 4): OAuth enrollment must clear competing `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY` variables so login state comes from the account-auth flow, not an ambient key. Scope: in scope, mapped to FR-005.
- **DESIGN-REQ-017** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 6): After verification, MoonMind registers or updates the OAuth-backed provider profile and syncs Provider Profile Manager for `runtime_id = claude_code`. Scope: in scope, mapped to FR-006 and FR-007.
- **DESIGN-REQ-018** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 7): Claude task launch resolves `claude_anthropic`, applies `clear_env_keys`, mounts or projects `claude_auth_volume`, sets Claude home variables, and avoids raw credential contents in workflow history, logs, or artifacts. Scope: partially in scope for preserving profile shape and runner environment; runtime launch itself is covered by prior launch stories. Mapped to FR-004, FR-005, FR-007, and FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST create Claude OAuth sessions for `claude_anthropic` through the existing OAuth Session API and persist the session against that provider profile row.
- **FR-002**: System MUST register `claude_code` as an OAuth provider with `auth_mode = oauth`, `session_transport = moonmind_pty_ws`, `default_volume_name = claude_auth_volume`, `default_mount_path = /home/app/.claude`, `provider_id = anthropic`, `provider_label = Anthropic`, `bootstrap_command = ["claude", "login"]`, and `success_check = claude_config_exists`.
- **FR-003**: System MUST start Claude OAuth enrollment through the existing short-lived auth-runner and PTY/WebSocket bridge rather than a no-terminal or placeholder command path.
- **FR-004**: System MUST set `HOME=/home/app`, `CLAUDE_HOME=/home/app/.claude`, and `CLAUDE_VOLUME_PATH=/home/app/.claude` in the Claude OAuth auth-runner environment.
- **FR-005**: System MUST ensure `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY` are absent from the Claude OAuth auth-runner environment even when ambient values exist in the worker.
- **FR-006**: System MUST preserve the seeded `claude_anthropic` OAuth profile as an OAuth-volume, OAuth-home profile using `claude_auth_volume` and `/home/app/.claude`.
- **FR-007**: System MUST keep `CLAUDE_API_KEY` in the `claude_anthropic` profile conflict-clearing list so future runtime launch materialization does not prefer API-key auth over OAuth home auth.
- **FR-008**: System MUST keep OAuth terminal sessions scoped to credential enrollment and repair, with normal Claude task execution remaining outside the OAuth session terminal path.
- **FR-009**: System MUST avoid placing Claude OAuth credential material or raw terminal input in workflow payloads, logs, artifacts, browser responses, or provider profile rows.

### Key Entities

- **OAuth Provider Spec**: Per-runtime OAuth defaults used to create, start, and verify sessions.
- **Managed Agent OAuth Session**: Durable session record for an enrollment or repair flow, including runtime, provider profile, volume, transport, terminal metadata, status, and owner.
- **Managed Agent Provider Profile**: Operator-visible provider profile row for `claude_anthropic`, including OAuth volume materialization fields and conflict-clearing environment keys.
- **OAuth Auth Runner**: Short-lived container and terminal bridge context used only for credential enrollment or repair.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A session created for `runtime_id = claude_code` and `profile_id = claude_anthropic` returns and stores `session_transport = moonmind_pty_ws`.
- **SC-002**: Provider registry tests prove `claude_code` resolves the exact Claude OAuth defaults and bootstrap command required by MM-478.
- **SC-003**: Auth-runner startup tests prove Claude runner Docker arguments include the Claude auth volume mount, `HOME`, `CLAUDE_HOME`, and `CLAUDE_VOLUME_PATH`, and exclude `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY`.
- **SC-004**: Startup seeding tests prove `claude_anthropic` remains OAuth-volume/OAuth-home and clears `CLAUDE_API_KEY` as well as existing conflicting keys.
- **SC-005**: Existing Codex OAuth runner tests still pass, proving the Claude-specific changes did not break Codex OAuth enrollment.
