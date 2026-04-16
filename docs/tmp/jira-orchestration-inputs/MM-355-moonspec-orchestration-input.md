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
