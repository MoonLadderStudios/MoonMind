# MM-451 MoonSpec Orchestration Input

## Source

- Jira issue: MM-451
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Accept canonical task remediation submissions with pinned target linkage
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-451 from MM board
Summary: Accept canonical task remediation submissions with pinned target linkage
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-451 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-451: Accept canonical task remediation submissions with pinned target linkage

User Story
As an operator, I can create a remediation task for a target MoonMind execution and have the platform persist an explicit non-dependency relationship to the target workflow and pinned run snapshot.

Source Document
docs/Tasks/TaskRemediation.md

Source Title
Task Remediation

Source Sections
- 1. Purpose
- 2. Why a separate system is required
- 5. Architectural stance
- 6. Core invariants
- 7. Submission contract
- 8. Identity, linkage, and read models

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-005

Acceptance Criteria
- Given a valid remediation request, the created MoonMind.Run contains the canonical task.remediation object and starts without waiting for target success.
- When runId is omitted, the backend resolves and stores the current target runId before the run starts.
- Malformed self-targets, unsupported targets, invalid authority modes, invisible targets, invalid taskRunIds, or incompatible policies are rejected with structured errors.
- The remediation relationship is visible from remediation-to-target and target-to-remediation read paths including pinned run identity and status fields.
- POST /api/executions/{workflowId}/remediation expands to the same canonical create contract as POST /api/executions.

Requirements
- Remediation is modeled separately from dependsOn and never as a success gate.
- Canonical payload storage is nested under task.remediation.
- The link record supports forward lookup, reverse lookup, current remediation status, lock holder, action summary, final outcome, and Mission Control/API rendering.

Implementation Notes
- Preserve MM-451 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Scope the implementation to accepting canonical remediation submissions and persisting explicit pinned target linkage.
- Use existing execution creation, task remediation payload, target run resolution, authorization, validation, and read-model surfaces where possible.
- Do not model remediation as a dependency gate or wait for target success before starting the remediation run.

Needs Clarification
- None
