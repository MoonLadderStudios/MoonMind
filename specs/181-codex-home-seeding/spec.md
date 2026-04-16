# Feature Specification: Per-Run Codex Home Seeding

**Feature Branch**: `181-codex-home-seeding`  
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

Selected generated story: STORY-003 Per-Run Codex Home Seeding
Dependencies: STORY-002
Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthterminal-md/stories.json
Source design: docs/ManagedAgents/OAuthTerminal.md
```

## User Story - Per-Run Codex Home Seeding

### Summary

As a task operator, I can start Codex App Server from a per-run CODEX_HOME seeded one way from durable credentials without making the auth volume live runtime state.

### Goal

Create the per-run codexHomePath under the task workspace.

### Independent Test

Run the session runtime with a fake auth-volume directory and assert eligible files are copied, excluded files are not copied, and Codex App Server receives the per-run CODEX_HOME.

### Acceptance Scenarios

1. **Given a valid auth-volume source path, when the Codex session runtime starts, then eligible auth entries are copied into the per-run Codex home before Codex App Server starts.**
2. **Given the auth-volume source path is missing or not a directory, when startup validation runs, then startup fails with an actionable error.**
3. **Given excluded auth-volume entries exist, when seeding runs, then those entries are not copied into the per-run Codex home.**
4. **Given Codex App Server starts, when its environment is inspected, then `CODEX_HOME` is the per-run Codex home rather than the durable auth-volume mount.**
5. **Given an operator reviews execution evidence, when they inspect MoonMind surfaces, then summaries, diagnostics, logs, and artifacts are used instead of runtime homes or auth volumes.**

### Edge Cases

- Auth volume contains generated logs, session files, or materialized runtime config.
- Auth volume contains symlinks or directories.
- Per-run Codex home already has generated configuration.
- Codex CLI auth layout changes eligible credential file names.

## Requirements

- **FR-001**: The system MUST create the per-run codexHomePath under the task workspace.
- **FR-002**: The system MUST copy only eligible auth entries from MANAGED_AUTH_VOLUME_PATH into codexHomePath.
- **FR-003**: The system MUST start Codex App Server with CODEX_HOME = codexHomePath.
- **FR-004**: The system MUST keep runtime home directories out of operator/audit presentation.
- **FR-005**: The spec artifacts MUST retain Jira issue key MM-318 and the original preset brief so final verification can compare against the originating Jira request.

## Source Design Requirements

- **DESIGN-REQ-005**: Materialize per-task paths under agent_workspaces, including repo, session state, artifact spool, and .moonmind/codex-home. Source: `docs/ManagedAgents/OAuthTerminal.md` 3.2 Shared task workspace volume. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-007**: Seed eligible auth entries one way from the durable auth volume into the per-run Codex home before starting Codex App Server. Source: `docs/ManagedAgents/OAuthTerminal.md` 4. Volume Targeting Rules. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-008**: Keep managed task execution on Codex App Server, not PTY attach or terminal scrollback. Source: `docs/ManagedAgents/OAuthTerminal.md` 4. Volume Targeting Rules; 10. Operator Behavior. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-010**: Never place raw credential contents in workflow history, logs, artifacts, or UI responses. Source: `docs/ManagedAgents/OAuthTerminal.md` 4. Volume Targeting Rules; 8. Verification; 9. Security Model. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-019**: Present Live Logs, artifacts, session summaries, diagnostics, and reset/control-boundary artifacts as execution evidence instead of runtime homes or auth volumes. Source: `docs/ManagedAgents/OAuthTerminal.md` 1. Purpose; 10. Operator Behavior. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-020**: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration. Source: `docs/ManagedAgents/OAuthTerminal.md` 11. Required Boundaries. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-001**: OAuth credential enrollment and targeting. Scope: out of scope for this isolated story; covered by STORY-001, STORY-004.
- **DESIGN-REQ-002**: Codex-focused managed-session scope. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-003**: Durable Codex auth volume. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-004**: Shared task workspace volume. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-006**: Explicit auth-volume target. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-009**: No workload auth inheritance. Scope: out of scope for this isolated story; covered by STORY-006.
- **DESIGN-REQ-011**: First-party OAuth terminal architecture. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-012**: Short-lived auth runner. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-013**: Authenticated terminal bridge. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-014**: No generic shell exposure. Scope: out of scope for this isolated story; covered by STORY-004.
- **DESIGN-REQ-015**: Transport-neutral OAuth state. Scope: out of scope for this isolated story; covered by STORY-005.
- **DESIGN-REQ-016**: Provider Profile registration. Scope: out of scope for this isolated story; covered by STORY-001, STORY-005.
- **DESIGN-REQ-017**: Managed Codex session launch. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-018**: Credential verification boundaries. Scope: out of scope for this isolated story; covered by STORY-005.

## Dependencies

- STORY-002

## Out Of Scope

- Deciding which provider profile is selected.
- OAuth enrollment and profile registration.
- Live Logs UI implementation.

## Key Entities

- **Per-Run Codex Home**: Task-scoped Codex home under the shared workspace used as live `CODEX_HOME` for Codex App Server.
- **Durable Auth Volume**: Provider-profile credential store used only as a source for eligible startup auth entries.
- **Auth Seeding Policy**: Rules deciding which entries copy into the per-run Codex home and which runtime/generated entries are excluded.
- **Operator Evidence**: Logs, diagnostics, summaries, and artifacts that form the reviewable execution record.

## Success Criteria

- **SC-001**: A runtime test verifies eligible auth files copy into the per-run Codex home.
- **SC-002**: A runtime test verifies excluded entries are not copied.
- **SC-003**: A runtime test verifies existing generated per-run config is not overwritten by durable auth contents.
- **SC-004**: A runtime test verifies Codex App Server receives the per-run `CODEX_HOME`.
- **SC-005**: A presentation test or audit check verifies runtime homes and auth volumes are not operator evidence surfaces.
