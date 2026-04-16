# Feature Specification: Codex Auth Volume Profile Contract

**Feature Branch**: `179-codex-auth-profile`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-318 from MM board
Summary: breakdown docs\ManagedAgents\OAuthTerminal.md
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-318 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md

Selected generated story: STORY-001 Codex Auth Volume Profile Contract
Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthterminal-md/stories.json
Source design: docs/ManagedAgents/OAuthTerminal.md
```

## User Story - Codex OAuth Profile Contract

### Summary

Operators can register or update a Codex OAuth Provider Profile that points to durable credential-volume metadata without exposing credential contents or treating the auth volume as task execution state.

### Goal

Allow verified Codex OAuth credentials to become durable, selectable Provider Profile metadata that later managed-session launch flows can consume safely through refs and policy fields only.

### Independent Test

Create or update a Codex OAuth profile from verified OAuth session data, then assert the stored profile and user-visible profile representations contain `runtime_id`, `provider_id`, `credential_source`, `runtime_materialization_mode`, `volume_ref`, `volume_mount_path`, and slot policy metadata while excluding raw credential contents, token values, auth file payloads, and unrelated runtime-home state.

### Acceptance Scenarios

1. **Given** verified Codex OAuth session data, **when** the profile registrar runs, **then** a Provider Profile exists with `runtime_id = codex_cli`, `credential_source = oauth_volume`, `runtime_materialization_mode = oauth_home`, `volume_ref`, `volume_mount_path`, and slot policy metadata.
2. **Given** profile data is returned through an API or workflow snapshot, **when** an operator or downstream workflow reads it, **then** raw token values and auth file contents are absent.
3. **Given** a non-Codex runtime profile is processed, **when** this story's behavior is evaluated, **then** it does not imply Claude or Gemini task-scoped managed-session parity.
4. **Given** a verified Codex OAuth profile is updated, **when** the existing profile is replaced or repaired, **then** durable auth-volume refs and slot policy remain explicit and credential contents remain outside profile state.

### Edge Cases

- OAuth verification succeeds but profile registration receives missing or blank `volume_ref` or `volume_mount_path`.
- Profile response serialization includes nested provider metadata that could accidentally expose credential-like values.
- Existing profiles are repaired or updated instead of created from scratch.
- A non-Codex profile uses OAuth-style credential source metadata but is outside the current managed-session target scope.

## Requirements

- **FR-001**: The system MUST define and enforce the Codex OAuth Provider Profile shape for `credential_source = oauth_volume` and `runtime_materialization_mode = oauth_home`.
- **FR-002**: The system MUST preserve `runtime_id`, `provider_id`, `volume_ref`, `volume_mount_path`, and slot policy fields when registering or updating a verified Codex OAuth Provider Profile.
- **FR-003**: The system MUST keep raw credential file contents, token values, auth file payloads, and environment dumps out of Provider Profile API responses, workflow payloads, logs, artifacts, and profile snapshots.
- **FR-004**: The system MUST fail validation when required Codex OAuth profile refs such as `volume_ref` or `volume_mount_path` are missing or blank.
- **FR-005**: The system MUST keep Claude and Gemini task-scoped managed-session parity out of scope for this story while allowing their profiles to remain independent from this Codex-specific contract.
- **FR-006**: The system MUST retain the Jira issue key `MM-318` and original preset brief in MoonSpec artifacts so final verification can compare implementation behavior against the originating Jira request.

## Source Design Requirements

- **DESIGN-REQ-001**: MoonMind provides a first-party way to enroll OAuth credentials and target resulting credential volumes into managed runtime containers. Source: `docs/ManagedAgents/OAuthTerminal.md` section 1. Scope: in scope. Maps to FR-001 and FR-002.
- **DESIGN-REQ-002**: Codex is the only fully updated task-scoped managed-session target for this contract; Claude/Gemini parity is out of scope. Source: sections 1 and 2. Scope: in scope as a boundary. Maps to FR-005.
- **DESIGN-REQ-003**: `codex_auth_volume` is durable provider-profile credential storage with enrollment path `/home/app/.codex`. Source: section 3.1. Scope: in scope. Maps to FR-001, FR-002, and FR-004.
- **DESIGN-REQ-010**: Raw credential contents must not enter workflow history, logs, artifacts, or UI responses. Source: sections 4, 8, and 9. Scope: in scope. Maps to FR-003.
- **DESIGN-REQ-016**: OAuth verification registers or updates Provider Profiles preserving Codex OAuth fields and slot policy. Source: section 6. Scope: in scope. Maps to FR-001 and FR-002.
- **DESIGN-REQ-020**: Ownership boundaries between OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration remain explicit. Source: section 11. Scope: in scope for Provider Profile ownership only. Maps to FR-005.
- **DESIGN-REQ-004**: Shared managed-session workspace volume. Scope: out of scope; covered by later generated story STORY-002.
- **DESIGN-REQ-005**: Per-task workspace layout. Scope: out of scope; covered by later generated stories STORY-002 and STORY-003.
- **DESIGN-REQ-006**: Explicit auth-volume target for managed Codex session launch. Scope: out of scope; covered by STORY-002.
- **DESIGN-REQ-007**: One-way auth seeding into per-run Codex home. Scope: out of scope; covered by STORY-003.
- **DESIGN-REQ-008**: Managed task execution uses Codex App Server rather than terminal transport. Scope: out of scope; covered by STORY-003 and STORY-004.
- **DESIGN-REQ-009**: Workload containers do not inherit managed-runtime auth volumes by default. Scope: out of scope; covered by STORY-006.
- **DESIGN-REQ-011**: First-party OAuth terminal architecture. Scope: out of scope; covered by STORY-004.
- **DESIGN-REQ-012**: Short-lived auth runner container. Scope: out of scope; covered by STORY-004.
- **DESIGN-REQ-013**: Authenticated PTY/WebSocket terminal bridge. Scope: out of scope; covered by STORY-004.
- **DESIGN-REQ-014**: No generic Docker exec or ordinary task terminal exposure. Scope: out of scope; covered by STORY-004.
- **DESIGN-REQ-015**: Transport-neutral OAuth session status. Scope: out of scope; covered by STORY-005.
- **DESIGN-REQ-017**: Managed Codex session launch mounts and reserved env values. Scope: out of scope; covered by STORY-002.
- **DESIGN-REQ-018**: Credential verification at OAuth/profile and managed-session launch boundaries. Scope: out of scope except profile registration input assumption; covered by STORY-005.
- **DESIGN-REQ-019**: Artifact-backed operator evidence instead of runtime homes or auth volumes. Scope: out of scope; covered by STORY-003.

## Key Entities

- **Provider Profile**: Durable runtime profile record that stores Codex OAuth refs, provider metadata, and slot policy, not credential contents.
- **Durable Auth Volume**: Operator-controlled credential backing store referenced by the profile through `volume_ref` and `volume_mount_path`.
- **OAuth Verification Result**: Secret-free evidence that a durable auth volume is ready for Provider Profile registration or repair.
- **Slot Policy**: Profile scheduling and concurrency controls such as maximum parallel runs, cooldown, and lease duration.

## Success Criteria

- **SC-001**: A test can register or update a verified Codex OAuth profile and observe the required profile fields without inspecting credential contents.
- **SC-002**: A test can fetch serialized profile data and confirm raw tokens, auth files, credential file bodies, and environment dumps are absent.
- **SC-003**: A validation test rejects missing or blank durable auth-volume refs for Codex OAuth profiles.
- **SC-004**: A scope test confirms this story does not introduce Claude or Gemini task-scoped managed-session parity.
