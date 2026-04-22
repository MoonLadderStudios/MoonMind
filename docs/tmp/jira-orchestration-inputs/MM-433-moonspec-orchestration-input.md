# MM-433 MoonSpec Orchestration Input

## Source

- Jira issue: MM-433
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose typed evidence tools and live follow for remediators
- Labels: `moonmind-workflow-mm-a59f3b1d-da4d-4600-86a8-1d582ee67fe8`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-433 from MM project
Summary: Expose typed evidence tools and live follow for remediators
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-433 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-433: Expose typed evidence tools and live follow for remediators

Source Reference
- Source Document: docs/Tasks/TaskRemediation.md
- Source Title: Task Remediation
- Source Sections:
  - 9.5 Evidence access surface for remediation tasks
  - 9.6 Live follow semantics
  - 9.7 Evidence freshness before action
  - 15.5 Live follow behavior
- Coverage IDs:
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-011
  - DESIGN-REQ-022
  - DESIGN-REQ-024

User Story
As a remediation runtime, I can read target evidence and optionally follow live observability through MoonMind-owned typed tools so I never scrape UI pages or treat live logs as authoritative state.

Acceptance Criteria
- Remediation runtimes can retrieve the parsed remediation.context bundle and read referenced target artifacts through normal artifact policy.
- Target logs are readable or tail-able through typed task-run observability APIs with bounded tailLines and cursor inputs.
- Live follow starts only when the target run is active, the selected taskRunId supports it, and policy permits it.
- Disconnects, worker restarts, and task retries resume from durable sequence cursor state when possible.
- When structured live history is unavailable, the system falls back to merged logs, stdout/stderr logs, diagnostics, summaries, or artifact tailing.
- Before any side-effecting action is requested, the remediator re-reads current target health and records the precondition evidence.

Requirements
- Evidence access is server-mediated through typed MoonMind-owned surfaces.
- Live follow is additive and best effort, never the only evidence path.
- Fresh target health must be checked before mutation.
- Runtime capabilities do not include raw storage, filesystem, or Mission Control scraping access.

Implementation Notes
- Preserve MM-433 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation evidence access surfaces, live follow semantics, freshness checks before action, and live follow behavior.
- Scope implementation to typed evidence retrieval and live observability access for remediation runtimes.
- Provide typed access for retrieving the parsed `remediation.context` bundle and reading referenced target artifacts through normal artifact policy.
- Expose target logs through bounded typed task-run observability APIs with `tailLines` and cursor inputs.
- Start live follow only when the target run is active, the selected taskRunId supports it, and policy permits it.
- Persist or propagate durable sequence cursor state so disconnects, worker restarts, and task retries can resume when possible.
- Fall back to merged logs, stdout/stderr logs, diagnostics, summaries, or artifact tailing when structured live history is unavailable.
- Require remediators to re-read current target health and record precondition evidence before requesting side-effecting actions.
- Do not grant remediation runtimes raw storage, filesystem, or Mission Control scraping access.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-433 blocks MM-432, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-433 is blocked by MM-434, whose embedded status is Selected for Development.
