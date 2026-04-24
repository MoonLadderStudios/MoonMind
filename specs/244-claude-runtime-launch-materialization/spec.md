# Feature Specification: Claude OAuth Runtime Launch Materialization

**Feature Branch**: `244-claude-runtime-launch-materialization`
**Created**: 2026-04-23
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-481 as the canonical Moon Spec orchestration input.

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
# MM-481 MoonSpec Orchestration Input

## Source

- Jira issue: MM-481
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude OAuth Runtime Launch Materialization
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-481 from MM project
Summary: Claude OAuth Runtime Launch Materialization
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-481 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-481: Claude OAuth Runtime Launch Materialization

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: Claude Anthropic OAuth in Settings
- Source Sections:
  - 2. OAuth Profile Shape
  - 7. Runtime Launch Behavior
  - 9. Security Requirements
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-015
  - DESIGN-REQ-017
  - DESIGN-REQ-018

User Story
As a task operator, when a Claude run uses the OAuth-backed profile, MoonMind launches `claude_code` with the Claude auth volume materialized as the runtime home and competing API-key variables cleared.

Acceptance Criteria
- Given a Claude task selects `claude_anthropic`, then launch resolves that provider profile before container or runtime startup.
- Given the selected profile contains `clear_env_keys`, then `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY` are removed from the launch environment before `claude_code` starts.
- Given `oauth_home` materialization is selected, then `claude_auth_volume` is mounted or projected at `/home/app/.claude` according to the provider-profile materialization contract.
- Given the runtime environment is built, then Claude home environment variables are set consistently for the runtime.
- Given workflow history, logs, or artifacts are inspected after launch, then raw credential file contents are absent.
- Given a workload or audit artifact path is requested, then the auth volume is not treated as a task workspace or audit artifact.

Requirements
- Resolve `claude_anthropic` at Claude task launch.
- Apply `clear_env_keys` exactly as defined by the selected profile.
- Materialize `claude_auth_volume` at the configured Claude home path for `oauth_home` profiles.
- Set Claude home environment variables consistently before launching `claude_code`.
- Keep raw credential file contents out of workflow history, logs, and artifacts.

Relevant Implementation Notes
- Preserve MM-481 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for OAuth profile shape, runtime launch behavior, and security requirements.
- Launch behavior must resolve the `claude_anthropic` provider profile before runtime startup rather than inferring settings ad hoc during command execution.
- Apply the selected profile's `clear_env_keys` exactly and remove competing API-key variables before `claude_code` starts.
- For `oauth_home` profiles, materialize `claude_auth_volume` at `/home/app/.claude` and set Claude home environment variables consistently for the runtime.
- Keep raw credential file contents out of workflow history, logs, artifacts, and audit paths.
- Do not treat the auth volume as a task workspace or as an artifact-backed path.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-481 blocks MM-480, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-481 is blocked by MM-482, whose embedded status is Selected for Development.

Needs Clarification
- None
```

## User Story - Claude OAuth Runtime Launch Materialization

**Summary**: As a task operator, when a Claude run uses the OAuth-backed profile, MoonMind launches `claude_code` with the Claude auth volume materialized as the runtime home and competing API-key variables cleared.

**Goal**: Claude task launch consistently resolves the OAuth-backed Claude profile, materializes the Claude auth volume at the runtime home, applies the required home environment, and prevents competing API-key authentication from overriding OAuth-backed execution.

**Independent Test**: Start or simulate a Claude task that selects the `claude_anthropic` profile, then verify the launch uses the OAuth-backed profile, materializes the Claude auth volume at the configured Claude home path, applies the expected Claude home environment, clears competing API-key variables before runtime start, and keeps raw credential file contents out of logs, workflow history, and artifacts.

**Acceptance Scenarios**:

1. **Given** a Claude task selects the OAuth-backed `claude_anthropic` profile, **When** launch preparation begins, **Then** the system resolves that provider profile before runtime startup.
2. **Given** the resolved profile defines conflict-clearing environment keys, **When** the runtime launch environment is built, **Then** competing Anthropic, Claude, and OpenAI API-key variables are removed before `claude_code` starts.
3. **Given** the resolved profile uses OAuth-home materialization, **When** the runtime workspace is prepared, **Then** the Claude auth volume is mounted or projected at the configured Claude home path.
4. **Given** the runtime starts from the OAuth-backed profile, **When** the launch environment is finalized, **Then** the Claude home environment variables are set consistently for the runtime.
5. **Given** logs, workflow history, diagnostics, or artifacts are inspected after launch, **When** the OAuth-backed runtime has been prepared, **Then** raw credential file contents are not exposed.
6. **Given** a workload or audit artifact path is requested, **When** the launch materialization paths are evaluated, **Then** the Claude auth volume is not treated as a task workspace or artifact-backed path.

### Edge Cases

- The selected provider profile is missing one or more conflict-clearing environment keys required to prevent competing authentication modes.
- The runtime resolves a non-OAuth Claude profile while the task intended to use `claude_anthropic`.
- The Claude auth volume path exists but is not mounted or projected at the configured Claude home location.
- Runtime diagnostics or artifact collection attempt to include files from the Claude auth volume.
- Existing non-Claude or non-OAuth runtime launch paths must remain unchanged.

## Assumptions

- MM-478 covers Claude OAuth session creation and provider registry defaults, MM-479 covers the browser terminal sign-in ceremony, and MM-480 covers post-login verification and provider profile registration; MM-481 starts from the point where a verified OAuth-backed Claude profile is selected for a task run.
- `claude_anthropic` is the canonical OAuth-backed Claude provider profile used for this launch path.
- The source design document `docs/ManagedAgents/ClaudeAnthropicOAuth.md` is the authoritative runtime requirements source for OAuth-backed Claude profile shape, runtime launch behavior, and security boundaries.

## Source Design Requirements

- **DESIGN-REQ-003** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 2): The OAuth-backed `claude_anthropic` provider profile uses Claude runtime identity, OAuth-volume credential sourcing, OAuth-home materialization, `claude_auth_volume`, the Claude home mount path, and conflict-clearing environment keys for competing API-key auth. Scope: in scope, mapped to FR-001, FR-002, FR-003, and FR-004.
- **DESIGN-REQ-004** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 2): Runtime launch must preserve the documented OAuth-backed provider profile shape rather than degrading to an API-key-driven or ad hoc launch configuration. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-015** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 7): When OAuth-home materialization is selected, the Claude auth volume is mounted or projected at the configured Claude home path for runtime startup. Scope: in scope, mapped to FR-003 and FR-004.
- **DESIGN-REQ-017** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 9): Sensitive Claude auth material must remain outside workflow history, logs, diagnostics, and artifacts visible to operators or downstream systems. Scope: in scope, mapped to FR-005 and FR-006.
- **DESIGN-REQ-018** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 7): Claude task launch resolves the OAuth-backed profile, applies conflict-clearing environment keys, materializes the auth volume, sets Claude home environment variables consistently, and avoids treating the auth volume as a task workspace or artifact path. Scope: in scope, mapped to FR-001 through FR-006.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST resolve the `claude_anthropic` provider profile before launching a Claude task that targets the OAuth-backed Claude runtime path.
- **FR-002**: System MUST preserve the OAuth-backed Claude profile shape during launch preparation, including the documented OAuth credential-source and conflict-clearing behavior, rather than falling back to competing authentication modes.
- **FR-003**: System MUST materialize the Claude auth volume at the configured Claude home path when the selected profile uses OAuth-home materialization.
- **FR-004**: System MUST set Claude home environment variables consistently for the runtime launched from the OAuth-backed profile.
- **FR-005**: System MUST remove competing Anthropic, Claude, and OpenAI API-key variables from the runtime launch environment before `claude_code` starts when the resolved profile requires those keys to be cleared.
- **FR-006**: System MUST keep raw credential file contents from the Claude auth volume out of workflow history, logs, diagnostics, and artifacts generated by the launch path.
- **FR-007**: System MUST prevent the Claude auth volume from being treated as a task workspace or artifact-backed path during runtime launch and audit flows.
- **FR-008**: System MUST preserve MM-481 in implementation notes, verification output, commit text, and pull request metadata for traceability.

### Key Entities

- **OAuth-backed Claude Provider Profile**: The `claude_anthropic` profile that identifies Claude runtime launch settings, OAuth credential sourcing, conflict-clearing environment behavior, and auth-volume materialization requirements.
- **Claude Runtime Launch Context**: The launch-time data used to resolve the selected profile, construct the runtime environment, and materialize the Claude auth volume before process start.
- **Claude Auth Volume Materialization**: The mounted or projected Claude auth storage that becomes the runtime Claude home for OAuth-backed execution.
- **Launch Diagnostics Surface**: Workflow history, logs, diagnostics, and artifacts observable during or after runtime launch.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Launch-path tests prove Claude tasks targeting the OAuth-backed profile resolve `claude_anthropic` before runtime startup.
- **SC-002**: Launch-environment tests prove competing Anthropic, Claude, and OpenAI API-key variables are absent from the runtime environment when the selected profile requires them to be cleared.
- **SC-003**: Launch materialization tests prove the Claude auth volume is mounted or projected at the configured Claude home path and that Claude home environment variables are set consistently.
- **SC-004**: Diagnostics and artifact tests prove raw credential file contents from the Claude auth volume do not appear in workflow history, logs, diagnostics, or artifacts.
- **SC-005**: Regression validation proves non-Claude or non-OAuth launch paths remain unaffected by the Claude OAuth launch materialization behavior.
