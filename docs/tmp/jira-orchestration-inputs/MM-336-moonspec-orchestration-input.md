# MM-336 MoonSpec Orchestration Input

## Source

- Jira issue: MM-336
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [MM-318] Project operator-visible managed auth diagnostics
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-336 from MM board
Summary: [MM-318] Project operator-visible managed auth diagnostics
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-336 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

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
