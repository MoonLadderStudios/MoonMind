# Feature Specification: Launch Codex Auth Materialization

**Feature Branch**: `175-launch-codex-auth-materialization`  
**Created**: 2026-04-15  
**Status**: Draft  
**Input**: MM-334: Launch managed Codex sessions with explicit auth materialization

User Story
As a task operator, I can launch a managed Codex session using a selected OAuth-backed Provider Profile, with the durable auth volume mounted only at an explicit auth target and eligible credentials copied one way into the per-run CODEX_HOME under the task workspace before Codex App Server starts.
Source Document
- Path: docs/ManagedAgents/OAuthTerminal.md
- Sections: 3.2 Shared task workspace volume, 3.3 Explicit auth-volume target, 4. Volume Targeting Rules, 7. Managed Codex Session Launch, 8. Verification
- Coverage IDs: DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017
- Breakdown Story ID: STORY-003
- Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthtermina-74125184/stories.json

## User Story - Managed Codex OAuth Auth Materialization

### Summary

A task operator launches a managed Codex session with an OAuth-backed Provider Profile, and MoonMind materializes credentials into the task-scoped runtime home without using the durable auth volume as the live Codex home.

### Goal

Allow selected OAuth-backed Codex provider credentials to be used by managed Codex sessions while keeping durable auth storage separate from per-run session state.

### Independent Test

Start a managed Codex session with a selected OAuth-backed profile and verify that the launch request uses the task workspace for the per-run Codex home, mounts the durable auth volume only at an explicit auth target, rejects an auth target equal to the Codex home, copies only eligible auth entries into the per-run Codex home, and starts Codex App Server with that per-run home.

### Acceptance Scenarios

1. **Given** a task operator selects an OAuth-backed Codex Provider Profile, **when** MoonMind launches the managed Codex session, **then** the session receives the shared task workspace volume and the per-run `CODEX_HOME` path is under that task workspace.
2. **Given** the selected profile requires a durable auth volume, **when** the session container is launched, **then** the auth volume is mounted only at an explicit auth target separate from the per-run Codex home.
3. **Given** the session runtime starts inside the container, **when** an explicit auth target is present, **then** it validates that the auth target is not the per-run Codex home, copies eligible auth entries one way into the per-run Codex home, and starts Codex App Server with `CODEX_HOME` set to the per-run path.
4. **Given** an invalid launch attempts to use the same path for the auth target and per-run Codex home, **when** the launcher or session runtime validates the request, **then** the session fails fast before using that path as live runtime state.

### Edge Cases

- The selected profile has no auth-volume target; the session still uses the per-run Codex home and does not mount the durable auth volume.
- The durable auth source contains session logs or materialized runtime config; those entries are not copied over generated per-run config.
- Workspace, artifact, session state, or Codex home paths are missing, non-writable, or outside the managed workspace boundary.
- Credential contents must not be included in workflow history, logs, artifacts, or UI-visible responses.

## Requirements

- **FR-001**: Managed Codex session launches MUST use the shared task workspace volume for task repo, session state, artifact spool, and per-run Codex home paths. Maps to DESIGN-REQ-005 and DESIGN-REQ-015.
- **FR-002**: OAuth-backed Provider Profile selection MUST pass compact profile metadata and an explicit auth target to the launch boundary without passing raw credential contents. Maps to DESIGN-REQ-006 and DESIGN-REQ-017.
- **FR-003**: The managed-session launcher MUST mount the durable Codex auth volume only when an explicit auth target is present, and the target MUST be separate from the per-run Codex home. Maps to DESIGN-REQ-006, DESIGN-REQ-007, and DESIGN-REQ-015.
- **FR-004**: The session runtime MUST independently validate that `MANAGED_AUTH_VOLUME_PATH` is an absolute auth source separate from the per-run `CODEX_HOME` before credential materialization. Maps to DESIGN-REQ-006 and DESIGN-REQ-015.
- **FR-005**: The session runtime MUST copy eligible auth entries one way from the durable auth source into the per-run Codex home before Codex App Server starts, without treating session-local state as provider-profile source of truth. Maps to DESIGN-REQ-016.
- **FR-006**: Codex App Server MUST start with `CODEX_HOME` set to the per-run Codex home under the task workspace. Maps to DESIGN-REQ-015 and DESIGN-REQ-016.
- **FR-007**: Verification MUST cover both the profile-to-launch boundary and the in-container runtime materialization boundary without exposing credential contents. Maps to DESIGN-REQ-017.

## Source Design Requirements

- **DESIGN-REQ-005**: Managed Codex sessions receive the shared `agent_workspaces` volume and per-task layout under `/work/agent_jobs`. Source: `docs/ManagedAgents/OAuthTerminal.md` section 3.2. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-006**: Durable auth volume mounts are explicit through `MANAGED_AUTH_VOLUME_PATH` and separate from the live Codex home. Source: section 3.3. Scope: in scope. Maps to FR-002, FR-003, FR-004.
- **DESIGN-REQ-007**: Credential copying is one-way from durable auth volume to per-run Codex home; workload containers do not inherit auth volumes by default. Source: section 4. Scope: in scope for managed session launch; workload container propagation is out of scope for this story. Maps to FR-003 and FR-005.
- **DESIGN-REQ-015**: Managed Codex launch passes reserved workspace, state, artifact, Codex home, and control URL environment values. Source: section 7. Scope: in scope. Maps to FR-001, FR-003, FR-004, FR-006.
- **DESIGN-REQ-016**: Session runtime validates workspace paths, creates the per-run Codex home, seeds eligible auth entries, and starts Codex App Server with `CODEX_HOME = codexHomePath`. Source: section 7. Scope: in scope. Maps to FR-005 and FR-006.
- **DESIGN-REQ-017**: Verification happens at OAuth/profile and managed-session launch boundaries without copying credential contents into workflow payloads, artifacts, logs, or UI responses. Source: section 8. Scope: in scope. Maps to FR-002 and FR-007.

## Key Entities

- **Provider Profile**: Selected runtime profile carrying compact credential source metadata, volume reference, explicit auth target, and materialization mode.
- **Durable Auth Volume**: Provider-profile credential store mounted only at the explicit auth target when required.
- **Per-Run Codex Home**: Task-scoped Codex home under the shared workspace that receives one-way credential seeds and is used as `CODEX_HOME`.
- **Managed Session Launch Request**: Boundary payload containing workspace paths, per-run Codex home path, image, control URL, profile metadata, and sanitized environment values.

## Success Criteria

- **SC-001**: Unit tests verify OAuth-backed Provider Profile launch passes an explicit auth target while keeping the per-run Codex home under the task workspace.
- **SC-002**: Unit tests verify launcher/runtime validation rejects an auth target equal to the per-run Codex home.
- **SC-003**: Unit tests verify eligible auth entries are copied into the per-run Codex home without overwriting materialized config or copying excluded runtime logs.
- **SC-004**: Unit tests verify Codex App Server receives `CODEX_HOME` as the per-run task workspace home.
