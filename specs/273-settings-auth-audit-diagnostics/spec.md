# Feature Specification: Settings Authorization Audit Diagnostics

**Feature Branch**: `273-settings-auth-audit-diagnostics`
**Created**: 2026-04-28
**Status**: Draft
**Input**:

```text
# MM-543 MoonSpec Orchestration Input

## Source

- Jira issue: MM-543
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Authorization, audit, redaction, and diagnostics
- Labels: `moonmind-workflow-mm-285619b3-4c87-4e03-944f-282e648fa000`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-543 from MM project
Summary: Authorization, audit, redaction, and diagnostics
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-543 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-543: Authorization, audit, redaction, and diagnostics

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 5.6 Least Privilege
- 5.7 Fail Fast
- 11.2 Settings Audit Table
- 12.6 Audit APIs
- 20. Authorization Model
- 21. Audit and Observability
- 22. Security Requirements

Coverage IDs:
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-018
- DESIGN-REQ-025

As an auditor or authorized operator, I can inspect settings changes and diagnostics with least-privilege permissions and redaction so configuration changes are accountable without exposing sensitive values.

Acceptance Criteria
- Permission checks distinguish catalog read, effective read, user write, workspace write, system read/write, secret metadata read, secret value write, secret rotation/disable/delete, provider profile write, operations invoke, and audit read.
- Audit records include setting key, scope, actor, permitted old/new values, redaction status, request/source metadata where available, reason, validation outcome, apply mode, and affected systems.
- Audit values redact raw secrets, sensitive generated config, OAuth state, private keys, token-like values, provider-returned sensitive diagnostics, and descriptor-redacted values.
- SecretRef values are recorded only when authorized by policy and treated as security-relevant metadata.
- Diagnostics answer why a setting is read-only, why a value is effective, where it came from, what changed, why validation failed, what needs restart, and which missing profile/secret/setting blocks launch readiness.

Requirements
- Frontend-hidden controls are not a security boundary.
- Settings changes must be auditable without exposing sensitive values.
- Fail-fast diagnostics must be actionable and must not silently fall back to another sensitive source.

Relevant Implementation Notes
- Settings permissions should be split across catalog/effective reads, user/workspace/system writes, secret metadata/value operations, provider profile operations, operational invocation, and audit reads.
- Invalid settings, missing SecretRefs, broken profile bindings, locked values, unsupported scopes, and launch-blocking readiness gaps must fail explicitly with actionable diagnostics.
- Settings audit storage should capture event type, key, scope, actor, allowed old/new values, redaction state, reason, request metadata, validation outcome, apply mode, and affected systems.
- Audit APIs must redact sensitive and security-relevant values according to descriptor audit policy.
- Backend authorization and validation are authoritative; client-hidden controls are only UX guidance.
- Raw secrets, OAuth state, private keys, token-like values, sensitive generated config, provider-returned sensitive diagnostics, and descriptor-redacted values must not be exposed through audit or diagnostics.
```

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Accountable Settings Inspection

**Summary**: As an auditor or authorized operator, I want settings changes and diagnostics to be protected by least-privilege permissions and redacted audit output so configuration activity is accountable without exposing sensitive values.

**Goal**: Authorized users can inspect settings audit records and diagnostics relevant to their role, while unauthorized users are denied and sensitive values are consistently redacted.

**Independent Test**: Can be fully tested by exercising settings permission checks, audit record creation/retrieval, redaction policy, and diagnostics output for allowed and denied users against representative settings, secrets, provider profiles, and launch-readiness failures.

**Acceptance Scenarios**:

1. **Given** users with distinct settings, secrets, provider profile, operations, and audit permissions, **When** they attempt catalog reads, effective reads, writes, secret operations, provider profile operations, operations invocation, and audit reads, **Then** each action is allowed only by its matching permission and denied otherwise.
2. **Given** a settings change with actor, scope, request context, reason, validation outcome, apply mode, affected systems, and sensitive old/new values, **When** the change is recorded and later inspected, **Then** the audit record includes the permitted metadata and redacts values according to descriptor and secret-safety policy.
3. **Given** audit data includes raw secrets, OAuth state, private keys, token-like values, sensitive generated config, provider-returned sensitive diagnostics, descriptor-redacted values, and authorized SecretRef metadata, **When** audit output is returned, **Then** prohibited values are never exposed and SecretRef metadata is included only when policy authorizes it.
4. **Given** settings are read-only, inherited, recently changed, invalid, restart-sensitive, or missing required profile/secret dependencies, **When** an authorized user requests diagnostics, **Then** diagnostics explain the reason, source, recent change context, validation failure, restart need, or launch-readiness blocker without falling back to another sensitive source.
5. **Given** frontend controls are hidden for a user, **When** that user attempts the same restricted actions through backend routes, **Then** backend authorization and validation still reject unauthorized or invalid access.

### Edge Cases

- A user has audit-read permission but lacks secret metadata permission.
- A SecretRef appears in old or new audit values for a setting whose descriptor requires redaction.
- A provider returns diagnostic text containing token-like or private-key-like material.
- Multiple scopes define the same setting and one scope is operator-locked.
- A launch-readiness diagnostic depends on a missing provider profile and a missing secret at the same time.
- A request has no source IP, request ID, or reason metadata available.

## Assumptions

- Existing MoonMind authentication/session context can identify the actor and role-derived permissions for settings routes.
- Existing settings catalog descriptors can carry or derive audit-redaction policy for sensitive settings.
- This story covers settings authorization, audit output, redaction, and diagnostics behavior; broader settings catalog creation, generic secret storage, and provider profile CRUD are source dependencies but not separate feature stories here.

## Source Design Requirements

- **DESIGN-REQ-014**: Source `docs/Security/SettingsSystem.md` sections 5.6 and 20.1-20.3. Settings capabilities must use separate permissions for catalog reads, effective reads, user/workspace/system writes, secret metadata/value operations, secret lifecycle operations, provider profile operations, operations read/invoke, and audit reads. Scope: in scope. Maps to FR-001 and FR-002.
- **DESIGN-REQ-015**: Source `docs/Security/SettingsSystem.md` sections 11.2, 12.6, and 21.1-21.2. Settings changes and audit APIs must record accountability metadata while redacting sensitive and descriptor-redacted values. Scope: in scope. Maps to FR-003, FR-004, FR-005, FR-006, and FR-007.
- **DESIGN-REQ-018**: Source `docs/Security/SettingsSystem.md` sections 5.7, 21.3, and 22. Settings diagnostics must fail fast with actionable explanations and must not silently fall back to another sensitive source. Scope: in scope. Maps to FR-008, FR-009, and FR-010.
- **DESIGN-REQ-025**: Source `docs/Security/SettingsSystem.md` sections 5.8, 20.3, and 22. Backend authorization and validation are authoritative; frontend-hidden controls are not a security boundary. Scope: in scope. Maps to FR-011 and FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST distinguish settings catalog read, effective settings read, user setting write, workspace setting write, system setting read/write, secret metadata read, secret value write, secret rotation, secret disable, secret delete, provider profile read/write, operations read/invoke, and settings audit read permissions.
- **FR-002**: System MUST authorize every settings write, sensitive metadata read, operational invocation, and audit read using backend permission checks rather than frontend visibility.
- **FR-003**: System MUST record settings audit events with setting key, scope, actor, allowed old value, allowed new value, redaction status, request or source metadata when available, reason, validation outcome, apply mode, and affected systems.
- **FR-004**: System MUST expose audit records only to callers with settings audit read permission and any additional permission needed for security-relevant metadata.
- **FR-005**: System MUST redact raw secrets, sensitive generated config, OAuth state, private keys, token-like values, provider-returned sensitive diagnostics, and descriptor-redacted values from audit output.
- **FR-006**: System MUST treat SecretRef values as security-relevant metadata and include them in audit output only when authorized by policy.
- **FR-007**: System MUST make audit redaction explicit in output so auditors can tell whether values were withheld.
- **FR-008**: System MUST provide diagnostics explaining why a setting is read-only, why a value is effective, where it came from, what changed recently, why validation failed, what needs restart, and which missing profile, secret, or setting blocks launch readiness.
- **FR-009**: System MUST return actionable fail-fast diagnostics for invalid settings, missing SecretRefs, broken provider profile bindings, locked values, unsupported scopes, and launch-readiness blockers.
- **FR-010**: System MUST NOT silently fall back to another sensitive source when a setting, secret, provider profile, or readiness dependency is missing or invalid.
- **FR-011**: System MUST reject unauthorized backend requests even when an equivalent frontend control is hidden or disabled.
- **FR-012**: System MUST ignore client-supplied descriptor metadata for authorization, validation, and redaction decisions.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-543` and this canonical Jira preset brief for traceability.

### Key Entities

- **Settings Permission**: A capability that authorizes a narrow settings, secrets, provider profile, operations, or audit action.
- **Settings Audit Event**: A durable record of a settings-related decision or change, including actor, scope, setting key, allowed values, redaction state, validation and apply metadata, request metadata, and affected systems.
- **Redaction Decision**: The policy result that determines whether a value or metadata field can be displayed, redacted, or omitted for the caller.
- **Settings Diagnostic**: An operator-facing explanation for effective values, read-only state, validation failures, restart needs, recent changes, or launch-readiness blockers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Permission tests cover every permission category in FR-001 with at least one allowed and one denied backend request.
- **SC-002**: Audit tests verify required metadata is present for a representative settings change and that unauthorized callers cannot read audit output.
- **SC-003**: Redaction tests verify no raw secret, OAuth state, private key, token-like value, sensitive generated config, provider-returned sensitive diagnostic, or descriptor-redacted value appears in audit output.
- **SC-004**: Diagnostics tests verify at least one actionable response for read-only state, effective value source, validation failure, restart need, and missing launch-readiness dependency.
- **SC-005**: Backend authorization tests demonstrate that hiding or disabling a frontend control does not grant or bypass restricted access.
- **SC-006**: Final verification confirms `MM-543`, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, and DESIGN-REQ-025 are preserved and covered by implementation evidence.
