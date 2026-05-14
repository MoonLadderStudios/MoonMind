# Feature Specification: Settings HTTP API Surface

**Feature Branch**: `352-settings-http-api-surface`
**Created**: 2026-05-14
**Status**: Draft
**Input**:

```text
# MM-657 MoonSpec Orchestration Input

## Source

- Jira issue: MM-657
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Settings HTTP API surface for catalog, effective, update, reset, validate, preview, audit
- Labels: `moonmind-workflow-mm-3997e9d9-e676-4b50-8e8d-e319fc13ef97`
- Trusted fetch tool: `jira.get_issue` via MoonMind MCP
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`; potentially related custom fields present in the response were empty or non-brief metadata.

## Canonical MoonSpec Feature Request

Jira issue: MM-657 from MM project
Summary: Settings HTTP API surface for catalog, effective, update, reset, validate, preview, audit
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 12 API Contract
Coverage IDs:
- S12.1
- S12.2
- S12.3
- S12.4
- S12.5
- S12.6
- S12.7
- S20.3
- S22.5
- S22.9
- S25.14
- S29.1
- S29.2

Implement the settings API: `GET /api/v1/settings/catalog` (with `section` and `scope` filters), `GET /api/v1/settings/effective[/{key}]`, `PATCH /api/v1/settings/{user|workspace}` (with `changes`, `expected_versions`, `reason`), `DELETE /api/v1/settings/{user|workspace}/{key}`, `POST /api/v1/settings/validate`, `POST /api/v1/settings/preview`, and `GET /api/v1/settings/audit`. Responses return descriptor-grouped sections/categories, resolved values with sources, refreshed descriptors after writes, and structured error envelopes (`unknown_setting`, `setting_not_exposed`, `scope_not_allowed`, `read_only_setting`, `operator_locked`, `invalid_setting_value`, `secret_ref_not_resolvable`, `provider_profile_not_found`, `version_conflict`, `permission_denied`, `requires_confirmation`).

Validation/preview must return effective-value diffs, dependency warnings, and reload requirements without committing.

Acceptance:
- All listed endpoints exist with the documented query parameters and request bodies.
- Catalog responses are grouped into the three top-level sections (`providers-secrets`, `user-workspace`, `operations`).
- Update writes use `expected_versions` for optimistic concurrency and emit `version_conflict` on stale writes.
- Errors match the structured shape from §12.7 with `error`, `message`, `key`, `scope`, and contextual `details`.
- Audit reads honor descriptor redaction policy.

Requirements
- Implement the settings HTTP API endpoints documented in the Jira brief: `GET /api/v1/settings/catalog`, `GET /api/v1/settings/effective[/{key}]`, `PATCH /api/v1/settings/{user|workspace}`, `DELETE /api/v1/settings/{user|workspace}/{key}`, `POST /api/v1/settings/validate`, `POST /api/v1/settings/preview`, and `GET /api/v1/settings/audit`.
- Support documented catalog filters, effective value reads, write payloads with `changes`, `expected_versions`, and `reason`, reset/delete behavior, validation, preview, and audit reads.
- Return descriptor-grouped sections and categories, resolved values with sources, refreshed descriptors after writes, and structured error envelopes matching the documented shape.
- Enforce optimistic concurrency with `expected_versions` and emit `version_conflict` on stale writes.
- Ensure validation and preview return effective-value diffs, dependency warnings, and reload requirements without committing changes.
- Ensure audit reads honor descriptor redaction policy.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-657 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
```

Preserved source Jira preset brief: `MM-657` from the trusted `jira.get_issue` response, reproduced verbatim in `**Input**` above for downstream verification.

Original brief reference: `/work/agent_jobs/mm:d0605b15-f8b2-40f8-9e2f-a9ea20825eef/artifacts/moonspec/MM-657-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserving the `MM-657` implementation brief was found under `specs/`; `Specify` was the first incomplete stage.

## User Story - Operate Settings Through Canonical APIs

**Summary**: As a MoonMind operator or settings client, I want a complete settings API surface for catalog discovery, effective reads, updates, resets, validation, preview, and audit so configuration can be inspected and changed safely through one durable contract.

**Goal**: The Settings System exposes the documented HTTP behaviors as a coherent runtime contract: clients can discover descriptors by section and scope, read effective values, submit validated changes with optimistic concurrency, reset overrides, preview changes without committing, and review redacted audit records.

**Independent Test**: Exercise the settings API end to end by reading catalog sections, reading effective values with and without a key, applying a workspace or user update with expected versions, resetting an override, validating and previewing a proposed change, reading audit output, and verifying structured errors for invalid keys, invalid scopes, stale versions, read-only values, locked values, unresolved references, and permission failures.

**Acceptance Scenarios**:

1. **Given** settings descriptors exist across provider, user/workspace, and operations sections, **When** a client reads the catalog with section and scope filters, **Then** descriptors are returned grouped into the documented top-level sections and categories.
2. **Given** default, workspace, user, or reference-backed values exist, **When** a client reads effective settings with or without a specific key, **Then** resolved values include source explanations and respect scope.
3. **Given** a client submits a user or workspace update with changes, expected versions, and a reason, **When** the request is valid and authorized, **Then** the change is persisted, affected descriptors are refreshed, and an audit-visible change record is created without exposing sensitive values.
4. **Given** a stored override exists, **When** a client resets that key at the matching user or workspace scope, **Then** the override is removed and the inherited effective value is returned.
5. **Given** a client submits validation or preview input, **When** the system evaluates the proposed changes, **Then** validation results, effective-value diffs, dependency warnings, and reload requirements are returned without committing changes.
6. **Given** a write is stale, unauthorized, read-only, operator-locked, invalid, or references an unavailable provider profile or secret, **When** the request is processed, **Then** the response uses the structured settings error envelope with `error`, `message`, `key`, `scope`, and contextual `details`.
7. **Given** settings audit records include sensitive or security-relevant fields, **When** a client reads audit data by key or scope, **Then** the response redacts values according to descriptor audit policy.
8. **Given** downstream artifacts or delivery metadata are produced for this work, **When** traceability is reviewed, **Then** Jira issue key `MM-657` and this preserved preset brief remain available for comparison.

### Edge Cases

- Catalog filters reference an unknown section or invalid scope.
- Effective-value reads reference an unknown key or a setting not exposed by the catalog.
- Update payloads omit expected versions for keys that require concurrency protection.
- Expected versions are stale because another actor changed the setting first.
- A setting is exposed but not writable at the requested scope.
- A setting is read-only because of operator lock or operational safety policy.
- Proposed values fail type, enum, numeric, string, provider-profile, or SecretRef validation.
- Preview includes a valid change that has dependency warnings or reload requirements.
- Audit records exist for sensitive settings whose old and new values must be redacted.

## Assumptions

- Existing MoonMind authorization and actor context are used to determine whether a client may read sensitive metadata, write settings, or read audit data.
- Existing descriptor, effective-value, override, provider-profile, SecretRef, and audit semantics from the Settings System design remain authoritative; this story exposes them through the documented API surface rather than redefining them.
- Response payloads may reuse existing descriptor and effective-value shapes as long as they satisfy the documented grouping, source explanation, redaction, and error-envelope behavior.

## Source Design Requirements

- **DESIGN-REQ-001**: Section 12.1 requires catalog reads with section and scope filters that return descriptors grouped into sections and categories. Scope: in scope. Mapped to FR-001, FR-002.
- **DESIGN-REQ-002**: Section 12.2 requires effective settings reads for all values and a specific key, with scope support and source explanations. Scope: in scope. Mapped to FR-003, FR-004.
- **DESIGN-REQ-003**: Section 12.3 requires user and workspace update APIs accepting changes, expected versions, and a reason, and returning refreshed descriptors for affected settings. Scope: in scope. Mapped to FR-005, FR-006, FR-007.
- **DESIGN-REQ-004**: Section 12.4 requires user and workspace reset APIs that remove an override and return the inherited effective value. Scope: in scope. Mapped to FR-008.
- **DESIGN-REQ-005**: Section 12.5 requires validation and preview APIs that evaluate proposed changes without committing and return effective-value changes, dependency warnings, and reload requirements. Scope: in scope. Mapped to FR-009, FR-010.
- **DESIGN-REQ-006**: Section 12.6 requires audit reads by key or scope and redaction according to descriptor audit policy. Scope: in scope. Mapped to FR-011, FR-012.
- **DESIGN-REQ-007**: Section 12.7 requires structured settings API errors with `error`, `message`, `key`, `scope`, and contextual `details`. Scope: in scope. Mapped to FR-013.
- **DESIGN-REQ-008**: Section 20.3 requires authorization checks on every write and every sensitive metadata read. Scope: in scope. Mapped to FR-014.
- **DESIGN-REQ-009**: Section 22.5 requires sensitive data and audit output to avoid exposing raw managed secret plaintext or security-relevant values. Scope: in scope. Mapped to FR-012, FR-015.
- **DESIGN-REQ-010**: Section 22.9 requires restored or broken references to secrets, OAuth volumes, or provider profiles to surface clearly instead of failing silently. Scope: in scope. Mapped to FR-010, FR-013, FR-016.
- **DESIGN-REQ-011**: Section 25.14 requires version conflicts to be detected. Scope: in scope. Mapped to FR-006.
- **DESIGN-REQ-012**: Section 29.1 requires settings to be editable only when explicitly exposed by the backend catalog. Scope: in scope. Mapped to FR-002, FR-013, FR-014.
- **DESIGN-REQ-013**: Section 29.2 requires writes to be rejected for scopes not declared by the descriptor. Scope: in scope. Mapped to FR-005, FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose catalog reads that accept section and scope filters for the documented settings sections.
- **FR-002**: Catalog responses MUST group descriptors into the top-level sections `providers-secrets`, `user-workspace`, and `operations`, and MUST exclude settings that are not explicitly exposed by the backend catalog.
- **FR-003**: System MUST expose effective settings reads for all effective values at a scope and for one requested setting key at a scope.
- **FR-004**: Effective settings responses MUST include resolved values with source explanations and descriptor context sufficient for clients to understand inheritance and override state.
- **FR-005**: System MUST expose user-scope and workspace-scope update requests that accept setting changes, expected versions, and a human-readable reason.
- **FR-006**: Update handling MUST enforce optimistic concurrency with expected versions and return a `version_conflict` error when a submitted version is stale.
- **FR-007**: Successful update responses MUST return refreshed descriptors or effective values for affected settings and MUST produce audit-visible change metadata.
- **FR-008**: System MUST expose user-scope and workspace-scope reset requests that remove the requested override and return the inherited effective value.
- **FR-009**: System MUST expose validation requests that evaluate proposed changes without committing them.
- **FR-010**: Preview responses MUST report effective-value diffs, dependency warnings, broken references, and reload requirements without committing changes.
- **FR-011**: System MUST expose settings audit reads filtered by setting key or scope.
- **FR-012**: Audit responses MUST redact sensitive and security-relevant values according to descriptor audit policy.
- **FR-013**: Settings API failures MUST use a structured error envelope with stable error code, message, key, scope, and contextual details for unknown settings, non-exposed settings, invalid scopes, read-only settings, operator locks, invalid values, unresolved SecretRefs, missing provider profiles, stale versions, permission failures, and confirmation-required cases.
- **FR-014**: System MUST enforce authorization on every write request and every read of sensitive metadata or audit data.
- **FR-015**: No settings API response MUST reveal raw secret plaintext or durable secret material.
- **FR-016**: References to missing provider profiles, unresolved SecretRefs, missing OAuth volumes, or policy-blocked dependencies MUST surface as explicit diagnostics rather than silent fallback.
- **FR-017**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-657` and the original preset brief.

### Key Entities

- **Setting Descriptor**: A backend-owned description of one exposed setting, including key, title, section, category, scopes, type, validation, source, read-only state, reload metadata, and audit policy.
- **Effective Setting Value**: The resolved value for a setting at a scope, including source explanation, inheritance or override state, diagnostics, and descriptor context.
- **Setting Change Request**: A user or workspace update request containing requested changes, expected versions, and a reason.
- **Setting Preview Result**: A non-committed evaluation of proposed changes, including diffs, warnings, diagnostics, and reload requirements.
- **Setting Audit Record**: A redacted record of a settings read or write outcome sufficient to explain who changed what, where allowed, without exposing sensitive values.
- **Settings Error Envelope**: The stable error shape used by settings API failures, including error code, message, key, scope, and contextual details.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Contract tests cover all documented settings API families: catalog, effective reads (list and by key), update, reset, validate, preview, and audit.
- **SC-002**: At least one validation path confirms catalog responses include all three top-level sections: `providers-secrets`, `user-workspace`, and `operations`.
- **SC-003**: At least one validation path confirms stale update requests return `version_conflict` and do not commit changes.
- **SC-004**: At least one validation path confirms every documented settings error code maps to the structured envelope fields `error`, `message`, `key`, `scope`, and `details`.
- **SC-005**: At least one validation path confirms audit reads redact sensitive values while preserving non-sensitive audit metadata.
- **SC-006**: Traceability review confirms `MM-657`, the original Jira preset brief, and all in-scope source coverage IDs remain preserved in MoonSpec artifacts and final verification evidence.
