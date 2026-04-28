# Feature Specification: Generated User and Workspace Settings UI

**Feature Branch**: `269-generated-user-workspace-settings-ui`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-539 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-539 MoonSpec Orchestration Input

## Source

- Jira issue: MM-539
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Generated User and Workspace settings UI
- Labels: moonmind-workflow-mm-285619b3-4c87-4e03-944f-282e648fa000
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-539 from MM project
Summary: Generated User and Workspace settings UI
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 3. Goals
- 6. Settings Page Topology
- 9. Eligibility Rules
- 13. UI Contract
- 16. User / Workspace Settings
- 27. Example End-to-End Flows
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-004
- DESIGN-REQ-009
- DESIGN-REQ-023

As a Mission Control user, I can configure eligible user and workspace settings through generated controls so local-first configuration is discoverable without bespoke forms for every setting.

Acceptance Criteria
- Given descriptors for boolean, string, bounded number, enum, list, key/value, SecretRef, and read-only settings, the UI renders the documented controls and submits only user intent.
- Given a read-only or operator-locked descriptor, the UI displays the lock reason and does not enable ordinary editing.
- Given a modified setting, the UI can preview validation and affected subsystem information before save.
- Given an overridden value, the UI shows reset-to-inherited behavior and source badges using documented labels.
- A fresh local deployment can reach Settings and configure initial non-secret settings and SecretRef bindings through Mission Control without requiring hand-edited frontend forms.

Requirements
- The frontend must not decide backend eligibility, validation, sensitivity, or authorization.
- Ineligible, secret, or operator-only settings must remain hidden, read-only, or routed to specialized managers according to backend descriptors.
- Generic settings UI must not allow direct environment-variable editing from the browser.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-539 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime UI story from `docs/Security/SettingsSystem.md`; it does not require `moonspec-breakdown`.

## User Story - Generated User and Workspace Settings

**Summary**: As a Mission Control user, I can configure eligible user and workspace settings through generated controls so local-first configuration is discoverable without bespoke forms for every setting.

**Goal**: The Settings page exposes a usable User / Workspace section generated from backend descriptors, while preserving backend authority for eligibility, validation, sensitivity, and authorization.

**Independent Test**: Load Settings with the User / Workspace section selected, serve descriptor data for workspace and user scopes, edit representative descriptor types, preview pending changes, save them through the Settings API, and reset an override while verifying read-only and SecretRef behavior does not expose plaintext or allow unauthorized editing.

**Acceptance Scenarios**

1. Given eligible workspace descriptors, when a user opens Settings -> User / Workspace, then controls render by descriptor type with title, description, source badge, scope badge, validation diagnostics, reload/restart badges where relevant, and affected subsystem details.
2. Given a user switches between workspace and user scope, when the selected scope changes, then the UI fetches descriptors for that scope and only shows settings available for that scope.
3. Given editable boolean, string, bounded number, enum, list, key/value, and SecretRef descriptors, when the user changes values, then the UI tracks only modified keys and shows a preview containing changed keys, old values, new values, validation state, affected subsystems, and reload requirements.
4. Given a descriptor is read-only or operator locked, when it renders, then its lock reason is visible and ordinary editing and saving are disabled for that setting.
5. Given an overridden value, when the user chooses reset, then the UI calls the reset API for that key and refreshes the descriptor so inherited source labels are visible.
6. Given a SecretRef setting, when it renders or is edited, then the UI accepts only SecretRef-style values and does not request or display plaintext secret values.

**Edge Cases**

- The catalog request fails or returns no eligible settings.
- A save request fails validation, conflicts on expected version, or is rejected by backend policy.
- A descriptor has diagnostics before the user edits it.
- A descriptor has no options for a select control.
- A user discards pending edits after changing multiple settings.

## Requirements

### Functional Requirements

- **FR-001**: The User / Workspace section MUST fetch backend-owned settings descriptors for the selected scope instead of hardcoding setting fields.
- **FR-002**: The UI MUST render controls according to descriptor metadata for booleans, strings, bounded numbers, enums, lists, key/value mappings, SecretRef strings, and read-only values.
- **FR-003**: Each settings row MUST display title, description when present, current control or read-only value, source badge, scope badge, validation diagnostics, reload/restart indicators when present, affected subsystem information, reset affordance when overridden, and lock reason when read-only.
- **FR-004**: The UI MUST support search, category filtering, scope switching, modified-only filtering, and read-only filtering without bypassing backend descriptor eligibility.
- **FR-005**: The UI MUST track local edits as explicit user intent and submit only changed keys with expected versions through the Settings API.
- **FR-006**: The UI MUST provide a change preview before save that lists changed keys, old values, new values, validation status, affected subsystems, and reload or restart requirements.
- **FR-007**: The UI MUST support discard for pending edits and reset-to-inherited for settings whose source is a user or workspace override.
- **FR-008**: The UI MUST disable ordinary editing for read-only or operator-locked descriptors and show the backend-provided lock reason.
- **FR-009**: The generic settings UI MUST represent SecretRef settings as references only and MUST NOT request or display plaintext secret values.
- **FR-010**: Save and reset failures MUST surface actionable, sanitized errors without exposing credentials or raw secret-like values.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-539` and the canonical Jira preset brief.

### Key Entities

- **Setting Descriptor**: Backend-owned metadata describing one eligible setting, including key, title, description, category, section, type, UI control, scopes, current/effective value, source, options, constraints, sensitivity, read-only state, reload requirements, affected subsystems, diagnostics, and value version.
- **Pending Setting Change**: Local UI state for one modified descriptor, carrying the user-entered value, original value, expected version, validation state, affected subsystem information, and reset/save availability.
- **Settings Scope**: User or workspace context determining descriptor eligibility, inheritance, override source, and reset behavior.

## Assumptions

- The existing Settings API is the backend authority for eligibility, validation, write permission, effective values, and reset behavior.
- The story covers the User / Workspace Settings section; Providers & Secrets and Operations remain specialized sections.
- A descriptor with `ui: "secret_ref_picker"` or `type: "secret_ref"` is a SecretRef reference control, not a plaintext secret editor.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Security/SettingsSystem.md` section 3): Mission Control must expose a unified Settings surface with a declarative catalog, safe exhaustiveness, scoped overrides, explainable effective values, server-side validation, secret-safe behavior, local-first configuration, auditability, and documented runtime application semantics. Scope: in scope. Maps to FR-001, FR-003, FR-005, FR-006, FR-007, FR-009, and FR-010.
- **DESIGN-REQ-004** (`docs/Security/SettingsSystem.md` section 6): The User / Workspace Settings section must be catalog-driven for user preferences, workspace defaults, non-secret integration defaults, feature flags, policy knobs, and SecretRef bindings. Scope: in scope. Maps to FR-001, FR-002, FR-004, and FR-009.
- **DESIGN-REQ-009** (`docs/Security/SettingsSystem.md` sections 9 and 13): The generic renderer must only expose eligible settings and must render documented controls, badges, validation errors, change preview, read-only lock reasons, and SecretRef pickers without frontend-owned eligibility decisions. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-006, FR-008, and FR-009.
- **DESIGN-REQ-023** (`docs/Security/SettingsSystem.md` sections 16 and 27): User settings must inherit workspace defaults unless overridden and the UI must make inheritance, reset-to-inherited, and workspace default changes visible in end-to-end flows. Scope: in scope. Maps to FR-003, FR-005, FR-007, and FR-010.

## Success Criteria

- **SC-001**: A user can open the User / Workspace section and see descriptor-driven controls for eligible settings without adding bespoke frontend forms per setting.
- **SC-002**: Editing and saving multiple settings submits only changed keys and refreshes the displayed effective values after success.
- **SC-003**: Read-only descriptors are visible with lock reasons and cannot be changed through ordinary controls.
- **SC-004**: SecretRef settings are handled as references only, with no plaintext secret display or request path in the generic UI.
- **SC-005**: Resetting an overridden setting removes the override and shows inherited source labeling after refresh.
- **SC-006**: Verification evidence preserves `MM-539`, the canonical Jira preset brief, and DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-009, and DESIGN-REQ-023 in MoonSpec artifacts.
