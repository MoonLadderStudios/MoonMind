# Feature Specification: Codex Auth Volume Profile Contract

**Feature Branch**: `189-codex-auth-profile`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**: MM-355: Codex Auth Volume Profile Contract

## Original Jira Preset Brief

```text
# MM-355 MoonSpec Orchestration Input

## Source

- Jira issue: MM-355
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Codex Auth Volume Profile Contract
- Labels: `mm-318`, `moonspec-breakdown`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-355 from MM project
Summary: Codex Auth Volume Profile Contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-355 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-355: Codex Auth Volume Profile Contract

Story ID: STORY-001

Short Name
codex-auth-profile

User Story
As an operator, I can register or update a Codex OAuth Provider Profile that points to durable credential-volume metadata without exposing credential contents or treating the auth volume as task execution state.

Acceptance Criteria
- Given verified Codex OAuth session data, when the profile registrar runs, then a Provider Profile exists with runtime_id = codex_cli, credential_source = oauth_volume, runtime_materialization_mode = oauth_home, volume_ref, volume_mount_path, and slot policy.
- Given profile data is returned through an API or workflow snapshot, then raw token values and auth file contents are absent.
- Given a non-Codex runtime profile is processed, then this story does not imply task-scoped managed-session parity.

Requirements
- Define and enforce the Codex OAuth Provider Profile shape for credential_source = oauth_volume and runtime_materialization_mode = oauth_home.
- Preserve volume_ref, volume_mount_path, and slot policy fields during profile registration or update.
- Keep raw credential file contents out of API responses, workflow payloads, logs, and artifacts.
- Keep Claude and Gemini task-scoped managed-session parity out of scope.

Independent Test
Create or update a Codex OAuth profile from verified OAuth session data, then assert the stored/profile API representation contains only refs and policy metadata, not credential contents.

Dependencies
- None specified.

Risks
- The exact API response redaction boundary should be covered by router tests and provider-profile service tests.

Out of Scope
- Interactive OAuth terminal UI.
- Managed-session container launch.
- Claude/Gemini managed-session parity.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 1. Purpose
- 2. Scope
- 3.1 Durable auth volume
- 6. Provider Profile Registration
- 9. Security Model
- 11. Required Boundaries

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-010
- DESIGN-REQ-016
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-001: Provide a first-party way to enroll OAuth credentials and target resulting credential volumes into managed runtime containers.
- DESIGN-REQ-002: Codex is the only fully updated task-scoped managed-session target for this contract; Claude/Gemini parity is out of scope.
- DESIGN-REQ-003: Treat codex_auth_volume as durable provider-profile credential storage, configurable by CODEX_VOLUME_NAME, with enrollment path /home/app/.codex.
- DESIGN-REQ-010: Never place raw credential contents in workflow history, logs, artifacts, or UI responses.
- DESIGN-REQ-016: Register or update Provider Profiles after OAuth verification, preserving Codex OAuth fields and slot policy.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Needs Clarification
- None
```

## User Story - Codex OAuth Profile Contract

**Summary**: Operators can register or update a Codex OAuth Provider Profile that points to durable credential-volume metadata without exposing credential contents or treating the auth volume as task execution state.

**Goal**: Allow verified Codex OAuth credentials to become durable, selectable Provider Profile metadata that later managed-session flows can consume through refs and policy fields only.

**Independent Test**: Create or update a Codex OAuth profile from verified OAuth session data, then confirm stored profile data, operator-visible profile data, and workflow-visible profile snapshots include only runtime identity, provider identity, auth-volume refs, materialization mode, mount path, and slot policy metadata while excluding raw credential contents, token values, auth file payloads, and unrelated runtime-home state.

**Acceptance Scenarios**:

1. **Given** verified Codex OAuth session data, **when** the profile registrar runs, **then** a Provider Profile exists with `runtime_id = codex_cli`, `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `volume_ref`, `volume_mount_path`, and slot policy metadata.
2. **Given** profile data is returned through an operator-facing read path or workflow snapshot, **when** the profile is serialized, **then** raw token values, auth file contents, auth file payloads, environment dumps, and raw auth-volume listings are absent.
3. **Given** Codex OAuth profile registration receives missing or blank durable auth-volume refs, **when** the profile is validated, **then** the profile is rejected before it can become selectable for managed sessions.
4. **Given** an existing Codex OAuth Provider Profile is repaired or updated, **when** the updated profile is saved, **then** durable auth-volume refs, materialization mode, mount path, provider identity, and slot policy remain explicit and credential contents remain outside profile state.
5. **Given** a non-Codex runtime profile is processed, **when** this story's behavior is evaluated, **then** it does not imply Claude or Gemini task-scoped managed-session parity and does not require those runtimes to adopt the Codex OAuth profile shape.

### Edge Cases

- OAuth verification succeeds but profile registration receives missing, blank, or whitespace-only `volume_ref`.
- OAuth verification succeeds but profile registration receives missing, blank, or whitespace-only `volume_mount_path`.
- Profile serialization includes nested provider metadata that could accidentally expose credential-like values.
- Existing profiles are repaired or updated instead of created from scratch.
- A non-Codex profile uses OAuth-style credential source metadata but is outside the current Codex task-scoped managed-session target scope.
- Operator-visible failure details are needed after validation rejects unsafe profile metadata.

## Assumptions

- OAuth verification produces secret-free evidence that the durable auth volume is ready before profile registration or update is attempted.
- Codex is the only task-scoped managed-session target covered by this story.
- Later managed-session launch stories will consume the registered profile but are not implemented by this story.

## Source Design Requirements

- **DESIGN-REQ-001**: MoonMind provides a first-party way to enroll OAuth credentials and target resulting credential volumes into managed runtime containers. Source: `docs/ManagedAgents/OAuthTerminal.md` sections 1 and 2. Scope: in scope for Provider Profile registration and profile metadata readiness. Maps to FR-001 and FR-002.
- **DESIGN-REQ-002**: Codex is the only fully updated task-scoped managed-session target for this contract; Claude and Gemini parity are out of scope. Source: sections 1 and 2. Scope: in scope as a boundary. Maps to FR-008.
- **DESIGN-REQ-003**: The durable Codex OAuth home is a provider-profile credential backing store with configurable volume name, conventional enrollment path, `volume_ref`, and `oauth_home` materialization mode. Source: section 3.1. Scope: in scope. Maps to FR-001, FR-002, FR-003, and FR-004.
- **DESIGN-REQ-010**: Workflow payloads, artifacts, logs, UI responses, and browser-visible profile data must not expose raw credential contents, token values, environment dumps, or raw auth-volume listings. Source: sections 4, 8, and 9. Scope: in scope. Maps to FR-005 and FR-006.
- **DESIGN-REQ-016**: After OAuth verification succeeds, MoonMind registers or updates a Provider Profile that preserves Codex runtime identity, provider identity, OAuth credential source, materialization mode, volume ref, mount path, and slot policy. Source: section 6. Scope: in scope. Maps to FR-001, FR-002, FR-003, and FR-007.
- **DESIGN-REQ-020**: Ownership boundaries between OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration remain explicit. Source: section 11. Scope: in scope for Provider Profile ownership and out of scope for managed-session launch and workload orchestration behavior. Maps to FR-008 and FR-009.
- **DESIGN-REQ-004**: Shared task workspace volume behavior. Scope: out of scope; this story only defines Provider Profile metadata and redaction behavior.
- **DESIGN-REQ-005**: Per-run Codex home materialization under the workspace volume. Scope: out of scope; managed-session launch and home seeding are later stories.
- **DESIGN-REQ-006**: Explicit auth-volume target for managed Codex session launch. Scope: out of scope except that this story preserves the profile refs needed by later launch behavior.
- **DESIGN-REQ-007**: One-way auth seeding into per-run Codex home. Scope: out of scope; this story does not launch managed sessions.
- **DESIGN-REQ-011**: First-party OAuth terminal architecture and browser terminal flow. Scope: out of scope; this story starts after OAuth verification succeeds.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define Codex OAuth Provider Profiles with `runtime_id = codex_cli`, a concrete Codex-supported `provider_id`, `credential_source = oauth_volume`, and `runtime_materialization_mode = oauth_home`.
- **FR-002**: The system MUST preserve `volume_ref` and `volume_mount_path` when registering, updating, repairing, serializing, or snapshotting a verified Codex OAuth Provider Profile.
- **FR-003**: The system MUST preserve Provider Profile slot policy metadata, including maximum parallel run policy, cooldown policy, and lease-duration policy when those values are present.
- **FR-004**: The system MUST reject Codex OAuth Provider Profiles when required durable auth-volume refs or mount paths are missing, blank, or otherwise unsafe to select for a managed runtime.
- **FR-005**: The system MUST keep raw credential file contents, token values, auth file payloads, raw auth-volume listings, and environment dumps out of operator-facing profile responses.
- **FR-006**: The system MUST keep raw credential file contents, token values, auth file payloads, raw auth-volume listings, and environment dumps out of workflow payloads, logs, artifacts, and profile snapshots.
- **FR-007**: The system MUST register a new Provider Profile or update an existing one from verified Codex OAuth session evidence without inventing a separate durable auth store.
- **FR-008**: The system MUST keep Claude and Gemini task-scoped managed-session parity out of scope while allowing non-Codex profiles to remain independent from this Codex-specific contract.
- **FR-009**: The system MUST preserve the ownership boundary that Provider Profile behavior owns credential refs, slot policy, and profile metadata, while managed-session launch, Codex runtime startup, OAuth terminal enrollment, and workload orchestration remain outside this story.
- **FR-010**: MoonSpec artifacts, verification evidence, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-355` and the original Jira preset brief for comparison against the originating request.

### Key Entities

- **Provider Profile**: Durable runtime profile record that stores Codex OAuth refs, provider identity, materialization mode, and slot policy without credential contents.
- **Durable Auth Volume**: Operator-controlled credential backing store referenced by the profile through `volume_ref` and `volume_mount_path`.
- **OAuth Verification Evidence**: Secret-free confirmation that a durable auth volume is ready for Provider Profile registration or repair.
- **Slot Policy**: Profile scheduling and concurrency controls such as maximum parallel runs, cooldown, and lease duration.
- **Profile Snapshot**: Secret-free representation of Provider Profile metadata exposed to operators or workflows.

## Success Criteria *(mandatory)*

- **SC-001**: A validation test can register or update a verified Codex OAuth Provider Profile and observe all required identity, auth-volume ref, materialization, mount path, and slot policy fields.
- **SC-002**: A serialization test can fetch profile data through operator-facing and workflow-facing views and confirm raw tokens, auth files, credential file bodies, auth-volume listings, and environment dumps are absent.
- **SC-003**: A validation test rejects missing or blank durable auth-volume refs and mount paths for Codex OAuth profiles before the profile can become selectable.
- **SC-004**: A scope test confirms this story does not introduce Claude or Gemini task-scoped managed-session parity or force non-Codex profiles into the Codex OAuth shape.
- **SC-005**: Final verification can compare the implementation against the preserved `MM-355` Jira preset brief and confirm the issue key appears in required MoonSpec and delivery metadata.
