# MM-432 MoonSpec Orchestration Input

## Source

- Jira issue: MM-432
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Build bounded remediation context artifacts
- Labels: `moonmind-workflow-mm-a59f3b1d-da4d-4600-86a8-1d582ee67fe8`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-432 from MM project
Summary: Build bounded remediation context artifacts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-432 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-432: Build bounded remediation context artifacts

Source Reference
- Source Document: docs/Tasks/TaskRemediation.md
- Source Title: Task Remediation
- Source Sections:
  - 9. Evidence and context model
  - 14.1 Required remediation artifacts
  - 16. Failure modes and edge cases
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-011
  - DESIGN-REQ-019
  - DESIGN-REQ-022
  - DESIGN-REQ-023
  - DESIGN-REQ-024

User Story
As a remediation task, I receive a bounded artifact-backed context bundle for the target execution so diagnosis starts from durable evidence instead of unbounded logs or workflow history.

Acceptance Criteria
- A remediation task produces `reports/remediation_context.json` with `artifact_type` `remediation.context` before diagnosis begins.
- The artifact includes target `workflowId`/`runId`, selected steps, observability refs, bounded summaries, diagnosis hints, policy snapshots, lock policy snapshot, and live-follow cursor state when applicable.
- Large logs, diagnostics, provider snapshots, and evidence bodies remain behind artifact refs or observability refs rather than being embedded unbounded in the context artifact.
- Missing artifact refs, unavailable diagnostics, and historical merged-log-only runs produce explicit degraded evidence metadata without deadlocking the remediation task.
- The context builder never places presigned URLs, raw storage keys, absolute local filesystem paths, raw secrets, or secret-bearing config bundles in durable context.

Requirements
- Evidence access is artifact-first and bounded.
- The context bundle is the stable entrypoint for the remediation runtime.
- Partial evidence is represented as a bounded degradation, not an infinite wait.
- Artifact presentation and redaction contracts apply to context metadata and bodies.

Implementation Notes
- Preserve MM-432 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation evidence, context bundle shape, required remediation artifacts, and degradation behavior.
- Scope implementation to building the bounded `reports/remediation_context.json` artifact before diagnosis begins.
- Keep full logs, diagnostics, provider snapshots, evidence bodies, presigned URLs, raw storage keys, absolute local paths, and secret-bearing config bundles out of the durable context artifact.
- Represent missing artifact refs, unavailable diagnostics, historical merged-log-only evidence, and unavailable live follow as explicit bounded degradation metadata.
- Include target identity, selected steps, observability refs, compact diagnosis hints, action and approval policy snapshots, lock policy snapshot, and live-follow cursor state when applicable.
- Keep the context artifact as the stable entrypoint for remediation runtime evidence; richer evidence should remain reachable through typed artifact or observability refs.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-432 blocks MM-431, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-432 is blocked by MM-433, whose embedded status is Backlog.
