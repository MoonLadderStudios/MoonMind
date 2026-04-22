# MM-431 MoonSpec Orchestration Input

## Source

- Jira issue: MM-431
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Accept remediation create requests and persist target links
- Labels: `moonmind-workflow-mm-a59f3b1d-da4d-4600-86a8-1d582ee67fe8`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-431 from MM project
Summary: Accept remediation create requests and persist target links
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-431 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-431: Accept remediation create requests and persist target links

Source Reference
- Source Document: docs/Tasks/TaskRemediation.md
- Source Title: Task Remediation
- Source Sections:
  - 5.1 Remediation tasks remain MoonMind.Run
  - 5.2 Remediation is a relationship, not a dependency
  - 7. Submission contract
  - 8. Identity, linkage, and read models
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-024

User Story
As an operator, I can create a remediation task that targets another execution so MoonMind records an explicit troubleshooting relationship without treating the target as a dependency gate.

Acceptance Criteria
- POST /api/executions accepts a task.remediation payload and stores it as initialParameters.task.remediation before MoonMind.Run starts.
- target.workflowId is required and a concrete target.runId is resolved and persisted when omitted by the caller.
- Malformed self-reference, unsupported target workflow types, invalid taskRunIds, unsupported authorityMode values, incompatible actionPolicyRef, and disallowed nested remediation are rejected with structured errors.
- A remediation link record supports remediator-to-target and target-to-remediator lookup including mode, authorityMode, current status, pinned run identity, lock holder, latest action summary, and outcome fields.
- The convenience route expands into the same canonical create contract and does not introduce a second durable payload shape.

Requirements
- Remediation tasks remain MoonMind.Run executions with additional nested task.remediation semantics.
- Remediation links are relationships, not dependsOn gates, and start independently of target success.
- The system exposes inbound and outbound remediation lookup APIs for execution detail surfaces.
- Canonical source data remains upstream of any derived read model.

Implementation Notes
- Preserve MM-431 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation task semantics, submission contract, identity, linkage, and read model behavior.
- Scope implementation to accepting remediation create requests, persisting the canonical task.remediation payload, resolving target run identity, validating unsupported or malformed remediation submissions, and exposing durable remediation link lookup data.
- Keep remediation links as explicit troubleshooting relationships rather than dependency gates; target execution success must not be required for remediation task startup.
- Keep the convenience route as an expansion into the canonical create contract, not a second durable payload shape.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-431 is blocked by MM-432, whose embedded status is Backlog.
