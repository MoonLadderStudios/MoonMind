# Feature Specification: Codex Managed Session Volume Targeting

**Feature Branch**: `180-codex-volume-targeting`  
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

Selected generated story: STORY-002 Codex Managed Session Volume Targeting
Dependencies: STORY-001
Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthterminal-md/stories.json
Source design: docs/ManagedAgents/OAuthTerminal.md
```

## User Story - Codex Managed Session Volume Targeting

### Summary

As a task operator, I can launch a managed Codex session that receives the workspace volume and only receives the durable auth volume at an explicit, separate auth target.

### Goal

Mount agent_workspaces into every managed Codex session container.

### Independent Test

Launch a managed Codex session with and without MANAGED_AUTH_VOLUME_PATH and inspect the generated Docker command plus validation failures for invalid mount targets.

### Acceptance Scenarios

1. **Given a managed Codex session launch request, when the launcher builds the runtime container, then the shared task workspace is mounted at `/work/agent_jobs`.**
2. **Given no explicit auth target is present, when the launcher builds the runtime container, then `codex_auth_volume` is not mounted.**
3. **Given an explicit auth target is present, when the launcher builds the runtime container, then `codex_auth_volume` is mounted only at that target and not at the per-run Codex home.**
4. **Given the explicit auth target equals the per-run Codex home, when launch validation runs, then launch fails before creating the container.**
5. **Given a managed-session container starts, when the session runtime reads its environment, then reserved `MOONMIND_SESSION_*` values are present and launcher-owned.**

### Edge Cases

- Auth target is relative, blank, or path-normalizes to the Codex home.
- Selected profile does not require an auth volume.
- Future provider profiles provide dynamic auth target paths.
- Reserved session environment values are accidentally supplied by caller-controlled input.

## Requirements

- **FR-001**: The system MUST mount agent_workspaces into every managed Codex session container.
- **FR-002**: The system MUST conditionally mount codex_auth_volume only when explicitly set by selected profile or launcher policy.
- **FR-003**: The system MUST reject auth-volume targets that equal codexHomePath.
- **FR-004**: The system MUST pass reserved session environment values into the container.
- **FR-005**: The spec artifacts MUST retain Jira issue key MM-318 and the original preset brief so final verification can compare against the originating Jira request.

## Source Design Requirements

- **DESIGN-REQ-004**: Treat agent_workspaces as the required managed-session workspace volume mounted at /work/agent_jobs. Source: `docs/ManagedAgents/OAuthTerminal.md` 3.2 Shared task workspace volume. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-005**: Materialize per-task paths under agent_workspaces, including repo, session state, artifact spool, and .moonmind/codex-home. Source: `docs/ManagedAgents/OAuthTerminal.md` 3.2 Shared task workspace volume. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-006**: Mount auth volumes into managed Codex sessions only through an explicit MANAGED_AUTH_VOLUME_PATH, separate from codexHomePath. Source: `docs/ManagedAgents/OAuthTerminal.md` 3.3 Explicit auth-volume target; 4. Volume Targeting Rules. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-017**: Launch managed Codex session containers with required workspace mount, conditional auth-volume mount, and reserved session environment values. Source: `docs/ManagedAgents/OAuthTerminal.md` 7. Managed Codex Session Launch. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-020**: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration. Source: `docs/ManagedAgents/OAuthTerminal.md` 11. Required Boundaries. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-001**: OAuth credential enrollment and targeting. Scope: out of scope for this isolated story; covered by STORY-001, STORY-004.
- **DESIGN-REQ-002**: Codex-focused managed-session scope. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-003**: Durable Codex auth volume. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-007**: One-way auth seeding. Scope: out of scope for this isolated story; covered by STORY-003.
- **DESIGN-REQ-008**: Managed execution transport boundary. Scope: out of scope for this isolated story; covered by STORY-003, STORY-004.
- **DESIGN-REQ-009**: No workload auth inheritance. Scope: out of scope for this isolated story; covered by STORY-006.
- **DESIGN-REQ-010**: No credential leakage. Scope: out of scope for this isolated story; covered by STORY-001, STORY-003, STORY-005, STORY-006.
- **DESIGN-REQ-011**: First-party OAuth terminal architecture. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-012**: Short-lived auth runner. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-013**: Authenticated terminal bridge. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-014**: No generic shell exposure. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-015**: Transport-neutral OAuth state. Scope: out of scope for this isolated story; covered by STORY-005.
- **DESIGN-REQ-016**: Provider Profile registration. Scope: out of scope for this isolated story; covered by STORY-001, STORY-005.
- **DESIGN-REQ-018**: Credential verification boundaries. Scope: out of scope for this isolated story; covered by STORY-005.
- **DESIGN-REQ-019**: Artifact-backed operator evidence. Scope: out of scope for this isolated story; covered by STORY-003.

## Dependencies

- STORY-001

## Out Of Scope

- OAuth terminal enrollment flow.
- Credential verification implementation.
- Workload container launches.

## Key Entities

- **Managed Session Launch Request**: Boundary payload carrying task workspace paths, per-run Codex home path, image, control URL, profile-derived auth target, and sanitized environment values.
- **Shared Task Workspace Volume**: Operator-controlled workspace volume mounted into managed sessions at `/work/agent_jobs`.
- **Auth Volume Target**: Explicit mount destination for durable Codex auth material, separate from the per-run Codex home.
- **Reserved Session Environment**: Launcher-owned environment values used by the session runtime to find workspace, state, artifacts, Codex home, image, and control URL.

## Success Criteria

- **SC-001**: A launch test verifies workspace mounting at `/work/agent_jobs`.
- **SC-002**: A launch test verifies no auth volume mount exists when no explicit auth target is present.
- **SC-003**: A launch test verifies auth volume mounting occurs only at the explicit target.
- **SC-004**: A validation test rejects auth target equality with the per-run Codex home.
- **SC-005**: A startup test verifies reserved session environment values are present and caller input cannot override them.
