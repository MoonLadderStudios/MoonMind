# Feature Specification: Server-Side Validation and Cross-Setting Policy Enforcement

**Feature Branch**: `341-server-side-validation`
**Created**: 2026-05-12
**Status**: Draft
**Input**:

```text
# MM-656 MoonSpec Orchestration Input

## Source

- Jira issue: MM-656
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Server-side validation and cross-setting policy enforcement
- Labels: `moonmind-workflow-mm-3997e9d9-e676-4b50-8e8d-e319fc13ef97`
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:6a56ae2e-2dd6-49a9-8d85-885149e190b2/artifacts/moonspec-inputs/MM-656-trusted-jira-get-issue.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-656 from MM project
Summary: Server-side validation and cross-setting policy enforcement
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-656 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-656: Server-side validation and cross-setting policy enforcement

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 5.7 Fail Fast
- 16.3 Workspace Policy Constraints
- 18 Validation Model

Coverage IDs:
- S5.7
- S16.3
- S18.1
- S18.2
- S18.3
- S22.4
- S22.10
- S22.11
- S25.4
- S25.5
- S25.6
- S25.7
- S26.SettingsValidator
- S29.3

Centralize all setting writes through a server-side validator that confirms: key exists, key is exposed, scope is allowed, actor is authorized, value type matches descriptor, enum/numeric/string/list/object constraints hold, SecretRef syntax is valid, referenced resources exist, dependencies are satisfied, and workspace policy permits the value. Cross-setting validation must enforce combinations such as profile selectors referencing only enabled profiles, canary percentage being zero when the feature is disabled, default runtime being inside the workspace allowed list, SecretRef backend matching workspace policy, and operational mode not conflicting with maintenance policy.

Validation runs at descriptor generation, on write requests, before persistence, on effective-value preview, before launch/operation execution, and during readiness diagnostics. Invalid settings, missing SecretRefs, broken provider profile bindings, locked values, and unsupported scopes must fail explicitly with structured errors and never silently fall back to another sensitive source.

Acceptance Criteria
- Type validation passes for booleans/strings/numbers/enums/lists/objects/SecretRefs and rejects mismatches.
- Numeric and string constraints are enforced at write time and at preview time.
- Cross-setting rules from section 18.2 each have a regression test and produce typed structured errors.
- Validation timing from section 18.3 is exercised by tests at every listed boundary.

Requirements
- Route all setting writes through a server-side validation path that verifies key existence, exposure, scope, actor authorization, value type, descriptor constraints, SecretRef syntax, referenced resources, dependencies, and workspace policy.
- Enforce cross-setting policy combinations for enabled profile selectors, disabled-feature canary percentage, allowed default runtime, SecretRef backend policy, and operational mode versus maintenance policy.
- Run validation at descriptor generation, write requests, pre-persistence, effective-value preview, launch or operation execution, and readiness diagnostics.
- Return structured, typed errors for invalid settings, missing SecretRefs, broken provider profile bindings, locked values, unsupported scopes, and policy violations.
- Fail fast without silently falling back to another sensitive source.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path: `docs/Security/SettingsSystem.md`.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

Preserved source Jira preset brief: `MM-656` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-656` and local handoff `/work/agent_jobs/mm:6a56ae2e-2dd6-49a9-8d85-885149e190b2/artifacts/moonspec-inputs/MM-656-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserving the `MM-656` implementation brief was found under `specs/`; `Specify` is the first incomplete stage.

## User Story - Validate Setting Changes Before They Take Effect

**Summary**: As an operator or authorized user changing MoonMind settings, I want every setting change and preview to be checked against the catalog, authorization rules, data constraints, dependencies, SecretRef rules, and workspace policy so that unsafe or invalid configuration cannot take effect silently.

**Goal**: Ensure settings changes, previews, launches, operations, catalog generation, and readiness diagnostics all surface consistent, actionable validation outcomes before invalid configuration can affect system behavior.

**Independent Test**: Can be fully tested by attempting valid and invalid setting changes, previews, launch readiness checks, and diagnostics across the documented validation boundaries, then confirming accepted values pass and rejected values return typed structured errors without falling back to another sensitive source.

**Acceptance Scenarios**:

1. **Given** an exposed setting accepts booleans, strings, numbers, enums, lists, objects, or SecretRefs, **When** a matching value is submitted or previewed, **Then** validation accepts the value and reports no structured error for that rule.
2. **Given** an exposed setting receives a value with the wrong type, out-of-range number, invalid string, invalid enum, malformed list, malformed object, or malformed SecretRef, **When** the change is submitted or previewed, **Then** validation rejects it with a typed structured error naming the setting and failed rule.
3. **Given** a setting references another resource such as a provider profile or SecretRef, **When** the referenced resource is missing, disabled, not authorized, or blocked by workspace policy, **Then** validation rejects the change with an actionable structured error and does not use an alternate sensitive source.
4. **Given** workspace policy restricts allowed runtimes, providers, canary percentage, publication modes, SecretRef backends, or operations during maintenance mode, **When** a setting combination violates the policy, **Then** validation rejects the combination before it can take effect.
5. **Given** validation is required during descriptor generation, write request handling, durable change application, effective-value preview, launch or operation execution, and readiness diagnostics, **When** invalid configuration is encountered at any boundary, **Then** the same rule is enforced and reported with boundary-appropriate structured diagnostics.
6. **Given** a setting is locked, in an unsupported scope, not exposed by the catalog, or unknown, **When** a user or operator attempts to change it, **Then** the change is rejected explicitly with no silent mutation or fallback behavior.

### Edge Cases

- Unknown setting keys are rejected even when the submitted value has a valid shape.
- Client-provided descriptor metadata cannot make an ineligible key, scope, or value valid.
- Operator-locked settings remain read-only for ordinary user and workspace changes.
- Missing SecretRefs, broken provider profile bindings, and unsupported scopes produce actionable errors instead of generic failures.
- A feature-disabled canary setting remains invalid unless the canary percentage is zero.
- Maintenance policy prevents conflicting operational mode settings from taking effect.
- SecretRef validation reports redacted diagnostics only and never exposes referenced plaintext.

## Assumptions

- Existing MoonMind authorization, catalog exposure, SecretRef ownership, provider profile readiness, and settings audit concepts remain authoritative; this story specifies validation behavior across those existing product concepts rather than introducing a new settings domain.
- Validation errors are considered structured when consumers can identify at minimum the setting key, rule or error code, message, and relevant scope without parsing free-form text.
- Runtime mode is required because Jira Orchestrate always runs as a runtime implementation workflow for this issue.

## Source Design Requirements

- **DESIGN-REQ-001**: `docs/Security/SettingsSystem.md` section 5.7, coverage S5.7. Invalid settings, missing SecretRefs, broken provider profile bindings, locked values, and unsupported scopes must fail explicitly with actionable errors and must not silently fall back to another sensitive source. Scope: in scope. Mapped requirements: FR-006, FR-007, FR-010.
- **DESIGN-REQ-002**: `docs/Security/SettingsSystem.md` section 16.3, coverage S16.3. Workspace settings may constrain user settings, including allowed runtimes, providers, maximum canary percentage, publication modes, allowed SecretRef backends, and operations during maintenance mode. Scope: in scope. Mapped requirements: FR-005, FR-006.
- **DESIGN-REQ-003**: `docs/Security/SettingsSystem.md` section 18.1, coverage S18.1. Every setting write must be validated for key existence, exposure, scope, actor authorization, value type, allowed enum values, numeric bounds, string constraints, list and object constraints, SecretRef syntax, referenced resources, dependencies, and workspace policy. Scope: in scope. Mapped requirements: FR-001, FR-002, FR-003, FR-004, FR-005.
- **DESIGN-REQ-004**: `docs/Security/SettingsSystem.md` section 18.2, coverage S18.2. Cross-setting validation must reject invalid combinations such as disabled or unavailable profile selectors, nonzero canary percentage for disabled features, runtime outside the workspace allowed list, disallowed SecretRef backend, and operational mode conflicting with maintenance policy. Scope: in scope. Mapped requirements: FR-005, FR-006.
- **DESIGN-REQ-005**: `docs/Security/SettingsSystem.md` section 18.3, coverage S18.3. Validation must occur during catalog descriptor generation, write request handling, before durable change application, effective-value preview, before launch or operation execution, and readiness diagnostics. Scope: in scope. Mapped requirements: FR-008.
- **DESIGN-REQ-006**: `docs/Security/SettingsSystem.md` section 22, coverage S22.4. The authoritative system owns catalog generation, eligibility, validation, and authorization. Scope: in scope. Mapped requirements: FR-001, FR-004, FR-007.
- **DESIGN-REQ-007**: `docs/Security/SettingsSystem.md` section 22, coverage S22.10. Values must be size-limited and schema-validated before they become durable. Scope: in scope. Mapped requirements: FR-002, FR-003, FR-008.
- **DESIGN-REQ-008**: `docs/Security/SettingsSystem.md` section 22, coverage S22.11. Object settings must not allow arbitrary executable code, templates, or commands unless a specialized subsystem explicitly owns that behavior. Scope: in scope. Mapped requirements: FR-003, FR-010.
- **DESIGN-REQ-009**: `docs/Security/SettingsSystem.md` section 25, coverage S25.4-S25.7. Validation tests must cover unknown keys, invalid scopes, supported value types including SecretRefs, and numeric and string constraints. Scope: in scope. Mapped requirements: FR-001, FR-002, FR-003, FR-011.
- **DESIGN-REQ-010**: `docs/Security/SettingsSystem.md` section 26, coverage S26.SettingsValidator. Settings validation must cover write payloads, dependencies, and policies as a distinct desired-state responsibility. Scope: in scope. Mapped requirements: FR-004, FR-005, FR-006, FR-008.
- **DESIGN-REQ-011**: `docs/Security/SettingsSystem.md` section 29, coverage S29.3. A setting cannot bypass authoritative validation. Scope: in scope. Mapped requirements: FR-008, FR-010, FR-011.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST reject setting writes for unknown keys, unexposed keys, unsupported scopes, or actors that are not authorized for the requested setting scope.
- **FR-002**: The system MUST validate setting values against the declared type vocabulary for booleans, strings, numbers, enums, lists, objects, and SecretRefs.
- **FR-003**: The system MUST enforce enum allow-lists, numeric bounds, string constraints, list constraints, object constraints, value size limits, and any schema restrictions before accepting a setting value.
- **FR-004**: The system MUST validate SecretRef syntax and referenced resource existence or readiness where the setting depends on external references, without exposing referenced plaintext.
- **FR-005**: The system MUST enforce workspace policy constraints on setting values and combinations, including allowed runtimes, allowed providers, maximum canary percentage, publication modes, allowed SecretRef backends, and maintenance-mode operation limits.
- **FR-006**: The system MUST reject invalid cross-setting combinations for profile selectors, disabled-feature canary percentages, default runtime choices, SecretRef backend choices, and operational mode conflicts.
- **FR-007**: The system MUST reject attempts to change locked settings, settings in unsupported scopes, and catalog-ineligible settings with structured, actionable validation errors.
- **FR-008**: The system MUST apply the same validation rule set at catalog descriptor generation, write request handling, before durable change application, effective-value preview, before launch or operation execution, and readiness diagnostics.
- **FR-009**: The system MUST return structured validation errors that identify the setting key, scope when applicable, failed rule or code, human-readable message, and whether the failure blocks persistence, preview, launch, operation execution, or readiness.
- **FR-010**: The system MUST fail fast for invalid settings, missing SecretRefs, broken provider profile bindings, locked values, unsupported scopes, and policy violations without silently falling back to another sensitive source.
- **FR-011**: The validation coverage MUST include regression checks for accepted and rejected supported value types, numeric and string constraints, cross-setting rules, validation timing boundaries, and fail-fast sensitive-source behavior.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-656` and the original preset brief for traceability.

### Key Entities

- **Setting Descriptor**: Catalog-owned definition of a setting's key, supported scopes, value type, constraints, eligibility, sensitivity policy, and reload or operational behavior.
- **Setting Change**: A requested user, workspace, or operational setting value that must be validated before it can affect previews, durable state, launches, operations, or diagnostics.
- **Workspace Policy**: Constraints that limit allowed values or setting combinations for a workspace, including runtime, provider, canary, publication, SecretRef backend, and maintenance-mode restrictions.
- **Referenced Resource**: A provider profile, SecretRef, runtime, publication target, or other named object that a setting may depend on and that must exist and be allowed when referenced.
- **Validation Result**: The accepted outcome or structured error produced for one setting or setting combination at a specific validation boundary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation tests cover all documented supported value categories: booleans, strings, numbers, enums, lists, objects, and SecretRefs, with at least one accepted and one rejected case for each category.
- **SC-002**: Numeric and string constraint tests demonstrate rejection at both setting write time and effective-value preview time.
- **SC-003**: Each documented cross-setting rule from section 18.2 has at least one regression test that produces a typed structured error for an invalid combination.
- **SC-004**: Each documented validation timing boundary from section 18.3 is exercised by at least one test or verification scenario.
- **SC-005**: Traceability review confirms `MM-656`, the original Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-011 remain present in MoonSpec artifacts and final verification evidence.
