# Feature Specification: Scoped Override Persistence and Inheritance

**Feature Branch**: `268-scoped-override-persistence`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-538 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-538` from the trusted `jira.get_issue` response, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-538`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-538` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
MM-538: Scoped override persistence and inheritance

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 3. Goals
- 4. Scoped Overrides
- 7.3 Scope
- 7.4 Override
- 7.5 Effective Value
- 11. Persistence Model
- 16. User / Workspace Settings
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-017
- DESIGN-REQ-026

As a workspace admin or user, I can save, inspect, and reset scoped settings overrides so user and workspace configuration inherits predictably without mutating built-in defaults.

Acceptance Criteria
- Given no override, the effective value inherits from the default or higher configured source.
- Given a workspace override, task/workspace resolution reflects the workspace value and records version metadata.
- Given an allowed user override, user resolution wins over workspace inheritance while preserving workspace policy constraints.
- Given reset of a user or workspace override, the override row is deleted and defaults, provider profiles, managed secrets, OAuth volumes, and audit history are not deleted.
- Given conflicting expected versions, update returns `version_conflict` and does not persist a partial change.

Requirements
- Override storage must enforce unique scope/workspace/user/key rows.
- Override rows may store only allowed JSON values, SecretRefs, and resource references.
- Override rows must never store raw secrets, OAuth state blobs, decrypted files, generated credential config, large artifacts, workflow payloads, or operational history beyond audit metadata.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-538 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Security/SettingsSystem.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`.
- Resume decision: No existing Moon Spec artifacts for `MM-538` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-538` because the trusted Jira preset brief defines one independently testable story.

## User Story - Save, Inspect, and Reset Scoped Overrides

**Summary**: As a workspace admin or user, I want scoped settings overrides to persist separately from defaults so workspace and user configuration inherits predictably and can be reset without deleting adjacent resources.

**Goal**: MoonMind allows authorized settings clients to save workspace and user overrides for eligible settings, read effective values that explain override inheritance, reset overrides by deleting only the override row, and reject conflicting writes without partial persistence.

**Independent Test**: Through the settings API, read an effective workspace value with no override, save a workspace override with the expected version, verify workspace effective resolution and version metadata, save a permitted user override that wins for user scope while workspace remains available for inheritance, reset each override, and verify defaults plus provider profiles, managed secrets, OAuth volumes, and audit history are not deleted.

**Acceptance Scenarios**:

1. **Given** no workspace or user override exists, **When** a settings client reads an effective value, **Then** the value inherits from the default or higher configured source and reports the inherited source.
2. **Given** a workspace override is saved for an eligible setting, **When** task or workspace resolution reads that setting, **Then** the workspace value wins, the response reports `workspace_override`, and version metadata is incremented.
3. **Given** a user override is saved for an eligible user-scoped setting, **When** user resolution reads that setting, **Then** the user value wins over workspace inheritance while preserving workspace policy constraints.
4. **Given** a user or workspace override is reset, **When** the effective value is read again, **Then** only the override row is deleted and inherited defaults, provider profiles, managed secrets, OAuth volumes, and audit history remain intact.
5. **Given** a write includes an expected version that does not match the current override version, **When** the settings API processes the update, **Then** it returns `version_conflict` and persists none of the requested changes.
6. **Given** a proposed override contains a raw secret, OAuth state blob, credential file content, generated credential config, large artifact, workflow payload, or operational history, **When** validation runs, **Then** the update is rejected and no unsafe value is stored.
7. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-538` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- A workspace override exists but a user override does not; user scope must inherit the workspace value where the setting allows user scope.
- A user override is intentionally set to `null`; the system must distinguish intentional override value from absence of an override.
- A multi-key update contains one invalid value or version conflict; no override in the batch may be partially persisted.
- A SecretRef override stores only the reference string and never resolves or stores plaintext.
- Resetting an override for an unknown or disallowed key must return a structured settings error rather than deleting unrelated rows.

## Assumptions

- Authentication and fine-grained authorization remain outside this isolated story; disabled-mode API tests exercise the trusted backend validation and persistence behavior.
- Workspace and user IDs may be nullable in the initial local baseline, but uniqueness must still enforce one row per scope, key, and effective subject.
- Existing provider profiles, managed secrets, OAuth rows, and audit rows are adjacent resources that must not be cascaded or manually deleted by settings reset behavior.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | `docs/Security/SettingsSystem.md` §3, §7.3-§7.5, §10.1-§10.2, §16.4 | User and workspace settings must resolve through predictable inheritance where defaults and configured values are weaker than workspace overrides, and user overrides win only when allowed by policy. | In scope | FR-001, FR-002, FR-003, FR-005 |
| DESIGN-REQ-017 | `docs/Security/SettingsSystem.md` §11.1-§11.4 | Scoped override persistence must store sparse user/workspace rows with unique scope/subject/key identity, version metadata, reset-by-delete semantics, and preservation of defaults, provider profiles, managed secrets, OAuth volumes, and audit history. | In scope | FR-002, FR-004, FR-006, FR-007, FR-009 |
| DESIGN-REQ-026 | `docs/Security/SettingsSystem.md` §11.3, §14, §15, §16.3 | Override rows may store only allowed JSON values, SecretRefs, and resource references; they must not store raw secrets, OAuth state, decrypted files, generated credential config, large artifacts, workflow payloads, or operational history beyond audit metadata. | In scope | FR-008, FR-010 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST resolve effective settings with this precedence for ordinary user/workspace settings: built-in or configured default, workspace override, then allowed user override.
- **FR-002**: The system MUST persist workspace overrides for eligible workspace-scoped settings and report the persisted value as `workspace_override` with current version metadata.
- **FR-003**: The system MUST persist user overrides only for settings that allow user scope, and user resolution MUST preserve workspace policy constraints while allowing the user override to win when permitted.
- **FR-004**: Resetting a user or workspace override MUST delete only the matching override row and return the inherited effective value.
- **FR-005**: Absence of an override MUST be distinguishable from an intentionally persisted null override.
- **FR-006**: Override storage MUST enforce unique rows by scope, workspace, user, and setting key.
- **FR-007**: Override writes MUST increment value version metadata and reject stale expected versions with `version_conflict`.
- **FR-008**: Override validation MUST reject raw secrets, OAuth state blobs, decrypted credential files, generated credential config, large artifacts, workflow payloads, and operational history beyond audit metadata.
- **FR-009**: Reset behavior MUST NOT delete inherited defaults, provider profiles, managed secrets, OAuth credential volumes, or settings audit history.
- **FR-010**: SecretRef and resource-reference overrides MUST persist only reference values and MUST never resolve or store plaintext secret material.
- **FR-011**: Settings write and reset failures MUST use the structured settings error shape with stable error code, message, key, scope, and details.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-538`.

### Key Entities

- **Setting Override**: A sparse persisted value for one setting key at a user or workspace scope, including subject identity, JSON value, schema version, value version, and audit metadata.
- **Effective Setting Value**: The resolved value for a setting at a requested scope, including whether it came from inherited configuration, workspace override, user override, or an intentional null override.
- **Settings Audit Event**: A durable settings change record that survives override resets and stores old/new values according to the descriptor audit policy.
- **Settings Error**: The structured error response used when an override write, reset, or read cannot be fulfilled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Service tests prove no-override resolution inherits configured/default values and workspace overrides become effective with `workspace_override` source and incremented version.
- **SC-002**: Service or API tests prove user overrides win only for user-scoped settings and workspace inheritance remains visible when no user override exists.
- **SC-003**: API tests prove reset deletes only the override row, returns inherited effective values, and leaves managed secrets plus audit rows intact.
- **SC-004**: Tests prove stale expected versions return `version_conflict` and no batch changes are partially persisted.
- **SC-005**: Tests prove unsafe raw secret/OAuth/artifact/workflow payload values are rejected while SecretRef reference values can be persisted without plaintext resolution.
- **SC-006**: Traceability review confirms `MM-538` and DESIGN-REQ-006, DESIGN-REQ-017, and DESIGN-REQ-026 remain preserved in MoonSpec artifacts and downstream evidence.
