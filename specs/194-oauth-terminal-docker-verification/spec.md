# Feature Specification: OAuth Terminal Docker Verification

**Feature Branch**: `194-oauth-terminal-docker-verification`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
# MM-363 MoonSpec Orchestration Input

## Source

- Jira issue: MM-363
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [OAuthTerminal] Close Docker-backed integration verification for managed auth volumes
- Labels: `MM-318`, `managed-sessions`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-363 from MM project
Summary: [OAuthTerminal] Close Docker-backed integration verification for managed auth volumes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-363 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-363: [OAuthTerminal] Close Docker-backed integration verification for managed auth volumes

Short Name
oauth-terminal-docker-verification

User Story
As a MoonMind maintainer, I can run and record Docker-enabled integration evidence for OAuthTerminal managed-session auth behavior, so the remaining ADDITIONAL_WORK_NEEDED verification reports can close without relying only on unit and fake-runner evidence.

Acceptance Criteria
- Given a Docker-enabled verification environment, `./tools/test_integration.sh` passes for the OAuthTerminal-relevant managed-session coverage.
- Given managed Codex session launch is inspected, `agent_workspaces` is mounted at `/work/agent_jobs` and the auth volume is mounted only at `MANAGED_AUTH_VOLUME_PATH` when explicitly configured.
- Given `MANAGED_AUTH_VOLUME_PATH` equals `codexHomePath`, launch fails before container creation.
- Given runtime startup receives a valid auth-volume path, eligible auth entries seed one way into the per-run `CODEX_HOME` before Codex App Server starts.
- Given the OAuth terminal flow runs against Docker, the auth runner and PTY bridge lifecycle are exercised end to end.
- Verification reports are updated from ADDITIONAL_WORK_NEEDED only when the evidence above passes; otherwise they record the exact blocker.

Requirements
- Run Docker-enabled integration verification for OAuthTerminal managed-session auth behavior.
- Ensure OAuthTerminal-relevant managed-session coverage passes through `./tools/test_integration.sh` in a Docker-enabled environment.
- Verify managed Codex session launch mounts `agent_workspaces` at `/work/agent_jobs`.
- Verify the auth volume is mounted only at `MANAGED_AUTH_VOLUME_PATH` when explicitly configured.
- Verify launch fails before container creation when `MANAGED_AUTH_VOLUME_PATH` equals `codexHomePath`.
- Verify valid auth-volume startup seeds eligible auth entries one way into the per-run `CODEX_HOME` before Codex App Server starts.
- Exercise the Docker-backed OAuth terminal auth runner and PTY bridge lifecycle end to end.
- Update verification reports only when evidence passes; otherwise record the exact blocker.

Independent Test
Run Docker-enabled integration verification for OAuthTerminal-relevant managed-session coverage and confirm the launch, auth-volume targeting, startup seeding, auth runner, and PTY bridge lifecycle behavior match the documented contract.

Source Document
- `docs/ManagedAgents/OAuthTerminal.md`

Source Sections
- 3. Current Codex Volume Model
- 4. Volume Targeting Rules
- 7. Managed Codex Session Launch
- 8. Verification

Coverage IDs
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-020

Current Evidence
- `specs/175-launch-codex-auth-materialization/verification.md` has verdict ADDITIONAL_WORK_NEEDED only because Docker-backed integration could not run in the managed container.
- `specs/180-codex-volume-targeting/verification.md` has verdict ADDITIONAL_WORK_NEEDED only because `./tools/test_integration.sh` could not run without `/var/run/docker.sock`.
- `specs/183-oauth-terminal-flow/verification.md` also needs Docker-enabled integration evidence for the real auth runner and WebSocket bridge lifecycle.

Relevant Implementation Notes
- This story is primarily verification closure, plus any small test harness fixes needed to make the documented OAuthTerminal managed-session auth contract observable.
- Preserve the separation between managed Codex session auth materialization and workload-container auth-volume guardrails.
- Record exact Docker or environment blockers in verification reports if Docker-backed evidence cannot be produced.

Out of Scope
- New product behavior beyond verification and small test harness fixes needed to make the documented contract observable.

Verification
- Run `./tools/test_integration.sh` in a Docker-enabled verification environment.
- Inspect managed Codex session launch for `agent_workspaces`, `MANAGED_AUTH_VOLUME_PATH`, and `codexHomePath` behavior.
- Exercise the Docker-backed OAuth terminal auth runner and PTY bridge lifecycle.
- Update the affected verification reports with passing evidence or the exact remaining blocker.

Needs Clarification
- None
```

**Implementation Intent**: Runtime verification closure. Required deliverables include Docker-backed integration evidence when available, plus any small runtime or test harness fixes needed to make the documented OAuthTerminal managed-session auth contract observable.

## User Story - OAuth Terminal Docker Verification

**Summary**: As a MoonMind maintainer, I want Docker-enabled integration evidence for OAuthTerminal managed-session auth behavior so that prior ADDITIONAL_WORK_NEEDED reports can be closed with real runtime proof.

**Goal**: Maintainers can run the required Docker-backed integration checks, inspect the managed Codex launch and OAuth terminal runtime boundaries, and update verification reports only with passing evidence or an exact blocker.

**Independent Test**: Run `./tools/test_integration.sh` in a Docker-enabled environment and confirm that OAuthTerminal-relevant managed-session coverage proves workspace mounting, explicit auth-volume targeting, auth target validation, one-way auth seeding, and Docker-backed auth runner/PTY bridge lifecycle behavior.

**Acceptance Scenarios**:

1. **Given** a Docker-enabled verification environment, **when** `./tools/test_integration.sh` runs, **then** OAuthTerminal-relevant managed-session integration coverage passes.
2. **Given** managed Codex session launch is inspected, **when** the launch command is built, **then** `agent_workspaces` is mounted at `/work/agent_jobs`.
3. **Given** an auth volume is explicitly configured, **when** the managed Codex session launch command is built, **then** the auth volume is mounted only at `MANAGED_AUTH_VOLUME_PATH` and not at `codexHomePath`.
4. **Given** `MANAGED_AUTH_VOLUME_PATH` equals `codexHomePath`, **when** launch validation runs, **then** the session fails before container creation.
5. **Given** runtime startup receives a valid auth-volume path, **when** Codex App Server startup begins, **then** eligible auth entries are seeded one way into per-run `CODEX_HOME`.
6. **Given** the OAuth terminal flow runs against Docker, **when** the flow completes, fails, expires, or is cancelled, **then** the auth runner and PTY bridge lifecycle is exercised end to end with secret-free evidence.
7. **Given** Docker-backed evidence is unavailable or fails, **when** verification reports are updated, **then** they record the exact blocker instead of claiming closure.

### Edge Cases

- Docker socket or daemon is unavailable in the verification environment.
- Compose-backed integration tests fail before OAuthTerminal-relevant coverage runs.
- Runtime evidence proves launch behavior but not OAuth terminal runner behavior, or the reverse.
- Integration output includes credential-like text that must not be copied into artifacts or reports.
- A small test harness defect prevents observing behavior that unit and fake-runner evidence already covered.

## Assumptions

- MM-363 is a verification-closure story for already implemented OAuthTerminal and managed Codex auth-volume behavior, not a request for new product semantics.
- Small test harness fixes are in scope only when they are necessary to observe the documented contract in Docker-backed integration.
- Verification reports may remain ADDITIONAL_WORK_NEEDED if Docker-backed evidence cannot be produced in the active runtime.

## Source Design Requirements

- **DESIGN-REQ-004**: Source section 3.2 requires every managed Codex session container to receive the shared `agent_workspaces` volume mounted at `/work/agent_jobs`. Scope: in scope. Maps to FR-001, FR-002, and FR-009.
- **DESIGN-REQ-005**: Source section 3.2 requires per-task paths, including repo, session state, artifacts, and per-run Codex home, to live under `agent_workspaces`. Scope: in scope. Maps to FR-001, FR-002, FR-005, and FR-009.
- **DESIGN-REQ-006**: Source sections 3.3 and 4 require auth volumes to mount into managed Codex sessions only through an explicit `MANAGED_AUTH_VOLUME_PATH` separate from `codexHomePath`. Scope: in scope. Maps to FR-003, FR-004, and FR-009.
- **DESIGN-REQ-007**: Source section 4 requires credential copying from durable auth volume to per-run Codex home to be one-way during session startup. Scope: in scope. Maps to FR-005 and FR-009.
- **DESIGN-REQ-017**: Source section 7 requires managed Codex session launch to pass reserved workspace, state, artifact, Codex home, and control URL values and to mount workspace and conditional auth volumes correctly. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, and FR-009.
- **DESIGN-REQ-018**: Source section 8 requires verification at the OAuth/Profile boundary and managed-session launch boundary without copying credential contents into workflow payloads, artifacts, logs, or UI responses. Scope: in scope. Maps to FR-006, FR-007, FR-008, and FR-009.
- **DESIGN-REQ-020**: Source section 11 requires OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration to keep ownership boundaries separate. Scope: in scope. Maps to FR-006, FR-007, FR-008, and FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The verification flow MUST run or attempt the Docker-backed integration command required for OAuthTerminal-relevant managed-session coverage.
- **FR-002**: The evidence MUST verify that managed Codex sessions mount `agent_workspaces` at `/work/agent_jobs`.
- **FR-003**: The evidence MUST verify that configured auth volumes are mounted only at `MANAGED_AUTH_VOLUME_PATH`.
- **FR-004**: The evidence MUST verify that `MANAGED_AUTH_VOLUME_PATH` equal to `codexHomePath` fails before container creation.
- **FR-005**: The evidence MUST verify that valid auth-volume startup seeds eligible auth entries one way into the per-run `CODEX_HOME` before Codex App Server starts.
- **FR-006**: The evidence MUST verify Docker-backed OAuth terminal auth runner and PTY bridge lifecycle behavior end to end.
- **FR-007**: Verification reports MUST change from ADDITIONAL_WORK_NEEDED only when passing Docker-backed evidence exists.
- **FR-008**: If Docker-backed evidence cannot be produced, verification reports MUST record the exact blocker without copying raw credentials, tokens, or sensitive environment dumps.
- **FR-009**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-363 and the original preset brief as traceability evidence.

### Key Entities

- **Docker-Enabled Verification Environment**: Runtime with Docker socket, compose dependencies, and local services needed to run the hermetic integration suite.
- **Managed Codex Session Launch Evidence**: Secret-free proof of workspace mount, auth-volume target, reserved environment, and pre-container validation behavior.
- **OAuth Terminal Runtime Evidence**: Secret-free proof that the auth runner and PTY/WebSocket bridge lifecycle runs against Docker.
- **Verification Report Update**: Changes to prior MoonSpec verification reports that either close ADDITIONAL_WORK_NEEDED with passing evidence or preserve it with an exact blocker.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `./tools/test_integration.sh` passes in a Docker-enabled environment or fails with a documented environment blocker before any closure is claimed.
- **SC-002**: Verification evidence covers workspace mounting, explicit auth-volume targeting, equality rejection, and one-way `CODEX_HOME` seeding.
- **SC-003**: Verification evidence covers Docker-backed OAuth terminal auth runner and PTY bridge lifecycle behavior.
- **SC-004**: Prior verification reports for specs 175, 180, and 183 are updated only with passing Docker-backed evidence or an exact blocker.
- **SC-005**: Verification output preserves `MM-363` and contains no raw credentials, token values, auth headers, private keys, or sensitive environment dumps.
