# Feature Specification: Sparse Settings Override Persistence and Reset

**Feature Branch**: `339-sparse-settings-overrides`
**Created**: 2026-05-11
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-654 as the canonical Moon Spec orchestration input.

MM-654: Scoped overrides persistence with sparse storage and reset

Source Reference:
- Source Document: docs/Security/SettingsSystem.md
- Source Title: Settings System
- Source Sections: 5.4 Scoped Overrides, Not Mutable Defaults; 7.3 Scope; 7.4 Override; 11 Persistence Model; 27.3 Reset User Override
- Coverage IDs: S5.4, S7.3, S7.4, S11.1, S11.3, S11.4, S22.4, S22.10, S25.11, S26.SettingsOverrideStore, S27.3, S29.9

Persist user/workspace setting overrides in a `settings_overrides` table keyed by `(scope, workspace_id, user_id, key)` with `value_json`, `schema_version`, `value_version`, audit columns, and uniqueness. Defaults remain immutable; overrides are sparse and absence means "inherit." Reset deletes the override row and reverts the effective value to the inherited source without touching defaults, profiles, secrets, OAuth volumes, or audit history.

Persistence rules: rows may store booleans, numbers, strings, enums, lists, small structured JSON, SecretRef strings, and resource references. Rows must not store raw secrets, OAuth session blobs, decrypted credentials, generated config containing secrets, large artifacts, workflow payloads, or operational command history beyond audit metadata.

Acceptance:
- Workspace and user overrides are persisted independently and round-trip correctly.
- Reset endpoints delete only the relevant override row and return the inherited effective value.
- Size limits and schema validation are enforced before persistence; oversized or off-schema writes are rejected.
- Stored payloads never contain secret plaintext; a fixture verifies the payload contract.
- Concurrent writes are guarded by `value_version` for optimistic concurrency.

Preserve Jira issue key MM-654 in downstream spec artifacts, implementation notes, verification output, commit text, and pull request metadata."

Preserved source Jira preset brief: `MM-654` from the trusted `jira.get_issue` response, reproduced verbatim in `**Input**` above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-654`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserving the `MM-654` implementation brief was found under `specs/`; `Specify` is the first incomplete stage.

## User Story - Save, Read, and Reset Sparse Settings Overrides

**Summary**: As a workspace administrator or user, I want workspace and user settings overrides to be saved sparsely and reset independently so configuration can inherit predictably without changing defaults or adjacent credential resources.

**Goal**: MoonMind persists user and workspace setting overrides independently, resolves inherited effective values when overrides are absent or reset, rejects invalid or unsafe override values before saving, and prevents stale concurrent writes from partially changing settings.

**Independent Test**: Save a workspace override and a user override for eligible settings, verify each can be read back with the expected effective source and version metadata, reset each override, confirm the effective value inherits from the next source, and verify unsafe, oversized, off-schema, or stale-version writes are rejected without storing secret plaintext or partially changing other overrides.

**Acceptance Scenarios**:

1. **Given** no user or workspace override exists for an eligible setting, **When** a client reads the effective setting value, **Then** the response shows the inherited value and identifies the inherited source rather than creating a stored override.
2. **Given** a workspace override is saved for an eligible setting, **When** workspace or task resolution reads that setting, **Then** the workspace value is returned with version metadata and without mutating the default value.
3. **Given** a user override is saved for an eligible user setting, **When** user resolution reads that setting, **Then** the user value is returned independently from the workspace override while preserving the workspace value for inheritance when no user override exists.
4. **Given** a user or workspace override exists, **When** the matching reset action is requested, **Then** only that override is removed and the response returns the inherited effective value.
5. **Given** a write contains an oversized, off-schema, raw secret, OAuth session, decrypted credential, generated credential config, large artifact, workflow payload, or operational history value, **When** validation runs, **Then** the write is rejected before persistence and no unsafe value is stored.
6. **Given** a write is submitted with a stale version expectation, **When** the system compares it with the current override version, **Then** the write is rejected as a version conflict and no partial setting change is persisted.
7. **Given** downstream artifacts or delivery metadata are produced for this work, **When** traceability is reviewed, **Then** Jira issue key `MM-654` and this preserved preset brief remain available for comparison.

### Edge Cases

- An override value is intentionally null; the system distinguishes that stored override from no override existing.
- A user override is reset while a workspace override still exists; the effective value inherits from the workspace source.
- A workspace override is reset while a user override still exists; the user override remains intact and can still be read in user scope.
- A multi-setting write contains one invalid or stale entry; none of the requested entries are partially persisted.
- A SecretRef or resource reference is stored as a reference only and is never resolved into plaintext as part of override storage.
- Reset is requested for an unknown, ineligible, or already absent override; the system returns a structured outcome without deleting unrelated settings or resources.

## Assumptions

- The selected story is limited to user and workspace settings overrides; system and operator scopes remain outside normal user-editable behavior for this feature.
- Existing authentication and authorization rules decide who may read, write, or reset settings; this story validates persistence, inheritance, reset, safety, and concurrency behavior after an operation is permitted.
- Adjacent resources include defaults, provider profiles, managed secrets, OAuth credential volumes, and settings audit history, and they must survive override resets.
- The source design remains authoritative when choosing between ambiguous behavior and an implementation detail in the Jira preset brief.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Security/SettingsSystem.md` §5.4 | User and workspace changes are stored as overrides; defaults remain immutable and reset removes only the override so the value inherits again. | In scope | FR-001, FR-004, FR-005, FR-009 |
| DESIGN-REQ-002 | `docs/Security/SettingsSystem.md` §7.3 | Supported settings scopes include user, workspace, system, and operator, with user and workspace as the normal editable scopes for most users. | In scope | FR-002, FR-003 |
| DESIGN-REQ-003 | `docs/Security/SettingsSystem.md` §7.4 | Overrides are sparse persisted scoped values; absence of an override means inherit. | In scope | FR-001, FR-002, FR-003, FR-005 |
| DESIGN-REQ-004 | `docs/Security/SettingsSystem.md` §7.5-§7.6 | Effective values must explain which source supplied the value and whether it was inherited or overridden. | In scope | FR-006 |
| DESIGN-REQ-005 | `docs/Security/SettingsSystem.md` §11.1 | Override storage must keep one value per scope, subject, and setting key with version and audit metadata. | In scope | FR-002, FR-003, FR-007, FR-010 |
| DESIGN-REQ-006 | `docs/Security/SettingsSystem.md` §11.3 | Override values may include allowed primitive, list, small structured, SecretRef, and resource-reference values, but must not include raw secrets, OAuth state, credential files, generated credential config, large artifacts, workflow payloads, or operational command history beyond audit metadata. | In scope | FR-008, FR-011 |
| DESIGN-REQ-007 | `docs/Security/SettingsSystem.md` §11.4 | Resetting a setting deletes the relevant override and does not delete inherited defaults, provider profiles, managed secrets, OAuth volumes, or audit history. | In scope | FR-005, FR-009 |
| DESIGN-REQ-008 | `docs/Security/SettingsSystem.md` §25, item 11 | Validation must prove reset removes overrides and restores inherited values. | In scope | FR-005, FR-012 |
| DESIGN-REQ-009 | `docs/Security/SettingsSystem.md` §26 | The system must provide a durable capability for persisting and retrieving scoped overrides. | In scope | FR-002, FR-003, FR-005 |
| DESIGN-REQ-010 | `docs/Security/SettingsSystem.md` §27.3 | Resetting a user override returns the inherited workspace or default value and updates the visible source accordingly. | In scope | FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat absence of a user or workspace override as inheritance from the next applicable configured source, without creating a stored override.
- **FR-002**: The system MUST persist workspace override values independently by workspace and setting key for eligible workspace-scoped settings.
- **FR-003**: The system MUST persist user override values independently by user, workspace context when applicable, and setting key for eligible user-scoped settings.
- **FR-004**: The system MUST keep built-in or configured default values immutable when user or workspace overrides are saved or reset.
- **FR-005**: Resetting a user or workspace override MUST delete only the matching override and return the inherited effective value for the requested setting.
- **FR-006**: Effective setting reads MUST report whether the value came from inheritance, a workspace override, a user override, or an intentional null override.
- **FR-007**: Override writes MUST maintain version metadata that changes when the stored override changes.
- **FR-008**: The system MUST validate override size and schema before persistence and reject oversized or off-schema writes.
- **FR-009**: Reset behavior MUST NOT delete inherited defaults, provider profiles, managed secrets, OAuth credential volumes, or settings audit history.
- **FR-010**: Concurrent writes MUST be guarded by expected version checks, and stale writes MUST fail without partial persistence.
- **FR-011**: Stored override values MUST NOT contain raw secret plaintext, OAuth session blobs, decrypted credentials, generated credential config containing secrets, large artifacts, workflow payloads, or operational command history beyond audit metadata.
- **FR-012**: Verification evidence for this feature MUST include checks that workspace and user overrides round-trip independently, reset restores inherited values, validation rejects unsafe values, and stale writes fail without partial persistence.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-654` and the original preset brief.

### Key Entities

- **Setting Override**: A sparse scoped value for one setting key at user or workspace scope, including subject identity, stored value, version metadata, and audit metadata.
- **Effective Setting Value**: The resolved setting value presented to a client, including its source and whether it is inherited, overridden, or intentionally null.
- **Settings Validation Result**: The structured outcome produced before saving an override, identifying accepted values or the reason a value is unsafe, oversized, off-schema, or stale.
- **Reset Outcome**: The structured result of removing an override, including the inherited effective value and source after reset.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested workspace override writes can be read back independently with the expected source and version metadata.
- **SC-002**: 100% of tested user override writes can be read back independently from workspace overrides, and user reset restores the inherited workspace or default value.
- **SC-003**: 100% of tested reset operations delete only the targeted override and leave defaults, provider profiles, managed secrets, OAuth credential volumes, and audit history intact.
- **SC-004**: 100% of oversized, off-schema, raw-secret, OAuth-session, credential-config, large-artifact, workflow-payload, and disallowed operational-history fixture values are rejected before persistence.
- **SC-005**: 100% of stale expected-version write attempts return a version-conflict outcome and persist zero partial setting changes.
- **SC-006**: Traceability review confirms `MM-654`, the original preset brief, and all in-scope source design requirements remain preserved in MoonSpec artifacts and final verification evidence.
