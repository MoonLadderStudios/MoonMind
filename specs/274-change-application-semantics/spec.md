# Feature Specification: Change Application, Reload, Restart, and Recovery Semantics

**Feature Branch**: `274-change-application-semantics`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-544 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-544` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-544`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-544` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-544 MoonSpec Orchestration Input

## Source

- Jira issue: MM-544
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Change application, reload, restart, and recovery semantics
- Trusted fetch tool: jira.get_issue
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose recommended preset instructions or a normalized preset brief.

## Canonical MoonSpec Feature Request

Jira issue: MM-544 from MM project
Summary: Change application, reload, restart, and recovery semantics
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-544 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-544: Change application, reload, restart, and recovery semantics

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 18. Validation Model
- 19. Change Application Semantics
- 23. Backup and Recovery
- 27. Example End-to-End Flows

Coverage IDs:
- DESIGN-REQ-016
- DESIGN-REQ-019
- DESIGN-REQ-025

As an operator, I can understand when settings take effect and recover from backup/restore reference gaps so runtime behavior changes are visible, durable, and safe.

Acceptance Criteria
- Each setting declares how changes apply and descriptors expose reload, worker restart, process restart, and affected subsystem metadata.
- Committed changes emit structured events containing event type, key, scope, source, apply mode, actor, and timestamp.
- Consumers can refresh catalog state, use updated task defaults, sync profile-related changes, reload non-disruptive worker settings, and update operational status where applicable.
- When restart is required, the UI shows current effective value, pending value if applicable, affected process or worker, whether active, and how to complete activation.
- After restore without corresponding secrets, OAuth volumes, or provider profiles, Settings surfaces broken references clearly while backups exclude raw managed secret plaintext.

Requirements
- Validation must occur at descriptor generation, write receipt, before persistence, after persistence during preview, before launch or operation execution, and during readiness diagnostics where applicable.
- Backups may contain setting keys, non-sensitive values, SecretRefs, resource references, audit records, and metadata only.
- Runtime application behavior must be observable rather than implicit.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Security/SettingsSystem.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`.
- Resume decision: No existing Moon Spec artifacts for `MM-544` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-544` because the trusted Jira preset brief defines one independently testable story.

## User Story - Understand and Apply Setting Changes Safely

**Summary**: As an operator, I want settings to declare how changes take effect and expose recovery gaps so runtime behavior changes are visible, durable, and safe.

**Goal**: MoonMind makes each settings change explain when it becomes active, publishes durable change evidence for affected consumers, shows restart or reload work that remains pending, and surfaces broken restored references without exposing raw secret plaintext.

**Independent Test**: Exercise the settings catalog, write or preview flows, runtime consumer refresh behavior, restart-required visibility, and backup/restore reference diagnostics to confirm that apply modes, structured change events, consumer-visible refresh outcomes, pending activation state, and broken reference reporting all remain traceable to `MM-544`.

**Acceptance Scenarios**:

1. **Given** a settings catalog descriptor is generated, **When** a client reads an eligible setting, **Then** the descriptor declares the setting's apply mode and exposes reload, worker restart, process restart, and affected subsystem metadata where applicable.
2. **Given** a settings write is accepted and committed, **When** the change is recorded, **Then** a structured change event includes event type, key, scope, source, apply mode, actor, and timestamp.
3. **Given** consumers depend on changed settings, **When** a committed change event is available, **Then** catalog views can refresh, task defaults can use updated values, profile-related changes can sync, non-disruptive worker settings can reload, and operational status can update where applicable.
4. **Given** a setting requires restart before activation, **When** an operator views the setting status, **Then** MoonMind shows the current effective value, pending value when applicable, affected process or worker, whether the value is already active, and how activation can be completed.
5. **Given** settings are restored without corresponding secrets, OAuth volumes, or provider profiles, **When** the Settings surface evaluates the restored references, **Then** broken references are clearly visible and backups exclude raw managed secret plaintext.
6. **Given** validation is required for a settings path, **When** descriptors are generated, writes are received, persistence is attempted, previews run, launches or operations execute, or readiness diagnostics run, **Then** validation occurs at that boundary and produces observable results.
7. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-544` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- A setting can apply immediately but also affects a subsystem that caches catalog metadata; the immediate update must not hide the need for a catalog or consumer refresh.
- A setting requires process restart but has no pending value because the current persisted value is already active; activation state must distinguish active from pending.
- A committed change event reaches a consumer that cannot reload the changed setting; MoonMind must expose the remaining activation requirement instead of implying success.
- A restored SecretRef, OAuth volume reference, or provider profile reference points to a missing resource; diagnostics must identify the broken reference without resolving or storing plaintext.
- Validation succeeds before persistence but fails later during readiness diagnostics because a referenced resource was removed; the later failure must remain visible to operators.

## Assumptions

- This story builds on existing settings catalog, override, audit, provider profile, managed secret, and operations surfaces rather than introducing a separate settings product area.
- The Settings UI and APIs already have a concept of descriptors and effective values; this story makes activation and recovery semantics explicit and observable.
- Backup creation and restore execution may remain outside the story, but the Settings surfaces must correctly represent restored references and exclude raw managed secret plaintext from backup content.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-016 | `docs/Security/SettingsSystem.md` §18.1-§18.3 | Settings validation must run at descriptor generation, write receipt, before persistence, after persistence during preview, before launch or operation execution, and during readiness diagnostics. | In scope | FR-001, FR-002, FR-012 |
| DESIGN-REQ-019 | `docs/Security/SettingsSystem.md` §19.1-§19.4, §27.1, §27.4 | Settings must declare apply modes, emit structured change events, allow appropriate consumers to refresh or reload, and show restart requirements and activation status when restart is needed. | In scope | FR-003, FR-004, FR-005, FR-006, FR-007, FR-008 |
| DESIGN-REQ-025 | `docs/Security/SettingsSystem.md` §23, §27.2 | Settings backups may include non-sensitive values, SecretRefs, resource references, audit records, and metadata, must exclude raw managed secret plaintext, and restored broken references must be surfaced clearly. | In scope | FR-009, FR-010, FR-011 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST validate settings descriptors when the catalog is generated so invalid keys, scopes, exposure rules, metadata, and dependency declarations are detected before clients rely on them.
- **FR-002**: The system MUST validate settings writes at receipt and before persistence for key existence, exposure, allowed scope, actor authorization, value shape, enum or numeric constraints, SecretRef syntax, referenced resources, dependency rules, and workspace policy.
- **FR-003**: Each editable setting descriptor MUST declare one apply mode from the supported desired-state set and expose reload, worker restart, process restart, and affected subsystem metadata where applicable.
- **FR-004**: A committed settings change MUST produce a structured change event containing event type, setting key, scope, source, apply mode, actor, and timestamp.
- **FR-005**: Settings consumers MUST be able to observe committed changes so catalog state, task defaults, profile-related state, non-disruptive worker settings, and operational status can refresh or reload where applicable.
- **FR-006**: For settings that require restart before activation, operator-visible status MUST show the current effective value, pending value when applicable, affected process or worker, restart requirement, activation state, and completion guidance.
- **FR-007**: The system MUST distinguish between changes that are already active, changes that will apply on the next request, task, or launch, and changes that remain pending until worker reload, process restart, or manual operation.
- **FR-008**: Runtime application behavior for settings MUST be observable through descriptors, events, effective state, diagnostics, or operational status rather than implicit in code paths.
- **FR-009**: Settings backup content MUST be limited to setting keys, non-sensitive values, SecretRefs, resource references, audit records, and metadata.
- **FR-010**: Settings backup and recovery behavior MUST NOT expose or persist raw managed secret plaintext in generic settings backup data.
- **FR-011**: After restore without matching secrets, OAuth volumes, or provider profiles, Settings surfaces MUST clearly identify broken restored references without resolving or revealing secret plaintext.
- **FR-012**: Validation MUST run during post-persistence preview, launch or operation execution, and readiness diagnostics where applicable so late dependency failures remain visible.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-544`.

### Key Entities *(include if feature involves data)*

- **Setting Descriptor**: Backend-owned metadata for one editable setting, including key, scope, validation constraints, apply mode, affected subsystem metadata, and reload or restart requirements.
- **Settings Change Event**: Durable notification that a setting changed, including event type, key, scope, source, apply mode, actor, timestamp, and enough metadata for consumers to decide whether to refresh, reload, or surface pending activation.
- **Effective Setting State**: Operator-visible resolved setting state that distinguishes current effective value, pending value, source, activation status, affected process or worker, and completion guidance.
- **Restored Reference Diagnostic**: A settings-facing diagnostic for a SecretRef, OAuth volume, provider profile, or resource reference that was restored but cannot currently be resolved.
- **Settings Backup Record**: Portable backup data containing non-sensitive settings values, references, audit records, and metadata, excluding raw managed secret plaintext.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every editable setting descriptor in scope reports an apply mode and accurately indicates reload, worker restart, process restart, and affected subsystem metadata when those conditions apply.
- **SC-002**: Every committed settings change in scope emits a structured event with event type, key, scope, source, apply mode, actor, and timestamp.
- **SC-003**: Runtime consumers covered by the story can demonstrate either refreshed state, reload completion, or explicit pending activation status after a relevant settings change.
- **SC-004**: Restart-required settings display current value, pending value when present, affected process or worker, active state, and activation guidance to operators.
- **SC-005**: Backup and restore diagnostics demonstrate that raw managed secret plaintext is absent from settings backup data and missing restored references are clearly surfaced.
- **SC-006**: Validation evidence covers descriptor generation, write receipt, pre-persistence, post-persistence preview, launch or operation execution, and readiness diagnostics where applicable.
- **SC-007**: `MM-544` appears in generated MoonSpec artifacts and downstream implementation evidence.
