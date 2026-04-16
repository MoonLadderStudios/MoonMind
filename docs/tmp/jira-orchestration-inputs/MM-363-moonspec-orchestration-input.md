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
