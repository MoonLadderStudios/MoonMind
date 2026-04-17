# MM-398 MoonSpec Orchestration Input

## Source

- Jira issue: MM-398
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Jira orchestrate should not proceed if the issue is marked as blocked by another issue that is not done yet
- Labels: None
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, `description`, acceptance criteria, or implementation notes.

## Canonical MoonSpec Feature Request

Jira issue: MM-398 from MM project
Summary: Jira orchestrate should not proceed if the issue is marked as blocked by another issue that is not done yet
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-398 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-398: Jira orchestrate should not proceed if the issue is marked as blocked by another issue that is not done yet

Source Reference
- Source Document: api_service/data/task_step_templates/jira-orchestrate.yaml
- Source Title: Jira Orchestrate preset
- Source Sections:
  - Load Jira preset brief
  - Classify request and resume point
  - Create or select Moon Spec
  - Implement the task breakdown
  - Create pull request
  - Move Jira issue to Code Review
- Related Design:
  - specs/173-jira-orchestrate-preset/spec.md
  - docs/Tools/JiraIntegration.md

User Story
As a Jira Orchestrate operator, I want orchestration to stop before implementation when the requested Jira issue is blocked by another Jira issue that is not done, so dependent work does not start before its prerequisite is complete.

Acceptance Criteria
- Jira Orchestrate performs a trusted Jira dependency preflight after fetching the target issue and before MoonSpec implementation work starts.
- If the target issue is marked as blocked by one or more linked Jira issues whose status is not Done, orchestration stops before MoonSpec specify/plan/tasks/implement, pull request creation, and Code Review transition.
- The blocked outcome reports the target issue key, each blocking issue key available from the trusted Jira response, and each blocking issue status available from the trusted Jira response.
- If all blocking issues are Done, or the issue has no blocker links, Jira Orchestrate proceeds with the existing MoonSpec lifecycle unchanged.
- The guard uses the trusted Jira tool surface and Jira issue-link metadata; it does not scrape Jira, hardcode transition IDs, or infer blocker state from prompt text alone.
- Existing Jira Orchestrate behavior remains unchanged for moving the target issue to In Progress before implementation and moving it to Code Review only after a confirmed pull request URL exists.

Requirements
- Add a Jira blocker/dependency preflight to the Jira Orchestrate flow before implementation work can begin.
- Detect blocker links from the trusted `jira.get_issue` response shape available to managed agents.
- Treat linked blocker issues as satisfied only when their Jira status is Done.
- Fail closed when a blocker link is present but the linked blocker issue status cannot be determined through trusted Jira data.
- Preserve MM-398 in all downstream MoonSpec artifacts, verification output, commit text, and pull request metadata.
- Keep the change within the trusted Jira boundary; do not introduce raw Jira credential use in agent shells or client-side scraping.

Relevant Implementation Notes
- The seeded preset definition currently lives at `api_service/data/task_step_templates/jira-orchestrate.yaml`.
- The original Jira Orchestrate preset behavior is documented in `specs/173-jira-orchestrate-preset/spec.md`.
- The current seeded flow transitions the issue to In Progress, loads the Jira preset brief, runs MoonSpec orchestration, creates a pull request, and moves the issue to Code Review.
- The new guard should run after the target issue is fetched and before any MoonSpec implementation stage can start.
- The guard should inspect Jira issue links that represent blocking relationships. In Jira's common blocker-link wording, this means the target issue is "is blocked by" another issue; implementation should use the normalized or raw link metadata returned by the trusted Jira tool rather than relying on a single display string when structured fields are available.
- When blocker issue status is not embedded in the first `jira.get_issue` response, the flow may fetch the linked blocker issue through trusted `jira.get_issue` before deciding whether to proceed.
- A non-Done blocker should produce a deterministic blocked result, not a generic failure.
- The blocked result should be operator-readable and should not transition MM-398 to Code Review or create a pull request.
- The trusted Jira fetch for MM-398 at brief-build time showed no description, acceptance criteria, implementation notes, labels, or exposed preset-brief fields; this synthesized brief is therefore intentionally scoped to the issue summary and relevant existing Jira Orchestrate behavior.

Verification
- Verify a Jira Orchestrate run for an issue with an unresolved blocker stops before MoonSpec specify/plan/tasks/implement.
- Verify the blocked output includes the target issue key, blocker issue key, and blocker status when available.
- Verify a Jira Orchestrate run for an issue whose blockers are all Done proceeds through the existing MoonSpec lifecycle.
- Verify a Jira Orchestrate run for an issue with no blocker links proceeds as it did before this change.
- Verify the guard uses trusted Jira tool calls and does not require raw Atlassian credentials in the agent runtime.
- Verify existing Jira Orchestrate tests for In Progress transition, MoonSpec lifecycle, pull request creation, and Code Review transition still pass or are updated to include the new preflight step.
- Preserve MM-398 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- None exposed by the trusted MM-398 Jira issue response at fetch time.
