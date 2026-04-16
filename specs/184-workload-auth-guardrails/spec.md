# Feature Specification: Workload Auth-Volume Guardrails

**Feature Branch**: `184-workload-auth-guardrails`  
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

Selected generated story: STORY-006 Workload Auth-Volume Guardrails
Dependencies: None
Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthterminal-md/stories.json
Source design: docs/ManagedAgents/OAuthTerminal.md
```

## User Story - Workload Auth-Volume Guardrails

### Summary

As a security reviewer, I can verify that Docker-backed workload containers launched near managed sessions do not implicitly inherit managed-runtime auth volumes.

### Goal

Enforce workload profile mount allowlists.

### Independent Test

Launch workload profiles from a simulated managed-session-assisted step and assert auth-volume mounts are rejected unless explicitly declared by approved workload policy.

### Acceptance Scenarios

1. **Given a workload launch is requested from a managed Codex session, when mount policy is evaluated, then no managed-runtime auth volume is inherited by default.**
2. **Given a workload profile declares ordinary workspace or cache mounts, when launch validation runs, then launch proceeds without auth volumes.**
3. **Given a workload profile requests an auth or credential mount without explicit approval, when launch validation runs, then launch is rejected with policy metadata.**
4. **Given a workload container runs, when Mission Control or APIs present it, then it is not represented as the managed Codex session identity.**

### Edge Cases

- Workload launch is requested by an agent running inside a managed session.
- Workload profile declares a credential mount without justification.
- Workload identity fields resemble managed-session identity fields.
- Future workload exceptions need explicit policy without broad inheritance.

## Requirements

- **FR-001**: The system MUST enforce workload profile mount allowlists.
- **FR-002**: The system MUST reject implicit managed-runtime auth-volume inheritance.
- **FR-003**: The system MUST require explicit justification/profile declaration for any credential mount.
- **FR-004**: The system MUST keep workload containers separate from managed session identity fields.
- **FR-005**: The spec artifacts MUST retain Jira issue key MM-318 and the original preset brief so final verification can compare against the originating Jira request.

## Source Design Requirements

- **DESIGN-REQ-009**: Do not make Docker workload containers inherit managed-runtime auth volumes by default. Source: `docs/ManagedAgents/OAuthTerminal.md` 2. Scope; 11. Required Boundaries. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-010**: Never place raw credential contents in workflow history, logs, artifacts, or UI responses. Source: `docs/ManagedAgents/OAuthTerminal.md` 4. Volume Targeting Rules; 8. Verification; 9. Security Model. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-020**: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration. Source: `docs/ManagedAgents/OAuthTerminal.md` 11. Required Boundaries. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-001**: OAuth credential enrollment and targeting. Scope: out of scope for this isolated story; covered by STORY-001, STORY-004.
- **DESIGN-REQ-002**: Codex-focused managed-session scope. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-003**: Durable Codex auth volume. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-004**: Shared task workspace volume. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-005**: Per-task workspace layout. Scope: out of scope for this isolated story; covered by STORY-002, STORY-003.
- **DESIGN-REQ-006**: Explicit auth-volume target. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-007**: One-way auth seeding. Scope: out of scope for this isolated story; covered by STORY-003.
- **DESIGN-REQ-008**: Managed execution transport boundary. Scope: out of scope for this isolated story; covered by STORY-003, STORY-004.
- **DESIGN-REQ-011**: First-party OAuth terminal architecture. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-012**: Short-lived auth runner. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-013**: Authenticated terminal bridge. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-014**: No generic shell exposure. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-015**: Transport-neutral OAuth state. Scope: out of scope for this isolated story; covered by STORY-005.
- **DESIGN-REQ-016**: Provider Profile registration. Scope: out of scope for this isolated story; covered by STORY-001, STORY-005.
- **DESIGN-REQ-017**: Managed Codex session launch. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-018**: Credential verification boundaries. Scope: out of scope for this isolated story; covered by STORY-005.
- **DESIGN-REQ-019**: Artifact-backed operator evidence. Scope: out of scope for this isolated story; covered by STORY-003.

## Dependencies

- None.

## Out Of Scope

- Managed Codex session container launch itself.
- OAuth terminal enrollment.
- Specialized workload runner internals beyond mount policy.

## Key Entities

- **Workload Profile**: Policy definition for Docker-backed workload mounts and allowed credential access.
- **Managed Runtime Auth Volume**: Credential volume associated with a managed agent runtime and not inherited by workloads by default.
- **Credential Mount Declaration**: Explicit workload profile approval and justification for mounting credential material.
- **Workload Identity**: Container identity separate from managed-session identity fields such as session and turn handles.

## Success Criteria

- **SC-001**: A workload launch test verifies no managed-runtime auth volume is inherited by default.
- **SC-002**: A workload profile test verifies workspace/cache mounts proceed without auth volumes.
- **SC-003**: A policy test rejects undeclared credential mounts with actionable metadata.
- **SC-004**: A presentation/API test verifies workload containers are not shown as managed-session identity.
- **SC-005**: A policy exception test verifies approved credential mounts require explicit declaration and justification.
