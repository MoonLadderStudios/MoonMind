# Feature Specification: Effective Value Resolver With Source Explanation and Operator Locks

**Feature Branch**: `340-effective-value-resolver`
**Created**: 2026-05-11
**Status**: Draft
**Input**:

```text
# MM-655 MoonSpec Orchestration Input

## Source

- Jira issue: MM-655
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Effective-value resolver with source explanation and operator locks
- Labels: `moonmind-workflow-mm-3997e9d9-e676-4b50-8e8d-e319fc13ef97`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-655 from MM project
Summary: Effective-value resolver with source explanation and operator locks
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 5.5 Explainability
- 7.5 Effective Value
- 7.6 Source
- 7.7 Lock
- 10 Resolution Model
- 16.4 Inheritance
- 27.1 Change Workspace Default Runtime
Coverage IDs:
- S5.5
- S7.5
- S7.6
- S7.7
- S10.1
- S10.2
- S10.3
- S10.5
- S16.4
- S26.SettingsResolver
- S27.1
- S29.8
- S29.10

Build the resolver that produces the effective value for a setting and the explanation of where it came from. Default resolution chain: `built-in default < config/env default < workspace override < user override`. Operator-locked resolution chain: `built-in default < workspace override < user override < operator lock`, with locks winning and forcing read-only state for non-operator editors. Each effective value carries a `source` label drawn from {`default`, `config_file`, `environment`, `workspace_override`, `user_override`, `provider_profile`, `secret_ref`, `operator_lock`}.

The resolver must distinguish missing/null/blocked states (no default, inherited null, intentionally null override, unresolvable SecretRef, missing provider profile, policy-blocked, post-migration invalid) and emit explicit diagnostics rather than silent fallback.

Acceptance Criteria
- Workspace overrides shadow defaults, user overrides shadow workspace, operator locks shadow user.
- Operator locks render the descriptor read-only with a populated `read_only_reason`.
- Diagnostic states from section 10.5 each produce a distinct, actionable explanation.
- The resolver answers every question in section 5.5: effective value, scope, inherited/overridden, locked, default, reload requirement, and dependent systems.

Requirements
- Implement effective-value resolution across built-in defaults, config/environment defaults, workspace overrides, user overrides, provider profile and SecretRef sources where applicable, and operator locks.
- Preserve operator-lock precedence and make locked values read-only for non-operator editors.
- Surface a stable source label for every resolved value using the documented source vocabulary.
- Return explicit diagnostics for missing, null, blocked, invalid, or unresolvable states without silently falling back.
- Include explainability metadata sufficient for operators and users to understand value source, scope, inheritance or override state, lock state, default value, reload requirement, and dependent systems.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-655 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
```

Preserved source Jira preset brief: `MM-655` from the trusted `jira.get_issue` response, reproduced verbatim in `**Input**` above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-655`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserving the `MM-655` implementation brief was found under `specs/`; `Specify` is the first incomplete stage.

## User Story - Explain Effective Settings Values

**Summary**: As a workspace administrator or settings user, I want each exposed setting to show its effective value, source, inheritance state, lock state, and actionable diagnostics so I can understand and safely change configuration without hidden fallback behavior.

**Goal**: MoonMind resolves effective settings values in the documented precedence order, explains the winning source and relevant metadata, prevents ordinary edits to operator-locked values, and reports missing, invalid, blocked, or unresolvable states distinctly.

**Independent Test**: Resolve representative settings across default, config or environment default, workspace override, user override, provider profile reference, SecretRef reference, and operator-lock cases, then verify the returned value, source label, inheritance or override status, read-only state, default, reload or restart metadata, dependent systems, and diagnostic explanation for every supported missing or blocked state.

**Acceptance Scenarios**:

1. **Given** a setting has only built-in or configured defaults, **When** a client reads the effective setting, **Then** the response returns the inherited value with the correct default, source label, scope, and no override state.
2. **Given** a workspace override exists, **When** the effective setting is read for workspace or user context without a user override, **Then** the workspace value wins over defaults and the explanation identifies the workspace source.
3. **Given** a user override exists, **When** the effective setting is read for that user, **Then** the user value wins over workspace and default values and the explanation identifies the user source.
4. **Given** an operator lock applies to a setting, **When** any non-operator editor reads the descriptor, **Then** the locked value wins, the descriptor is read-only, and the read-only reason explains the operator lock.
5. **Given** a setting value comes from a provider profile or SecretRef reference, **When** the effective setting is read, **Then** the source label identifies the reference source without exposing secret plaintext or embedding provider-profile semantics.
6. **Given** a setting has no default, inherited null, intentionally null override, unresolvable SecretRef, missing provider profile, policy-blocked value, or post-migration invalid value, **When** the effective setting is read, **Then** the system returns a distinct actionable diagnostic instead of silently falling back.
7. **Given** downstream artifacts or delivery metadata are produced for this work, **When** traceability is reviewed, **Then** Jira issue key `MM-655` and this preserved preset brief remain available for comparison.

### Edge Cases

- A workspace override and user override both exist; the user override wins for that user while the workspace override remains the inherited value for users without an override.
- An intentionally null override exists; the explanation distinguishes it from absence of a value.
- A SecretRef reference is syntactically valid but cannot be resolved at the relevant boundary; the diagnostic identifies the unresolved reference state without exposing plaintext.
- A provider profile reference points to a missing or disabled profile; the diagnostic identifies the missing dependency.
- An operator lock conflicts with a user or workspace override; the lock wins and ordinary editors see the setting as read-only.
- A setting requires reload or restart; the explanation reports the requirement and affected systems alongside the source.
- A migrated value is no longer valid; the diagnostic describes the invalid state instead of returning a misleading inherited value.

## Assumptions

- The selected story is limited to resolving and explaining effective values for settings that are already eligible to appear through the settings catalog.
- Existing authentication and authorization rules decide who may edit operator-locked or scope-restricted settings; this story requires observable read-only output for ordinary editors when a lock applies.
- SecretRef plaintext resolution remains owned by controlled execution and validation boundaries; the settings explanation exposes references and diagnostics only.
- Provider profile management remains owned by the Providers & Secrets surface; settings may reference profiles and report profile-reference diagnostics without replacing provider-profile behavior.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Security/SettingsSystem.md` §5.5 | Every effective setting value must explain value, supplying scope, inherited or overridden state, lock state, default, reload or restart requirement, and dependent systems. | In scope | FR-001, FR-004, FR-005, FR-008, FR-009, FR-012 |
| DESIGN-REQ-002 | `docs/Security/SettingsSystem.md` §7.5-§7.6 | Effective values must be resolved values with source labels from the documented source vocabulary. | In scope | FR-001, FR-002, FR-003, FR-004, FR-006 |
| DESIGN-REQ-003 | `docs/Security/SettingsSystem.md` §7.7 | Operator locks are enforced values that cannot be changed through normal UI flows. | In scope | FR-005, FR-010 |
| DESIGN-REQ-004 | `docs/Security/SettingsSystem.md` §10.1 | Ordinary settings must resolve in precedence order from built-in defaults through config/environment defaults, workspace overrides, and user overrides. | In scope | FR-002, FR-003, FR-004 |
| DESIGN-REQ-005 | `docs/Security/SettingsSystem.md` §10.2 | Operator-locked settings must resolve with operator locks winning over user overrides and making the field read-only for non-operator editors. | In scope | FR-005, FR-010 |
| DESIGN-REQ-006 | `docs/Security/SettingsSystem.md` §10.3 | Provider profiles are referenced by settings but remain separate resources whose semantics are not inlined into generic setting values. | In scope | FR-006, FR-011 |
| DESIGN-REQ-007 | `docs/Security/SettingsSystem.md` §10.4 | SecretRef settings resolve as references during settings resolution, with plaintext resolved only at controlled boundaries. | In scope | FR-006, FR-011 |
| DESIGN-REQ-008 | `docs/Security/SettingsSystem.md` §10.5 | Missing defaults, inherited nulls, intentionally null overrides, unresolvable SecretRefs, missing provider profiles, policy-blocked values, and invalid migrated values must produce explicit diagnostics instead of silent fallback. | In scope | FR-007, FR-013 |
| DESIGN-REQ-009 | `docs/Security/SettingsSystem.md` §16.4 | User settings inherit workspace defaults unless explicitly overridden and permitted by policy, and the UI must make inheritance visible. | In scope | FR-003, FR-004, FR-008 |
| DESIGN-REQ-010 | `docs/Security/SettingsSystem.md` §27.1 | Changing a workspace default refreshes descriptors so future reads and task creation use the new effective default and show workspace source. | In scope | FR-003, FR-008, FR-009 |
| DESIGN-REQ-011 | `docs/Security/SettingsSystem.md` §26 | The settings system must provide a durable capability for resolving effective values and explaining sources. | In scope | FR-001, FR-012 |
| DESIGN-REQ-012 | `docs/Security/SettingsSystem.md` §29, items 8 and 10 | Effective values must always explain their source, and operator locks cannot be overwritten by ordinary user or workspace writes. | In scope | FR-001, FR-005, FR-010, FR-012 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each effective setting read MUST return the resolved value and a stable source label for the winning source.
- **FR-002**: Built-in defaults MUST be the weakest source when no stronger configured, workspace, user, or operator source applies.
- **FR-003**: Config-file and environment defaults MUST override built-in defaults and remain weaker than workspace and user overrides for ordinary settings.
- **FR-004**: Workspace overrides MUST override defaults, and user overrides MUST override workspace values for the matching user context.
- **FR-005**: Operator locks MUST override user and workspace values and make the setting read-only for non-operator editors.
- **FR-006**: Source labels MUST use the documented vocabulary: `default`, `config_file`, `environment`, `workspace_override`, `user_override`, `provider_profile`, `secret_ref`, and `operator_lock`.
- **FR-007**: Effective setting reads MUST distinguish no default, inherited null, intentionally null override, unresolvable SecretRef, missing provider profile, policy-blocked value, and post-migration invalid value as separate diagnostic states.
- **FR-008**: Effective setting explanations MUST identify whether the value is inherited, overridden, intentionally null, or locked.
- **FR-009**: Effective setting explanations MUST include the default value when one exists, any reload or restart requirement, and affected dependent systems.
- **FR-010**: A locked setting descriptor returned to a non-operator editor MUST include a read-only state and populated read-only reason.
- **FR-011**: Provider profile and SecretRef sources MUST be represented as references and diagnostics only; the explanation MUST NOT expose secret plaintext or inline provider-profile internals.
- **FR-012**: Verification evidence for this feature MUST cover each supported source-precedence case, operator-lock read-only behavior, explainability metadata, and every distinct diagnostic state.
- **FR-013**: The system MUST fail visibly with an actionable diagnostic instead of silently falling back when the winning candidate cannot be used because it is missing, blocked, invalid, or unresolvable.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-655` and the original preset brief.

### Key Entities

- **Effective Setting Value**: The resolved value presented for one setting in a specific context, including source label, default, inheritance or override state, lock state, reload or restart metadata, dependent systems, and diagnostics.
- **Setting Source**: The origin of the winning candidate value, selected from the documented source vocabulary.
- **Operator Lock**: A deployment or policy-controlled value that wins over ordinary overrides and makes the descriptor read-only for non-operator editors.
- **Resolution Diagnostic**: A structured explanation for missing, null, blocked, invalid, or unresolvable setting states.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested built-in default, config-file default, environment default, workspace override, user override, provider-profile reference, SecretRef reference, and operator-lock cases return the expected source label and winning value.
- **SC-002**: 100% of tested operator-locked settings return read-only output with a non-empty read-only reason for non-operator editors.
- **SC-003**: 100% of tested no-default, inherited-null, intentionally-null, unresolvable-SecretRef, missing-provider-profile, policy-blocked, and post-migration-invalid cases return distinct diagnostics.
- **SC-004**: 100% of tested effective setting explanations include inheritance or override state, default visibility when applicable, reload or restart requirement, and dependent systems metadata.
- **SC-005**: 0 tested SecretRef or provider-profile explanations expose secret plaintext or inline provider-profile internals.
- **SC-006**: Traceability review confirms `MM-655`, the original preset brief, and all in-scope source design requirements remain preserved in MoonSpec artifacts and final verification evidence.
