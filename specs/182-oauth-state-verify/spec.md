# Feature Specification: OAuth Session State and Verification Boundaries

**Feature Branch**: `182-oauth-state-verify`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-359 from MM project
Summary: OAuth Session State and Verification Boundaries
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-359 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-359: OAuth Session State and Verification Boundaries

MoonSpec Story ID: STORY-005
Short Name: oauth-state-verify

User Story
As an operator, I can understand OAuth credential readiness through transport-neutral statuses and secret-free verification results at profile and launch boundaries.

Acceptance Criteria
- OAuth sessions progress through transport-neutral states including pending, starting, bridge_ready, awaiting_user, verifying, registering_profile, and terminal states.
- session_transport = none is valid while PTY bridge is disabled and does not imply tmate semantics.
- OAuth verification failure blocks profile registration and exposes a secret-free failure reason.
- Managed-session launch verifies selected profile materialization before marking the session ready.
- Persisted or returned verification output contains compact status/failure metadata only.

Requirements
- Use transport-neutral OAuth statuses.
- Allow session_transport = none while the interactive bridge is disabled.
- Verify durable auth volume credentials before Provider Profile registration.
- Verify selected profile materialization at managed-session launch.
- Keep verification outputs compact and secret-free.

Independent Test
Exercise OAuth session success, cancel, expire, and disabled-bridge paths with mocked volume verification and assert status transitions plus redacted verification outputs.

Dependencies: STORY-001, STORY-002
Source design: docs/ManagedAgents/OAuthTerminal.md
Source Sections: 5.3 Session transport state; 6. Provider Profile Registration; 8. Verification; 9. Security Model; 11. Required Boundaries
Coverage IDs: DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020
```

## User Story - OAuth Session State and Verification Boundaries

### Summary

As an operator, I can understand OAuth credential readiness through transport-neutral statuses and secret-free verification results at profile and launch boundaries.

### Goal

Use transport-neutral OAuth statuses.

### Independent Test

Exercise OAuth session success, cancel, expire, and disabled-bridge paths with mocked volume verification and assert status transitions plus redacted verification outputs.

### Acceptance Scenarios

1. **Given an OAuth session runs, when lifecycle transitions occur, then status progresses through transport-neutral states and terminal states.**
2. **Given the PTY bridge is disabled, when session metadata is produced, then `session_transport = none` is valid and does not imply tmate URL behavior.**
3. **Given OAuth verification fails, when profile registration would otherwise run, then registration is blocked and a secret-free failure reason is visible.**
4. **Given managed-session launch selects a profile, when materialization verification runs, then the session is not marked ready until verification succeeds.**
5. **Given verification output is persisted or returned, when it is inspected, then it contains compact status and failure metadata only.**

### Edge Cases

- OAuth session is cancelled or expires during verification.
- Interactive bridge is intentionally disabled.
- Verification fails with provider-specific output that may contain secret-like values.
- Status payload shape changes while workflows may already be running.

## Requirements

- **FR-001**: The system MUST use transport-neutral OAuth statuses.
- **FR-002**: The system MUST allow session_transport = none while the interactive bridge is disabled.
- **FR-003**: The system MUST verify durable auth volume credentials before Provider Profile registration.
- **FR-004**: The system MUST verify selected profile materialization at managed-session launch.
- **FR-005**: The system MUST keep verification outputs compact and secret-free.
- **FR-006**: The spec artifacts MUST retain Jira issue key MM-359 and the original preset brief so final verification can compare against the originating Jira request.

## Source Design Requirements

- **DESIGN-REQ-010**: Never place raw credential contents in workflow history, logs, artifacts, or UI responses. Source: `docs/ManagedAgents/OAuthTerminal.md` 4. Volume Targeting Rules; 8. Verification; 9. Security Model. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-015**: Use transport-neutral OAuth statuses and allow session_transport = none while the interactive bridge is disabled. Source: `docs/ManagedAgents/OAuthTerminal.md` 5.3 Session transport state. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-016**: Register or update Provider Profiles after OAuth verification, preserving Codex OAuth fields and slot policy. Source: `docs/ManagedAgents/OAuthTerminal.md` 6. Provider Profile Registration. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-018**: Verify credentials at both the OAuth/profile boundary and the managed-session launch boundary without leaking credential contents. Source: `docs/ManagedAgents/OAuthTerminal.md` 8. Verification. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-020**: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration. Source: `docs/ManagedAgents/OAuthTerminal.md` 11. Required Boundaries. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-001**: OAuth credential enrollment and targeting. Scope: out of scope for this isolated story; covered by STORY-001, STORY-004.
- **DESIGN-REQ-002**: Codex-focused managed-session scope. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-003**: Durable Codex auth volume. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-004**: Shared task workspace volume. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-005**: Per-task workspace layout. Scope: out of scope for this isolated story; covered by STORY-002, STORY-003.
- **DESIGN-REQ-006**: Explicit auth-volume target. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-007**: One-way auth seeding. Scope: out of scope for this isolated story; covered by STORY-003.
- **DESIGN-REQ-008**: Managed execution transport boundary. Scope: out of scope for this isolated story; covered by STORY-003, STORY-004.
- **DESIGN-REQ-009**: No workload auth inheritance. Scope: out of scope for this isolated story; covered by STORY-006.
- **DESIGN-REQ-011**: First-party OAuth terminal architecture. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-012**: Short-lived auth runner. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-013**: Authenticated terminal bridge. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-014**: No generic shell exposure. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-017**: Managed Codex session launch. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-019**: Artifact-backed operator evidence. Scope: out of scope for this isolated story; covered by STORY-003.

## Dependencies

- STORY-001
- STORY-002

## Out Of Scope

- Full terminal UI implementation.
- Codex App Server turn execution.
- Provider-specific auth UX copy.

## Key Entities

- **OAuth Session**: Credential enrollment or repair lifecycle record with transport-neutral status and verification metadata.
- **Session Transport**: Metadata describing whether interactive PTY/WebSocket bridge transport is active or intentionally absent.
- **Verification Result**: Secret-free status/failure metadata proving credential readiness or explaining why registration or launch cannot proceed.
- **Materialization Check**: Launch-boundary readiness validation for a selected Provider Profile.

## Success Criteria

- **SC-001**: Lifecycle tests verify transport-neutral OAuth statuses including terminal states.
- **SC-002**: A disabled-bridge test verifies `session_transport = none` is accepted without tmate semantics.
- **SC-003**: Verification failure tests block Provider Profile registration and redact failure output.
- **SC-004**: Launch-boundary tests verify selected profile materialization before ready state.
- **SC-005**: Persistence or response tests verify verification outputs are compact and secret-free.
