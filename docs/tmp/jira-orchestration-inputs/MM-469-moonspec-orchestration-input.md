# MM-469 MoonSpec Orchestration Input

## Source

- Jira issue: MM-469
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Register the curated security.pentest.run tool contract
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-469 from MM project
Summary: Register the curated security.pentest.run tool contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-469 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-469: Register the curated security.pentest.run tool contract

Source Reference
- Source document: `docs/Tools/PentestTool.md`
- Source title: Pentest Tool
- Source sections:
  - 2. Summary
  - 4. Locked decisions
  - 8. Tool model
  - 16. Activity contract
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-021

User Story
As a MoonMind operator, I need PentestGPT exposed as a curated executable tool so planners and task steps can request authorized pentest runs through a stable, non-generic contract.

Acceptance Criteria
- The registry contains `security.pentest.run` version `1.0.0` with type `skill` and executor `activity_type` `security.pentest.execute`.
- The tool schema requires `target`, `scope_artifact_ref`, `operation_mode`, and `runner_profile_id` and permits exact `execution_profile_ref` or `provider_selector`.
- The output schema includes `status`, `target`, `runner_profile_id`, `stdout`/`stderr`/`diagnostics`/`summary`/`findings` artifact refs, and finding counts.
- The tool policy sets conservative timeout values, `max_attempts = 1`, and the documented non-retryable error codes.
- The activity payload models are covered by tests for the real dispatcher or activity-wrapper invocation shape.

Requirements
- Expose PentestGPT only as `security.pentest.run` with `tool.type = skill`.
- Bind the tool to a curated activity rather than the generic executable-tool path when stronger controls are needed.
- Keep the tool contract stable if routing later moves to a dedicated security workload capability.
- Represent request and response data through typed Pydantic contracts suitable for workflow/activity boundaries.

Relevant Implementation Notes
- Preserve MM-469 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/PentestTool.md` as the source design reference for the curated PentestGPT executable tool contract.
- Register only the curated `security.pentest.run` tool contract; do not expose PentestGPT through a generic executable-tool path for this story.
- Keep the request and response contracts typed and suitable for workflow/activity boundaries.
- Include tests that exercise the real dispatcher or activity-wrapper invocation shape for the activity payload models.

Non-Goals
- Implementing the full PentestGPT runner execution path beyond the curated tool contract registration.
- Replacing the documented `security.pentest.run` contract with a generic executable-tool contract.
- Adding unstable aliases or compatibility wrappers for alternative pentest tool names.

Validation
- Verify the tool registry exposes `security.pentest.run` version `1.0.0` with `tool.type = skill` and executor `activity_type` `security.pentest.execute`.
- Verify the input schema requires `target`, `scope_artifact_ref`, `operation_mode`, and `runner_profile_id`.
- Verify the input schema accepts exact `execution_profile_ref` or `provider_selector`.
- Verify the output schema includes the required artifact refs and finding counts.
- Verify the tool policy uses conservative timeout values, `max_attempts = 1`, and the documented non-retryable error codes.
- Verify tests cover the real dispatcher or activity-wrapper invocation shape.

Needs Clarification
- None
