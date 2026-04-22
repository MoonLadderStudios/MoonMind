# Feature Specification: Claude Settings Credential Actions

**Feature Branch**: `239-claude-settings-credential-actions`
**Created**: 2026-04-22
**Status**: Draft
**Input**:

```text
Jira issue: MM-477 from MM project
Summary: Claude Settings Credential Method Actions
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-477 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-477: Claude Settings Credential Method Actions

Source Reference
- Source document: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source title: Claude Anthropic OAuth in Settings
- Source sections:
  - 1. Product Intent
  - 3. Settings UX
  - 3.1 Placement
  - 3.2 Row Actions
  - 8. API-Key Auth Is Separate
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-005

User Story
As an operator, I can manage Claude Anthropic credentials from the existing Provider Profiles table and choose either OAuth enrollment or API-key enrollment without confusing the two credential methods.

Acceptance Criteria
- Given the operator opens Settings -> Providers & Secrets -> Provider Profiles, then the `claude_anthropic` row is available from that table rather than a separate Claude auth page.
- Given the Claude Anthropic row is rendered, then it exposes Connect with Claude OAuth and Use Anthropic API key as distinct first-class actions.
- Given an OAuth volume is present and provider policy permits checks, then Validate OAuth is available for the row.
- Given disconnect is supported by the provider-profile lifecycle policy, then Disconnect OAuth is available for the row.
- Given the operator chooses Use Anthropic API key, then the flow stores an Anthropic API key in Managed Secrets and does not create an OAuth terminal session.
- Given Claude-specific behavior is shown, then Codex-specific labels are not reused for the Claude Anthropic row.

Requirements
- Keep Claude Anthropic credential setup inside the existing Provider Profiles table.
- Expose distinct OAuth and API-key enrollment actions for Claude Anthropic.
- Route API-key enrollment to Managed Secrets with `ANTHROPIC_API_KEY` materialization, not through the browser terminal.
- Use Claude-specific labels for Claude actions while preserving Codex OAuth behavior for `codex_default`.

Relevant Implementation Notes
- Preserve MM-477 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for Claude Anthropic provider-profile placement, row actions, OAuth enrollment, API-key enrollment, and API-key/OAuth separation.
- Implement credential actions in the existing Settings -> Providers & Secrets -> Provider Profiles table rather than introducing or relying on a separate Claude auth page.
- Present Claude OAuth and Anthropic API-key enrollment as distinct row actions for `claude_anthropic`.
- Ensure the API-key path stores an Anthropic API key in Managed Secrets and materializes it as `ANTHROPIC_API_KEY`.
- Ensure choosing the API-key path does not create an OAuth terminal session.
- Keep Codex OAuth behavior for `codex_default` intact while using Claude-specific labels for Claude Anthropic actions.

Validation
- Verify the `claude_anthropic` provider profile appears in Settings -> Providers & Secrets -> Provider Profiles.
- Verify the Claude Anthropic row exposes distinct Connect with Claude OAuth and Use Anthropic API key actions.
- Verify Validate OAuth appears when an OAuth volume is present and provider policy permits validation checks.
- Verify Disconnect OAuth appears when disconnect is supported by provider-profile lifecycle policy.
- Verify Use Anthropic API key stores the key in Managed Secrets for `ANTHROPIC_API_KEY` materialization.
- Verify Use Anthropic API key does not create an OAuth terminal session.
- Verify Claude Anthropic UI copy does not reuse Codex-specific labels.
- Verify existing `codex_default` OAuth behavior is preserved.

Non-Goals
- Creating a separate Claude auth page outside the existing Provider Profiles table.
- Routing Anthropic API-key enrollment through the OAuth browser terminal flow.
- Replacing or regressing existing Codex OAuth behavior for `codex_default`.

Needs Clarification
- None
```

## Classification

Single-story runtime feature request. The brief contains one independently testable Settings behavior change: `claude_anthropic` provider profile rows must present distinct Claude OAuth and Anthropic API-key credential method actions inside the existing Provider Profiles table while preserving existing Codex OAuth behavior.

## User Story - Claude Credential Method Actions

**Summary**: As an operator managing Claude Anthropic provider profiles, I want OAuth enrollment and API-key enrollment to appear as distinct first-class row actions so that I can choose the correct credential method without mistaking one path for the other.

**Goal**: Operators can configure Claude Anthropic credentials from Settings > Providers & Secrets > Provider Profiles, choose between Claude OAuth and Anthropic API-key enrollment from the row, validate or disconnect OAuth when supported, and continue using Codex OAuth behavior unchanged.

**Independent Test**: Render the Settings Provider Profiles table with a `claude_anthropic` row that supports OAuth volume enrollment, API-key enrollment, OAuth validation, and OAuth disconnect; activate each credential method action and verify the OAuth actions use the OAuth session lifecycle while the API-key action opens the API-key enrollment flow without creating an OAuth terminal session. Render `codex_default` alongside it and verify existing Codex OAuth labels and requests remain unchanged.

**Acceptance Scenarios**:

1. **Given** the operator opens Settings > Providers & Secrets > Provider Profiles, **When** a `claude_anthropic` profile is present, **Then** Claude Anthropic credential setup is available from that row and no standalone Claude auth page is required.
2. **Given** a `claude_anthropic` row supports both credential methods, **When** the row renders, **Then** it exposes `Connect with Claude OAuth` and `Use Anthropic API key` as distinct first-class actions.
3. **Given** the operator activates `Connect with Claude OAuth`, **When** the action starts, **Then** MoonMind uses the OAuth session lifecycle for `runtime_id = "claude_code"` and does not route the operator into the API-key enrollment flow.
4. **Given** the operator activates `Use Anthropic API key`, **When** the action starts, **Then** MoonMind opens an API-key enrollment flow that stores a Managed Secret for `ANTHROPIC_API_KEY` materialization and does not create an OAuth terminal session.
5. **Given** an OAuth volume is present and trusted policy metadata permits validation, **When** the row renders, **Then** it exposes `Validate OAuth`.
6. **Given** trusted provider-profile lifecycle metadata supports OAuth disconnect, **When** the row renders, **Then** it exposes `Disconnect OAuth`.
7. **Given** a `codex_default` OAuth-capable profile is present, **When** the table renders and Codex OAuth is started, **Then** existing Codex OAuth labels, session creation, and terminal behavior remain available.

### Edge Cases

- A `claude_anthropic` row supports API-key enrollment but not OAuth enrollment.
- A `claude_anthropic` row supports OAuth enrollment but does not have a volume available for validation.
- A connected OAuth row supports validation but not disconnect.
- A Claude row lacks trusted credential-method metadata.
- A non-Claude profile uses `runtime_id = "claude_code"` or `provider_id = "anthropic"` but is not the `claude_anthropic` profile.
- Long action labels render in narrow Settings table and card layouts without hiding the credential method distinction.

## Assumptions

- The source document treats API-key enrollment as a separate method from OAuth, so this story owns only the row-level method selection and entrypoints; deeper API-key persistence may reuse the existing manual-auth/Managed Secrets backend path when present.
- Existing trusted provider profile metadata under `command_behavior` remains the row-action capability source for Claude-specific Settings behavior.
- OAuth validation and disconnect actions may be rendered as row-level affordances first; if a dedicated backend endpoint is unavailable in the current slice, the action must fail safely without exposing secrets and remain covered by tests or verification notes.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` sections 1 and 3.1): Operators should configure Claude Code credentials from Settings > Providers & Secrets > Provider Profiles, and MoonMind should not create a separate Claude auth page. Scope: in scope, mapped to FR-001, FR-002, and FR-009.
- **DESIGN-REQ-002** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` sections 1 and 3.2): Claude Anthropic rows should expose credential-method-specific actions including `Connect with Claude OAuth`, `Use Anthropic API key`, `Validate OAuth`, and `Disconnect OAuth` when supported, and should avoid Codex-specific labels where behavior is Claude-specific. Scope: in scope, mapped to FR-003 through FR-008 and FR-010.
- **DESIGN-REQ-005** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 8): API-key enrollment is separate from OAuth, stores the Anthropic key in Managed Secrets, materializes it as `ANTHROPIC_API_KEY`, and must not run through the browser terminal OAuth session. Scope: in scope, mapped to FR-004, FR-005, FR-011, and FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Settings MUST keep `claude_anthropic` credential setup inside Providers & Secrets > Provider Profiles.
- **FR-002**: The system MUST NOT require or introduce a standalone Claude auth page for this credential-method choice.
- **FR-003**: A supported `claude_anthropic` row MUST expose `Connect with Claude OAuth` when trusted row metadata indicates OAuth enrollment is supported.
- **FR-004**: A supported `claude_anthropic` row MUST expose `Use Anthropic API key` when trusted row metadata indicates API-key enrollment is supported.
- **FR-005**: Activating `Connect with Claude OAuth` MUST use the OAuth session lifecycle for the Claude profile and MUST NOT open the API-key enrollment flow.
- **FR-006**: Activating `Use Anthropic API key` MUST open the API-key enrollment flow and MUST NOT create an OAuth terminal session.
- **FR-007**: A `claude_anthropic` row MUST expose `Validate OAuth` only when trusted row metadata indicates an OAuth volume exists and validation is permitted.
- **FR-008**: A `claude_anthropic` row MUST expose `Disconnect OAuth` only when trusted provider-profile lifecycle metadata indicates disconnect is supported.
- **FR-009**: Profiles without trusted Claude credential-method metadata MUST NOT show misleading Claude credential method actions.
- **FR-010**: Claude-specific actions MUST use Claude/Anthropic labels and MUST NOT reuse Codex-specific labels for Claude enrollment.
- **FR-011**: The API-key enrollment path MUST store the Anthropic key through Managed Secrets for `ANTHROPIC_API_KEY` runtime materialization.
- **FR-012**: The API-key enrollment path MUST NOT route through a browser terminal OAuth session.
- **FR-013**: `codex_default` and other Codex OAuth-capable profiles MUST retain their existing OAuth action behavior.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-477 and the source design mappings.

### Key Entities

- **Provider Profile Row**: The Settings table row representing a runtime/provider credential configuration and its readiness state.
- **Claude Credential Method Action Set**: Trusted row actions for OAuth enrollment, API-key enrollment, OAuth validation, and OAuth disconnect.
- **OAuth Enrollment Action**: A Claude-specific row action that starts the OAuth session lifecycle for the selected profile.
- **API-Key Enrollment Action**: A Claude-specific row action that opens the Managed Secrets-backed API-key enrollment flow without creating an OAuth session.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests confirm supported `claude_anthropic` rows show both `Connect with Claude OAuth` and `Use Anthropic API key`.
- **SC-002**: UI tests confirm `Connect with Claude OAuth` creates an OAuth session for `runtime_id = "claude_code"` and does not show the API-key enrollment flow.
- **SC-003**: UI tests confirm `Use Anthropic API key` opens the API-key enrollment flow and makes no `/api/v1/oauth-sessions` request.
- **SC-004**: UI tests confirm `Validate OAuth` and `Disconnect OAuth` render only when trusted metadata supports them.
- **SC-005**: UI tests confirm unsupported or metadata-free Claude rows hide credential-method actions.
- **SC-006**: Regression tests confirm `codex_default` keeps existing Codex OAuth behavior.
- **SC-007**: UI layout verification confirms long Claude action labels remain visible and distinguishable in provider profile row/card layouts.
- **SC-008**: Traceability verification confirms MM-477 and DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-005 remain present in MoonSpec artifacts and final verification evidence.
