# Feature Specification: Route Claude Auth Actions

**Feature Branch**: `226-route-claude-auth-actions`
**Created**: 2026-04-22
**Status**: Draft
**Input**:

```text
Jira issue: MM-445 from MM project
Summary: Route claude_anthropic Settings auth actions to a Claude enrollment flow
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-445 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-445: Route claude_anthropic Settings auth actions to a Claude enrollment flow

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: MoonMind Design: claude_anthropic Settings Authentication (Repo-Backed)
- Source Sections:
  - 2.1 Settings surface
  - 2.3 Current Settings UI implementation
  - 5.1 Placement
  - 5.2 Row-level action model
  - 10.1 Frontend
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-003
  - DESIGN-REQ-007

User Story
As an operator configuring provider profiles, I can start Claude Anthropic authentication from the existing Providers & Secrets table and see Claude-specific actions instead of a Codex-shaped Auth control.

Acceptance Criteria
- `claude_anthropic` exposes a Connect Claude action when not connected.
- Connected `claude_anthropic` rows expose Replace token, Validate, and Disconnect actions where supported by returned capability/readiness metadata.
- Codex OAuth behavior remains available for `codex_default` without reusing Codex labels for Claude.
- No new standalone Claude auth page or specs directory is created by this story.

Requirements
- Auth capability is derived from profile metadata or explicit strategy, not hardcoded to `profile.runtime_id === codex_cli`.
- The provider profile row remains the entry point for Claude enrollment.
- Action labels distinguish manual Claude enrollment from terminal OAuth.

Implementation Notes
- Preserve MM-445 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for the Settings provider row placement, action model, and frontend behavior.
- Scope implementation to routing `claude_anthropic` Settings authentication actions from the existing Providers & Secrets table into the Claude enrollment flow.
- Keep Codex OAuth behavior available for `codex_default` and avoid reusing Codex OAuth labels for Claude-specific manual token enrollment.
- Do not create a new standalone Claude auth page or a separate specs directory for this story.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-445 is blocked by MM-446, whose embedded status is Backlog.
```

## Classification

Single-story runtime feature request. The brief contains one independently testable Settings behavior change: `claude_anthropic` provider profile rows must route authentication actions to Claude-specific enrollment controls while preserving existing Codex OAuth behavior.

## User Story - Claude Settings Auth Actions

**Summary**: As an operator configuring provider profiles, I want `claude_anthropic` rows in Providers & Secrets to show Claude-specific authentication actions so I can start or manage Claude enrollment without seeing Codex-shaped OAuth controls.

**Goal**: Operators can manage Claude Anthropic authentication from the existing Provider Profiles table with labels and action availability that match Claude enrollment state, while Codex OAuth rows continue to behave as they do today.

**Independent Test**: Render the Settings Providers & Secrets table with a disconnected `claude_anthropic` profile, a connected `claude_anthropic` profile, and `codex_default`. The story passes when Claude rows expose only Claude-specific actions appropriate to their state, the action entrypoint remains in the provider row, Codex still exposes its OAuth behavior, and no standalone Claude auth page is needed.

**Acceptance Scenarios**:

1. **Given** a `claude_anthropic` provider profile has no connected credential state, **When** the Providers & Secrets table renders, **Then** the row exposes a `Connect Claude` action instead of a generic `Auth` or Codex OAuth action.
2. **Given** a `claude_anthropic` provider profile is connected and capability metadata supports lifecycle actions, **When** the row renders, **Then** it exposes Claude-specific `Replace token`, `Validate`, and `Disconnect` actions according to the supported capabilities.
3. **Given** a `codex_default` provider profile is OAuth capable, **When** the Providers & Secrets table renders, **Then** the existing Codex OAuth action behavior and labels remain available.
4. **Given** Claude authentication is started from Settings, **When** the operator activates the Claude action, **Then** the interaction stays anchored to the existing provider profile row and does not route to a new standalone Claude auth page.
5. **Given** profile metadata is missing or does not indicate Claude auth support, **When** the Providers & Secrets table renders, **Then** the system avoids showing misleading Claude lifecycle actions.

### Edge Cases

- A `claude_anthropic` row has readiness data but no explicit lifecycle capability metadata.
- A connected `claude_anthropic` row supports validation but not disconnect.
- A `claude_anthropic` row is in a failed or degraded readiness state.
- A non-Claude, non-Codex profile has the same runtime but a different provider or credential strategy.
- Long profile names, provider names, and action labels render in narrow Settings layouts.

## Assumptions

- "Connected" means the provider row exposes trusted readiness, credential, or capability metadata indicating Claude enrollment is present.
- The first slice may open an existing or newly added in-row modal or drawer entrypoint, but it must not create a separate Claude auth page.
- The story is limited to Settings row action routing and visible action labels; backend token persistence may be represented by existing readiness or capability metadata if already available.
- MM-446 is recorded as a Jira dependency, but this specification preserves the selected MM-445 story for downstream orchestration.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 2.1): Claude Anthropic authentication should live in Settings > Providers & Secrets > Provider Profiles, using the existing provider profile surface for credential health, readiness, validation feedback, and lifecycle entrypoints. Scope: in scope, mapped to FR-001, FR-002, and FR-006.
- **DESIGN-REQ-003** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 2.3): The current Settings UI gap is that auth capability is treated as Codex-only; Claude must become auth-capable without relying on the Codex-only runtime check. Scope: in scope, mapped to FR-003, FR-004, and FR-008.
- **DESIGN-REQ-007** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` sections 5.1, 5.2, and 10.1): Claude rows should use row-level Claude-specific actions such as `Connect Claude`, `Replace token`, `Validate`, and `Disconnect`, should not reuse Codex labels, and should not create a separate Claude auth page. Scope: in scope, mapped to FR-002, FR-005, FR-006, and FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Settings MUST keep `claude_anthropic` authentication entrypoints inside Providers & Secrets > Provider Profiles.
- **FR-002**: A disconnected `claude_anthropic` provider row MUST expose a `Connect Claude` action when trusted profile metadata indicates Claude enrollment is supported.
- **FR-003**: Claude auth action availability MUST be derived from provider profile metadata, credential strategy, readiness, or explicit capability data rather than a Codex-only runtime check.
- **FR-004**: `codex_default` and other Codex OAuth-capable profiles MUST retain their existing OAuth action behavior.
- **FR-005**: Connected `claude_anthropic` rows MUST expose `Replace token`, `Validate`, and `Disconnect` actions only when trusted capability or readiness metadata supports those actions.
- **FR-006**: Claude row actions MUST use Claude-specific labels and MUST NOT show Codex OAuth labels for Claude enrollment.
- **FR-007**: Activating a Claude auth action MUST keep the operator in the existing Settings provider-profile flow and MUST NOT require or create a standalone Claude auth page.
- **FR-008**: Profiles without trusted Claude auth capability metadata MUST NOT show misleading Claude enrollment actions.
- **FR-009**: The provider row MUST continue to surface readiness, validation, failure, or degraded state in a way operators can distinguish from connected and disconnected states.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-445 and the source design mappings.

### Key Entities

- **Provider Profile Row**: The Settings table row representing a runtime/provider credential configuration and its readiness state.
- **Auth Capability Metadata**: Trusted profile, credential strategy, readiness, or lifecycle metadata that determines which auth actions are available.
- **Claude Auth Action Set**: The Claude-specific row actions for starting, replacing, validating, or disconnecting Claude enrollment.
- **Codex OAuth Action Set**: The existing Codex OAuth row behavior that must remain unchanged.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests confirm disconnected `claude_anthropic` rows show `Connect Claude` and do not show generic `Auth` or Codex-specific OAuth labels.
- **SC-002**: UI tests confirm connected `claude_anthropic` rows show supported Claude lifecycle actions and omit unsupported actions.
- **SC-003**: UI tests confirm `codex_default` keeps existing Codex OAuth behavior.
- **SC-004**: Tests or verification confirm auth capability decisions are not hardcoded solely to `profile.runtime_id === codex_cli`.
- **SC-005**: UI tests confirm Claude auth actions remain within the Providers & Secrets table flow and no standalone Claude auth page is introduced.
- **SC-006**: Traceability verification confirms MM-445 and DESIGN-REQ-001, DESIGN-REQ-003, and DESIGN-REQ-007 are preserved in MoonSpec artifacts and final verification evidence.
