# MM-453 MoonSpec Orchestration Input

## Source

- Jira issue: MM-453
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enforce remediation authority modes, permissions, and redaction boundaries
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-453 from MM project
Summary: Enforce remediation authority modes, permissions, and redaction boundaries
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-453 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-453: Enforce remediation authority modes, permissions, and redaction boundaries

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 10. Security and authority model
  - 6. Core invariants
  - 4. Non-goals
- Coverage IDs:
  - DESIGN-REQ-010
  - DESIGN-REQ-011
  - DESIGN-REQ-024

User Story
As a platform security owner, I can configure remediation authority through explicit modes, permissions, and named security profiles so privileged troubleshooting never implies raw host access or secret disclosure.

Acceptance Criteria
- `observe_only` remediation can read evidence and produce diagnoses but cannot execute side-effecting actions.
- `approval_gated` remediation can propose or dry-run actions but requires recorded approval before side effects.
- `admin_auto` remediation can execute only allowlisted actions within policy and still respects high-risk approval rules.
- Audit records include both requesting user/workflow and the execution principal used for privileged actions.
- A user with target view permission alone cannot launch admin remediation or approve high-risk actions.
- Generated context, logs, summaries, diagnostics, and artifacts do not contain raw secrets or durable raw storage/file access material.

Requirements
- Authority is policy/profile based, not implicit root or ordinary runtime access.
- Unauthorized direct fetches do not leak execution existence.
- Secret redaction rules apply even to admin remediation.

Relevant Implementation Notes
- Preserve MM-453 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation authority modes, permission boundaries, redaction behavior, and non-goals.
- Model remediation authority with explicit modes: `observe_only`, `approval_gated`, and `admin_auto`.
- Elevated remediation must execute through a named admin remediation principal or security profile rather than the ordinary user runtime.
- Distinguish permissions to view a target execution, create a remediation task, request an admin remediation profile, approve high-risk actions, and inspect remediation audit history.
- Keep administrative actions typed and allowlisted; remediation must not imply arbitrary host shell access, Docker daemon access, SQL/database editing, or any command the model suggests.
- Keep evidence, artifact, and log access server-mediated through refs, read capability handles, redacted previews, or typed observability APIs.
- Never place raw secrets, presigned storage URLs, storage backend keys, absolute local filesystem paths, or raw secret-bearing config bundles in durable remediation context, logs, summaries, diagnostics, or artifacts.
- Ensure side-effecting remediation actions are idempotent or safely keyed, and keep high-risk actions subject to explicit approval policy even under `admin_auto`.
- Preserve redaction-safe visibility: unauthorized direct fetches must not leak execution existence, and admin remediation artifacts must still follow redaction-safe display rules.

Non-Goals
- Arbitrary raw shell access on the MoonMind host.
- Unrestricted Docker daemon access from the remediation runtime.
- Arbitrary SQL or direct database row editing by the agent.
- Silent import of another task's entire workflow history into `initialParameters`.
- Cross-task managed-session reuse.
- Treating every failed task as automatically eligible for an admin healer.
- Bypassing the Secrets System or artifact redaction rules.
- Treating Live Logs as the source of truth or as a control channel.

Validation
- Verify each supported authority mode enforces its side-effect permissions: `observe_only`, `approval_gated`, and `admin_auto`.
- Verify side-effecting actions require recorded approval when policy or risk classification requires it.
- Verify audit records include both the requesting user or workflow and the execution principal/security profile used for privileged actions.
- Verify target view permission alone is insufficient to launch admin remediation or approve high-risk actions.
- Verify generated context, logs, summaries, diagnostics, and artifacts redact raw secrets and do not expose durable raw storage or filesystem access material.
- Verify unauthorized direct fetches do not leak execution existence.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-453 blocks MM-452, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-453 is blocked by MM-454, whose embedded status is Backlog.

Needs Clarification
- None
