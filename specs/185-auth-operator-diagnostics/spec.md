# Feature Specification: Auth Operator Diagnostics

**Feature Branch**: `185-auth-operator-diagnostics`
**Created**: 2026-04-16
**Status**: Draft
**Input**: MM-336: [MM-318] Project operator-visible managed auth diagnostics

## Original Jira Preset Brief

```text
MM-336: [MM-318] Project operator-visible managed auth diagnostics

User Story
As an operator, I can understand OAuth enrollment, Provider Profile registration, managed Codex auth materialization, and ordinary task execution through safe statuses, summaries, diagnostics, logs, artifacts, and session metadata without inspecting auth volumes, runtime homes, or terminal scrollback as execution records.

Source Document
- Path: docs/ManagedAgents/OAuthTerminal.md
- Sections: 1. Purpose, 8. Verification, 10. Operator Behavior, 11. Required Boundaries
- Coverage IDs: DESIGN-REQ-004, DESIGN-REQ-016, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022
- Breakdown Story ID: STORY-005
- Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthtermina-74125184/stories.json

Acceptance Criteria
- OAuth enrollment surfaces show session status, timestamps, failure reason, and registered profile summary where applicable.
- Managed Codex session metadata records selected profile refs, volume refs, auth mount target, workspace Codex home path, readiness, and validation failure reasons without credential contents.
- Ordinary task execution views direct operators to Live Logs, artifacts, summaries, diagnostics, and reset/control-boundary artifacts.
- Runtime home directories and auth volumes are not exposed as presentation artifacts.
- Enrollment terminal scrollback is not treated as the durable execution record for managed task runs.
- Diagnostic events make it clear which component owns enrollment, profile metadata, session mounts, runtime seeding, or workload container behavior.

Requirements
- Publish safe OAuth/profile/session status and diagnostics metadata for operator use.
- Record managed-session readiness and auth materialization validation failures in session metadata or diagnostics artifacts.
- Avoid presenting auth volumes, runtime homes, and terminal scrollback as task artifacts.
- Keep operator-visible diagnostics aligned with the component ownership boundaries in the design.

Independent Test
Simulate successful and failed enrollment plus successful and failed managed Codex session launch, then assert Mission Control/API projections show safe statuses, profile summaries, validation failures, diagnostics refs, and artifact/log pointers while omitting raw credentials, auth-volume listings, runtime-home contents, and terminal scrollback from ordinary task records.

Notes
- Short name: auth-operator-diagnostics
- Dependencies: STORY-001, STORY-003
- Needs clarification: None

Out Of Scope
- Displaying credential files or raw volume listings
- Making runtime home directories browseable artifacts
- Using OAuth terminal scrollback as the durable task execution record
- Building Live Logs transport

Source Design Coverage
- DESIGN-REQ-004: Owns operator-facing distinction between credentials, workspaces, and artifacts.
- DESIGN-REQ-016: Owns projection of startup validation and readiness diagnostics.
- DESIGN-REQ-020: Owns durable execution record expectations.
- DESIGN-REQ-021: Owns diagnostics that preserve component responsibility boundaries.
- DESIGN-REQ-022: Owns non-goals around Live Logs transport and task-run PTY attach in operator docs/projections.
```

## User Story - View Safe Auth Diagnostics

**Summary**: As an operator, I want OAuth enrollment, Provider Profile registration, managed Codex auth materialization, and task execution surfaces to expose safe statuses and diagnostics so I can understand auth-related state without inspecting credential volumes, runtime homes, or terminal scrollback.

**Goal**: Provide safe operator-visible projections for OAuth sessions and managed Codex session launches that identify status, selected profile refs, mount refs, readiness, validation failures, and durable artifact/log pointers while keeping credential contents and raw runtime homes out of ordinary task records.

**Independent Test**: Simulate successful and failed OAuth enrollment plus successful and failed managed Codex session launch; the story passes only when API/runtime projections expose safe status, profile summary, readiness, validation failure, diagnostics refs, and artifact/log pointers and omit raw credentials, auth-volume listings, runtime-home contents, and terminal scrollback.

**Acceptance Scenarios**:

1. **Given** an OAuth enrollment session has current status, timestamps, and profile metadata, **when** an operator fetches the session through the API, **then** the response includes sanitized status, timestamps, failure reason when present, and a registered profile summary where available.
2. **Given** a managed Codex session launch uses an OAuth-backed provider profile, **when** the launch succeeds, **then** session metadata records the selected profile ref, credential source, volume ref, auth mount target, workspace Codex home path, readiness state, and component ownership without credential contents or raw listings.
3. **Given** managed Codex auth materialization or launch validation fails, **when** the failure is surfaced to the operator, **then** metadata includes a sanitized validation failure reason and component ownership while omitting credential contents, environment dumps, raw auth-volume listings, runtime-home contents, and terminal scrollback.
4. **Given** an ordinary managed task run is inspected, **when** its execution records are presented, **then** durable logs, artifacts, summaries, diagnostics, and reset/control-boundary refs are the operator-visible execution record rather than auth volumes, runtime homes, or OAuth terminal scrollback.

### Edge Cases

- OAuth enrollment has not yet registered a provider profile.
- OAuth enrollment failed with a secret-like failure reason or auth path.
- Managed Codex launch has no selected provider profile.
- Managed Codex launch uses an OAuth profile with a volume ref but no auth mount target.
- Runtime materialization failure text contains token-like values, auth paths, or raw environment values.

## Assumptions

- STORY-001 and STORY-003 provide Provider Profile metadata and managed Codex auth materialization inputs that this story can project safely.
- Live Logs and artifact transport already exist; this story references their safe refs and does not build new transport.

## Source Design Requirements

- **DESIGN-REQ-004**: Operators must be able to distinguish credentials, workspaces, and artifacts without treating auth volumes or runtime homes as presentation artifacts. Source: `docs/ManagedAgents/OAuthTerminal.md` sections 1 and 10. Scope: in scope. Maps to FR-001, FR-004, FR-006, and FR-007.
- **DESIGN-REQ-016**: Startup validation and readiness diagnostics must be projected for managed Codex auth materialization. Source: section 8. Scope: in scope. Maps to FR-003 and FR-005.
- **DESIGN-REQ-020**: Logs, summaries, diagnostics, and continuity artifacts remain the durable operator/audit truth for ordinary task execution. Source: sections 1 and 10. Scope: in scope. Maps to FR-006 and FR-007.
- **DESIGN-REQ-021**: Diagnostic events must preserve ownership boundaries between OAuth terminal, Provider Profile, managed-session controller, Codex runtime, and workload orchestration. Source: section 11. Scope: in scope. Maps to FR-002, FR-003, and FR-005.
- **DESIGN-REQ-022**: OAuth terminal scrollback and raw auth/runtime homes must not become the ordinary managed task execution record. Source: sections 10 and 11. Scope: in scope. Maps to FR-006 and FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: OAuth session API responses MUST expose sanitized session status, creation/expiration timestamps, terminal transport refs, and redacted failure reason without credential contents, token values, raw auth-volume listings, or raw runtime-home contents.
- **FR-002**: OAuth session API responses SHOULD include a registered provider profile summary when the profile exists, limited to profile ID, runtime ID, provider ID/label, credential source, materialization mode, account label, enabled/default flags, and rate-limit policy.
- **FR-003**: Managed Codex launch metadata MUST expose safe auth materialization diagnostics including selected provider profile ref when present, credential source, volume ref, auth mount target, workspace Codex home path, readiness state, and owning component.
- **FR-004**: Managed Codex launch metadata MUST NOT expose credential file contents, token values, environment dumps, raw auth-volume listings, runtime-home directory contents, or OAuth terminal scrollback.
- **FR-005**: Managed Codex launch failures caused by auth materialization or startup validation MUST surface a sanitized validation failure reason and owning component without leaking secret-like values or auth paths.
- **FR-006**: Ordinary managed task execution records MUST direct operators to Live Logs, artifacts, summaries, diagnostics, and reset/control-boundary refs as durable evidence, not auth volumes, runtime homes, or OAuth terminal scrollback.
- **FR-007**: Diagnostic metadata MUST identify whether OAuth enrollment, Provider Profile metadata, managed-session container mounts, Codex runtime seeding, or workload orchestration owns the reported state.

### Key Entities

- **OAuth Session Projection**: Browser/API-safe view of an OAuth enrollment session.
- **Provider Profile Summary**: Compact non-secret profile metadata suitable for operator display.
- **Auth Materialization Diagnostics**: Non-secret managed-session launch metadata describing selected profile refs, mount refs, readiness, validation failures, and owning component.
- **Durable Execution Evidence**: Live log refs, artifact refs, summaries, diagnostics, and reset/control-boundary refs for ordinary managed task runs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: OAuth session API tests prove responses include status, timestamps, redacted failure reason, and profile summary when available, with zero secret-like values or raw auth paths.
- **SC-002**: Managed Codex launch tests prove success metadata includes selected profile ref, credential source, volume ref, auth mount target, workspace Codex home path, readiness, and owning component.
- **SC-003**: Managed Codex launch failure tests prove validation failure details are sanitized and classified by owning component.
- **SC-004**: Projection tests prove task execution metadata references logs, artifacts, summaries, diagnostics, and reset/control-boundary refs without presenting auth volumes, runtime homes, or terminal scrollback as artifacts.
- **SC-005**: Source design coverage for DESIGN-REQ-004, DESIGN-REQ-016, DESIGN-REQ-020, DESIGN-REQ-021, and DESIGN-REQ-022 is mapped to passing verification evidence.
