# Feature Specification: Settings Catalog and Effective Values

**Feature Branch**: `267-settings-catalog-effective-values`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-537 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-537` from the trusted `jira.get_issue` response, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-537`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-537` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-537 MoonSpec Orchestration Input

## Source

- Jira issue: MM-537
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Settings catalog and effective-value contract
- Labels: moonmind-workflow-mm-285619b3-4c87-4e03-944f-282e648fa000
- Trusted fetch tool: jira.get_issue
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose recommendedImports.presetInstructions, normalizedPresetBrief, presetBrief, or presetInstructions.

## Canonical MoonSpec Feature Request

Jira issue: MM-537 from MM project
Summary: Settings catalog and effective-value contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-537 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-537: Settings catalog and effective-value contract

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 1. Summary
- 5. Core Principles
- 7. Key Concepts
- 8. Settings Catalog Contract
- 10. Resolution Model
- 12. API Contract
- 26. Suggested Internal Components

Coverage IDs:
- DESIGN-REQ-003
- DESIGN-REQ-005
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-022

User Story
As a MoonMind operator or settings client, I can read a backend-owned settings catalog and effective-value explanations so configuration surfaces are discoverable, typed, scoped, and authoritative.

Acceptance Criteria
- Given an explicitly exposed setting, the catalog returns its stable key, type, section, category, scopes, constraints, source, reload metadata, dependency metadata, and audit policy.
- Given an unexposed backend setting, the catalog omits it and write attempts return setting_not_exposed.
- Given defaults, config/environment inputs, workspace overrides, user overrides, SecretRefs, provider-profile references, and operator locks, the effective API returns the winning value and its source explanation.
- Given missing defaults, null inherited values, unresolved SecretRefs, missing provider profile references, policy-blocked values, or invalid migrated values, the resolver returns explicit diagnostics instead of silently falling back.
- Settings API errors use the documented structured error shape for unknown keys, invalid scopes, read-only settings, operator locks, invalid values, version conflicts, and permission failures.

Requirements
- The backend is the authority for descriptor metadata and effective-value resolution.
- Catalog responses are reusable by UI, API clients, CLI tooling, tests, diagnostics, onboarding, and documentation generators.
- Descriptor keys and option values remain stable durable API contract values.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Security/SettingsSystem.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`.
- Resume decision: No existing Moon Spec artifacts for `MM-537` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-537` because the trusted Jira preset brief defines one independently testable story. Scoped override persistence is intentionally deferred to linked issue `MM-538`.

## User Story - Read Settings Catalog and Effective Values

**Summary**: As a MoonMind operator or settings client, I want backend-owned settings catalog descriptors and effective-value explanations so that configuration surfaces can be discovered, typed, scoped, and treated as authoritative.

**Goal**: MoonMind exposes read-side settings contracts that enumerate explicitly exposed settings, omit unexposed backend fields, resolve effective values from the supported source chain, and report resolver diagnostics through structured errors or diagnostic entries instead of hidden fallback behavior.

**Independent Test**: Call the settings catalog and effective-value endpoints for user and workspace scopes. Verify exposed descriptors include stable metadata, unexposed backend fields are omitted, effective responses explain the winning source, unresolved references or invalid states produce diagnostics, unsupported writes fail with a structured `setting_not_exposed` error, and all artifacts preserve `MM-537`.

**Acceptance Scenarios**:

1. **Given** a backend setting is explicitly exposed, **When** a settings client reads the catalog, **Then** the descriptor includes stable key, type, section, category, scopes, constraints, source metadata, reload metadata, dependency metadata, and audit policy.
2. **Given** a backend setting is not explicitly exposed, **When** a settings client reads the catalog, **Then** the setting is omitted and any write attempt against that key returns a structured `setting_not_exposed` error.
3. **Given** defaults and deployment-provided configuration are available for a setting, **When** a settings client reads effective values, **Then** the response includes the winning value, source, source explanation, value version, and any diagnostics.
4. **Given** a setting can resolve to a SecretRef or provider-profile reference, **When** the reference is unresolved or missing, **Then** the effective response reports an explicit diagnostic without exposing plaintext secret values or silently falling back to a different value.
5. **Given** a settings request uses an unknown key, invalid scope, read-only setting, operator-locked setting, invalid value, version conflict, or permission failure, **When** the API rejects the request, **Then** the error uses the documented structured shape with error code, message, key, scope, and details.
6. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-537` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- A field exists on backend configuration but lacks explicit MoonMind exposure metadata, so it must remain absent from catalog output and fail writes as `setting_not_exposed`.
- A setting has no default value, an inherited null value, an intentionally null override, an unresolved SecretRef, a missing provider-profile reference, a policy-blocked value, or an invalid migrated value, and the response must distinguish the condition through diagnostics.
- A catalog filter asks for a valid section and an unsupported scope combination, and the API must return only descriptors eligible for the requested scope rather than leaking unrelated metadata.
- A SecretRef-like setting must return only a reference and security metadata, never a raw secret value.
- A future write-capable settings story adds scoped overrides; this story's read-side catalog and effective contracts must remain stable.

## Assumptions

- `MM-537` is scoped to backend-owned catalog and effective-value read contracts plus structured rejection for unsupported writes; durable scoped override persistence belongs to linked issue `MM-538`.
- Initial read-side behavior may resolve defaults and deployment-provided configuration without implementing user/workspace override storage.
- Provider profile references and SecretRefs are exposed as references and diagnostics only; plaintext secret resolution remains outside this story.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-003 | `docs/Security/SettingsSystem.md` §1, §7.2, §8.2-§8.4 | The backend owns the settings catalog registry and descriptor metadata so clients do not duplicate editable setting knowledge. | In scope | FR-001, FR-002, FR-008 |
| DESIGN-REQ-005 | `docs/Security/SettingsSystem.md` §7.1, §8.1, §8.4 | Catalog descriptors expose stable keys, types, section/category, scopes, constraints, options, source metadata, reload metadata, dependencies, read-only state, and audit policy as durable API contract values. | In scope | FR-001, FR-002, FR-006 |
| DESIGN-REQ-007 | `docs/Security/SettingsSystem.md` §8.3, §9.1-§9.4 | Only explicitly exposed, eligible settings appear in generic settings surfaces; ineligible or secret-like plaintext fields are omitted and cannot be edited as ordinary settings. | In scope | FR-003, FR-007 |
| DESIGN-REQ-008 | `docs/Security/SettingsSystem.md` §10.1-§10.5, §12.2 | Effective-value resolution returns the winning value, source explanation, and explicit diagnostics for missing defaults, null inheritance, unresolved references, policy blocks, and invalid migrated values. | In scope | FR-004, FR-005 |
| DESIGN-REQ-022 | `docs/Security/SettingsSystem.md` §12.1-§12.7, §18.1 | Settings API responses and errors use structured contracts for catalog reads, effective reads, validation failures, unknown keys, invalid scopes, read-only settings, operator locks, invalid values, version conflicts, and permission failures. | In scope | FR-005, FR-007, FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose a backend-owned settings catalog endpoint that returns descriptors grouped by section and category for explicitly exposed settings.
- **FR-002**: Each exposed descriptor MUST include stable key, title, description, section, category, type, UI control, allowed scopes, default value, effective value, override value, source, options, constraints, sensitivity metadata, read-only metadata, reload metadata, dependency metadata, order, and audit policy.
- **FR-003**: The catalog MUST omit backend settings that lack explicit exposure metadata or are ineligible for ordinary settings editing.
- **FR-004**: The system MUST expose effective settings endpoints that return the winning value, source, source explanation, value version, and diagnostics for user and workspace scopes.
- **FR-005**: Effective-value resolution MUST distinguish missing defaults, inherited nulls, intentionally null override values, unresolved SecretRefs, missing provider-profile references, policy-blocked values, and invalid migrated values without silently falling back.
- **FR-006**: Descriptor keys and option values MUST remain stable durable contract values independent of display labels.
- **FR-007**: Write attempts against unexposed, unknown, read-only, or operator-locked settings MUST fail with structured settings errors rather than mutating state.
- **FR-008**: The backend MUST remain the authority for descriptor metadata and effective-value resolution consumed by UI, API clients, CLI tooling, tests, diagnostics, onboarding, and documentation generators.
- **FR-009**: Settings API errors MUST include a stable error code, human-readable message, key when applicable, scope when applicable, and details object.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-537`.

### Key Entities

- **Setting Descriptor**: Backend-owned metadata describing one exposed setting, including stable key, type, UI control, scope eligibility, constraints, options, source, reload behavior, dependencies, and audit policy.
- **Effective Setting Value**: The resolved value for a setting at a requested scope, including source explanation, version, and diagnostics.
- **Setting Diagnostic**: A structured non-secret explanation of resolver conditions such as missing defaults, unresolved SecretRefs, missing provider-profile references, policy blocks, or invalid migrated values.
- **Settings Error**: The structured error response used when a settings request cannot be fulfilled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Catalog tests prove at least one exposed setting returns all required descriptor fields and at least one unexposed backend setting is omitted.
- **SC-002**: Effective-value tests prove default/configured values include winning value, source, source explanation, value version, and no hidden fallback.
- **SC-003**: Resolver diagnostic tests prove unresolved references or missing values are reported explicitly without revealing plaintext secrets.
- **SC-004**: API error tests prove unsupported writes and invalid read requests return the documented structured error shape.
- **SC-005**: Traceability review confirms `MM-537` and DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, and DESIGN-REQ-022 remain preserved in MoonSpec artifacts and downstream evidence.
