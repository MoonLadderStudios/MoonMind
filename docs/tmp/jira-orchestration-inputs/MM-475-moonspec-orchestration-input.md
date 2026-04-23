# MM-475 MoonSpec Orchestration Input

## Source

- Jira issue: MM-475
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Handle PentestGPT failure, cancellation, progress, and cleanup conservatively
- Labels: `moonmind-workflow-mm-f8e211f2-108c-4071-ac6b-4b3cea8ff6a9`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-475 from MM project
Summary: Handle PentestGPT failure, cancellation, progress, and cleanup conservatively
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-475 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-475: Handle PentestGPT failure, cancellation, progress, and cleanup conservatively

Source Reference
- Source document: `docs/Tools/PentestTool.md`
- Source title: Pentest Tool
- Source sections:
  - 16.3 Heartbeats and progress
  - 17. Failure, retry, cancellation, and cleanup
- Coverage IDs:
  - DESIGN-REQ-017
  - DESIGN-REQ-021
  - DESIGN-REQ-022

User Story
As a platform operator, I need PentestGPT failures, cancellation, retries, heartbeats, partial artifacts, leases, and orphan containers handled deterministically so security workloads do not repeat unsafe actions or leak resources.

Acceptance Criteria
- Default execution has max_attempts = 1 and does not automatically rerun post-interaction failures.
- Allowed retry exceptions are limited to explicit pre-interaction infrastructure failures such as image pull, container start, provider slot timeout before launch, or transient infrastructure faults before first runtime action.
- Structured heartbeats cover all documented phases and Live Logs receive operator-visible progress annotations where available.
- Cancellation and timeout paths capture remaining logs/diagnostics, publish useful partial evidence, release provider leases, and remove the workload container.
- Orphan cleanup finds containers by the deterministic PentestGPT labels defined in the runner story.

Requirements
- Keep retries conservative because security testing may be non-idempotent.
- Release all acquired resources on every terminal path.
- Make failure diagnostics structured enough for operators to understand whether a retry is safe.

Relevant Implementation Notes
- Preserve MM-475 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/PentestTool.md` as the source design reference for PentestGPT heartbeat/progress metadata, retry policy, allowed retry exceptions, cancellation behavior, timeout handling, partial evidence publication, provider lease release, container removal, and orphan cleanup.
- Default PentestGPT execution must use `max_attempts = 1`.
- Do not automatically rerun failures after meaningful target interaction; security testing may be non-idempotent and duplicate execution can create unsafe or confusing side effects.
- Permit retries only for explicit pre-interaction infrastructure failures, such as image pull failure, container start failure, provider-profile slot timeout before launch, or transient infrastructure fault before first runtime action.
- Ensure retry exceptions remain explicit and observable rather than implicit fallback behavior.
- Emit structured heartbeat phase metadata for `validating_scope`, `waiting_for_profile_slot`, `materializing_inputs`, `launching_container`, `running`, `publishing_artifacts`, `normalizing_findings`, and `cleanup`.
- Emit operator-visible progress annotations to Live Logs or the artifact-backed progress stream where that stream is available.
- On task cancel, step cancel, or timeout, best-effort terminate the workload gracefully and escalate to kill after the grace period.
- Cancellation and timeout handling must capture remaining stdout/stderr and diagnostics, publish useful partial evidence, release the provider-profile lease, and remove the workload container.
- Orphan cleanup must discover and clean PentestGPT containers using deterministic names and labels defined by the runner/container story.
- Failure diagnostics must distinguish whether the failure happened before or after meaningful target interaction so operators can decide whether a manual retry is safe.

Non-Goals
- Automatically retrying intrusive or post-interaction PentestGPT failures.
- Hiding partial evidence, remaining logs, or diagnostics when cancellation or timeout occurs.
- Leaving provider-profile leases or workload containers behind on terminal paths.
- Treating broad infrastructure or provider errors as retryable unless they are explicit pre-interaction exceptions.
- Replacing the PentestGPT artifact, evidence, normalized finding, provider selection, or runner launch contracts owned by adjacent stories.

Validation
- Verify default PentestGPT execution has max_attempts = 1.
- Verify post-interaction failures are non-retryable by default.
- Verify allowed retry exceptions are limited to explicit pre-interaction infrastructure failures.
- Verify structured heartbeats cover `validating_scope`, `waiting_for_profile_slot`, `materializing_inputs`, `launching_container`, `running`, `publishing_artifacts`, `normalizing_findings`, and `cleanup`.
- Verify Live Logs or artifact-backed progress streams receive operator-visible progress annotations where available.
- Verify cancellation and timeout paths capture remaining stdout/stderr and diagnostics.
- Verify cancellation and timeout paths publish useful partial evidence when available.
- Verify provider-profile leases are released on every terminal path.
- Verify workload containers are removed on cancellation, timeout, and failure paths.
- Verify orphan cleanup finds PentestGPT containers by deterministic names and labels from the runner/container story.
- Verify failure diagnostics make it clear whether a retry is safe because no meaningful target interaction occurred.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-475 blocks MM-474, whose embedded status is Code Review.
- No trusted Jira issue link indicates another issue blocks MM-475 at fetch time.

Needs Clarification
- None
