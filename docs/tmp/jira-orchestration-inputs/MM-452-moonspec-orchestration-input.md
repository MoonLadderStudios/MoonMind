# MM-452 MoonSpec Orchestration Input

## Source

- Jira issue: MM-452
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Build bounded artifact-first remediation evidence bundles and tools
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-452 from MM board
Summary: Build bounded artifact-first remediation evidence bundles and tools
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-452 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-452: Build bounded artifact-first remediation evidence bundles and tools

User Story
As a remediation runtime, I receive a bounded MoonMind-owned evidence bundle and typed evidence tools so I can diagnose a target execution without scraping UI pages or embedding unbounded logs in workflow history.

Source Document
docs/Tasks/TaskRemediation.md

Source Title
Task Remediation

Source Sections
- 9. Evidence and context model
- 5.3 Control remains separate from observation
- 6. Core invariants

Coverage IDs
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-022
- DESIGN-REQ-023

Acceptance Criteria
- A remediation run receives a reports/remediation_context.json artifact containing the specified v1 schema fields and artifact_type remediation.context.
- Full logs and diagnostics remain behind refs or typed read APIs; durable context contains only bounded summaries/excerpts.
- Evidence tools can read referenced artifacts/logs through normal artifact and task-run policy checks.
- Live follow is available only when target state, taskRunId support, and policy allow it; cursor state survives retries where possible.
- When live follow is unavailable, the remediator can still diagnose from merged/stdout/stderr logs, diagnostics, summaries, and artifacts with evidence degradation recorded.
- Before any side-effecting action request is submitted, the runtime re-reads current target health and target-change guard inputs.

Requirements
- The context builder is the stable entrypoint for target evidence.
- Live logs are observation only and never the source of truth or control channel.
- Missing evidence degrades the task rather than causing unbounded waits.

Implementation Notes
- Preserve MM-452 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Scope the implementation to bounded artifact-first remediation evidence bundles and typed evidence tool access.
- Use existing task remediation, artifact, live log, diagnostics, task-run policy, and guard-input surfaces where possible.
- Do not scrape UI pages, embed unbounded logs in workflow history, or treat live logs as a source of truth or control channel.

Needs Clarification
- None
