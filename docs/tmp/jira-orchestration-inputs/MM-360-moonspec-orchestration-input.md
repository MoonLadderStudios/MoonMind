# MM-360 MoonSpec Orchestration Input

## Source

- Jira issue: MM-360
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Workload Auth-Volume Guardrails
- Labels: `mm-318`, `moonspec-breakdown`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-360 from MM project
Summary: Workload Auth-Volume Guardrails
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-360 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-360: Workload Auth-Volume Guardrails

MoonSpec Story ID: STORY-006

Short Name
workload-auth-guardrails

User Story
As a security reviewer, I can verify that Docker-backed workload containers launched near managed sessions do not implicitly inherit managed-runtime auth volumes.

Acceptance Criteria
- A workload launch requested from a managed Codex session inherits no managed-runtime auth volume by default.
- Ordinary workspace/cache workload mounts proceed without auth volumes.
- Credential mounts without explicit approval are rejected with policy metadata.
- Mission Control and APIs do not present workload containers as the managed Codex session identity.

Requirements
- Enforce workload profile mount allowlists.
- Reject implicit managed-runtime auth-volume inheritance.
- Require explicit justification/profile declaration for any credential mount.
- Keep workload containers separate from managed session identity fields.

Independent Test
Launch workload profiles from a simulated managed-session-assisted step and assert auth-volume mounts are rejected unless explicitly declared by approved workload policy.

Dependencies
- None specified.

Risks
- Approved credential mount policy should stay narrow enough for future workload-specific exceptions.

Out of Scope
- Managed Codex session container launch itself.
- OAuth terminal enrollment.
- Specialized workload runner internals beyond mount policy.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 2. Scope
- 4. Volume Targeting Rules
- 9. Security Model
- 11. Required Boundaries

Coverage IDs
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-009: Do not make Docker workload containers inherit managed-runtime auth volumes by default.
- DESIGN-REQ-010: Never place raw credential contents in workflow history, logs, artifacts, or UI responses.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Needs Clarification
- None
