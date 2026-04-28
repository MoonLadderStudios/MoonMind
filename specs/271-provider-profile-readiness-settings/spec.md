# Feature Specification: Provider Profile Management and Readiness in Settings

**Feature Branch**: `271-provider-profile-readiness-settings`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-541 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-541 MoonSpec Orchestration Input

## Source

- Jira issue: MM-541
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Provider Profile management and readiness in Settings
- Labels: moonmind-workflow-mm-285619b3-4c87-4e03-944f-282e648fa000
- Trusted fetch tool: `jira.get_issue`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-541 from MM project
Summary: Provider Profile management and readiness in Settings
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-541 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-541: Provider Profile management and readiness in Settings

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 2.3 What Provider Profiles own
- 6.1 Providers & Secrets
- 10.3 Provider Profile Resolution
- 15. Provider Profiles Integration
- 27.2 Add GitHub Token
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-012
- DESIGN-REQ-025

As a workspace admin, I can manage provider profiles in Settings and see readiness diagnostics while Provider Profiles remain the execution contract for runtime/provider launch semantics.

Acceptance Criteria
- Provider profile editing exposes runtime, provider, credential source class, materialization mode, default model, overrides, SecretRef role bindings, OAuth volume metadata, concurrency, cooldown, tags, priority, default status, and readiness where applicable.
- Provider profile readiness combines schema validity, required fields, SecretRef resolvability, OAuth volume status, provider validation, enabled state, concurrency availability, and cooldown state.
- A workspace or user setting may select a provider profile reference, but generic setting values do not inline runtime launch semantics.
- Missing provider profile references return explicit diagnostics and launch blockers rather than silent fallback.
- Runtime strategies still own command construction, environment shaping, generated runtime files, process launch, and capability checks.

Requirements
- Provider Profiles remain first-class resources inside Providers & Secrets.
- Role-aware SecretRef pickers must describe what the profile needs and where the value can be resolved.
- Readiness state must be observable before affected launches run.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-541 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

- Input type: Single-story runtime feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable Provider Profiles story.
- Selected mode: Runtime.
- Source design: `docs/Security/SettingsSystem.md` is treated as runtime source requirements.
- Resume decision: No existing Moon Spec artifacts for `MM-541` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-541`.

## User Story - Manage Provider Profiles and Readiness

**Summary**: As a workspace admin, I can manage provider profiles in Settings and see readiness diagnostics while Provider Profiles remain the execution contract for runtime/provider launch semantics.

**Goal**: MoonMind lets authorized Settings users create, inspect, edit, validate, enable, disable, and choose default provider profiles from Providers & Secrets, with actionable readiness diagnostics that explain whether a profile can launch without turning provider-profile semantics into generic settings.

**Independent Test**: Open Settings -> Providers & Secrets, create or edit a provider profile with runtime/provider details, model defaults, SecretRef role bindings, OAuth volume metadata, concurrency and cooldown fields, validate readiness, select a default profile, and verify generic user/workspace settings can reference only provider profile identifiers while missing or unhealthy profiles produce explicit launch blockers.

### Acceptance Scenarios

1. Given a workspace admin opens Settings -> Providers & Secrets, when provider profiles are listed, then each profile shows runtime, provider, credential source class, materialization mode, default model, overrides, SecretRef binding summary, OAuth volume metadata, concurrency, cooldown, tags, priority, enabled/default state, and readiness where applicable.
2. Given a workspace admin edits a provider profile, when required fields are missing or invalid, then the form reports schema and required-field diagnostics before the profile is used for launch.
3. Given a provider profile requires secrets, when its SecretRef role bindings render, then each picker labels the required role and stores only the selected SecretRef location without exposing plaintext.
4. Given a provider profile uses OAuth-backed credentials, when Settings renders readiness, then OAuth volume presence and status contribute to the readiness result.
5. Given a profile has disabled state, exhausted concurrency, active cooldown, unresolved SecretRefs, failed provider validation, or missing required fields, when readiness is evaluated, then Settings shows explicit diagnostics and affected launches are blocked rather than silently falling back.
6. Given a workspace or user setting selects a default provider profile, when the profile reference is missing or disabled, then the effective setting returns an explicit diagnostic and launch blocker while preserving Provider Profiles as the launch-semantics authority.
7. Given runtime strategies launch an agent, when provider profile settings change, then command construction, environment shaping, generated runtime files, process launch, and capability checks remain owned by runtime strategies.

### Edge Cases

- Provider profile list or readiness fetch fails.
- A profile references a deleted, disabled, malformed, or policy-disallowed SecretRef.
- OAuth volume metadata exists but the backing credential state is missing or stale.
- A default provider profile is deleted, disabled, or no longer valid for its runtime/provider.
- Multiple profiles compete for default selection within the same runtime.

## Requirements

### Functional Requirements

- **FR-001**: Settings MUST expose Provider Profiles as first-class resources inside Providers & Secrets with list, create, update, validate, enable, disable, delete, and default-selection workflows where authorized.
- **FR-002**: Provider profile list and detail views MUST show runtime, provider, credential source class, materialization mode, default model, model overrides, tags, priority, enabled/default state, and readiness where applicable.
- **FR-003**: Provider profile editing MUST expose profile-level SecretRef role bindings, OAuth volume metadata, concurrency limits, cooldown state or policy, and runtime/provider binding metadata without reducing profiles to generic key/value settings.
- **FR-004**: Role-aware SecretRef pickers MUST describe the required credential role and store only SecretRef references without displaying plaintext secret values.
- **FR-005**: Provider profile readiness MUST combine schema validity, required fields, SecretRef resolvability, OAuth volume status, provider-specific validation, enabled state, concurrency availability, and cooldown state where applicable.
- **FR-006**: Missing, disabled, unhealthy, or unresolved provider profile references selected by user/workspace settings MUST return explicit diagnostics and launch blockers rather than silently falling back.
- **FR-007**: Generic user/workspace settings MAY reference provider profile identifiers or selectors but MUST NOT inline runtime selection, credential source class, materialization, command construction, environment shaping, generated runtime files, process launch, or capability-check semantics.
- **FR-008**: Runtime strategies MUST remain the authority for command construction, environment shaping, generated runtime files, process launch, and runtime-specific capability checks.
- **FR-009**: Provider profile readiness and mutation failures MUST surface actionable, sanitized diagnostics without exposing raw credentials, tokens, OAuth state blobs, or decrypted files.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-541` and this canonical Jira preset brief.

### Key Entities

- **ProviderProfile**: A first-class resource that owns runtime/provider selection, credential source class, default model intent, materialization mode, routing metadata, SecretRef role bindings, OAuth volume references, concurrency and cooldown policy, priority, tags, enabled state, and default selection.
- **ProviderProfileReadiness**: A diagnostic result that combines profile schema validity, required fields, SecretRef and OAuth readiness, provider validation, enabled state, concurrency availability, and cooldown state.
- **SecretRefRoleBinding**: A mapping from a profile-required credential role to a SecretRef location, stored as metadata and never as plaintext.
- **ProviderProfileReferenceSetting**: A user or workspace setting value that references a provider profile by stable identifier without copying provider launch semantics into generic settings.
- **LaunchBlockerDiagnostic**: A sanitized explanation that a selected provider profile cannot be used for launch because it is missing, disabled, unhealthy, unavailable, or unresolved.

## Assumptions

- Existing Provider Profile data models, services, and runtime strategy boundaries remain authoritative for launch semantics.
- This story extends the Settings Providers & Secrets surface rather than introducing a separate provider profile administration page.
- Managed Secrets and OAuth credential storage semantics remain owned by their adjacent systems; this story only displays and binds their references for provider profiles.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-002 | `docs/Security/SettingsSystem.md` section 2.3 | Provider Profiles own runtime/provider selection, credential source class, routing metadata, default model intent, materialization strategy, launch shaping, concurrency, and cooldown policy; Settings may expose forms but must not become the execution authority. | In scope | FR-001, FR-002, FR-003, FR-007, FR-008 |
| DESIGN-REQ-012 | `docs/Security/SettingsSystem.md` sections 6.1 and 10.3 | Providers & Secrets must expose provider-profile readiness while user/workspace settings may reference provider profiles without inlining provider profile semantics. | In scope | FR-005, FR-006, FR-007 |
| DESIGN-REQ-025 | `docs/Security/SettingsSystem.md` sections 15 and 27.2 | Settings may manage provider profiles and role-aware SecretRef bindings; readiness combines schema validity, required fields, SecretRef resolvability, OAuth volume status, provider validation, enabled state, concurrency, and cooldown. | In scope | FR-001, FR-003, FR-004, FR-005, FR-009 |

## Success Criteria

- **SC-001**: A workspace admin can list and edit provider profiles from Settings -> Providers & Secrets without a separate administration surface.
- **SC-002**: Provider profile readiness diagnostics identify at least schema, required-field, SecretRef, OAuth volume, provider validation, enabled-state, concurrency, and cooldown blockers where applicable.
- **SC-003**: SecretRef role binding tests prove profile forms store SecretRef references only and never render submitted plaintext.
- **SC-004**: User/workspace settings can reference provider profiles by identifier while missing or disabled references produce explicit diagnostics and launch blockers.
- **SC-005**: Runtime strategy tests or boundary checks prove provider profile Settings changes do not move command construction, environment shaping, generated runtime files, process launch, or capability checks into generic settings.
- **SC-006**: Verification evidence preserves `MM-541`, DESIGN-REQ-002, DESIGN-REQ-012, and DESIGN-REQ-025 across MoonSpec artifacts.
