# MM-455 MoonSpec Orchestration Input

## Source

- Jira issue: MM-455
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Guard remediation mutations with locks, idempotency, budgets, and loop prevention
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-455 from MM project
Summary: Guard remediation mutations with locks, idempotency, budgets, and loop prevention
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-455 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-455: Guard remediation mutations with locks, idempotency, budgets, and loop prevention

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 12. Locking, idempotency, and loop prevention
  - 6. Core invariants
  - 9.7 Evidence freshness before action
- Coverage IDs:
  - DESIGN-REQ-009
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-016
  - DESIGN-REQ-022
  - DESIGN-REQ-023

User Story
As the orchestration platform, I prevent conflicting or runaway remediation by requiring mutation locks, action-ledger idempotency, cooldowns, retry budgets, nested remediation limits, and target-change checks.

Acceptance Criteria
- Only one active remediator can hold the default exclusive `target_execution` mutation lock for a target.
- Duplicate logical action requests with the same idempotency key return the canonical ledger result rather than repeating side effects.
- Lock expiration, recovery, and lock loss are explicit; a remediator does not silently continue mutating after losing a lock.
- Retry budgets and cooldowns prevent repeated destructive actions and produce terminal escalation when exhausted.
- Self-targeting and automatic nested remediation are rejected by default.
- If the target materially changed since the pinned snapshot, action execution no-ops, re-diagnoses, or escalates according to policy and records the reason.

Requirements
- The remediation action ledger is the canonical idempotency surface for actions.
- Concurrent diagnosis may be allowed later, but concurrent mutation is not allowed by default.
- Loop prevention is mandatory for manual and future automatic remediation.

Relevant Implementation Notes
- Preserve MM-455 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation locking, action-ledger idempotency, retry/cooldown budgets, nested remediation defaults, target-change guards, core invariants, and evidence freshness.
- Mark remediation tasks explicitly with the canonical `payload.task.remediation` marker.
- Target exactly one logical execution and one pinned run snapshot; require `target.workflowId` and persist the resolved `target.runId` at create time.
- Keep remediation non-transitive by default; one remediation task must not automatically inherit authority over another remediation task's target.
- Keep evidence access server-mediated through artifact refs or observability APIs, not presigned URLs, raw storage keys, raw local filesystem paths, or unbounded workflow-history payloads.
- Require idempotent or safely keyed behavior for every side-effecting action so replays, retries, and duplicate requests do not repeat destructive effects.
- Require exclusive locking for shared target mutation; diagnosis may become concurrent later, but mutation may not.
- Implement canonical lock scopes for `target_execution`, `task_run`, `managed_session`, `provider_profile_lease`, and `workload_container`.
- Use `target_execution` with `exclusive` mode as the default v1 mutation lock.
- Lock records should capture lock identity, scope, target workflow/run, holder workflow/run, creation and expiration timestamps, and mode.
- Lock acquisition must be idempotent, stale locks must expire or be recoverable, and lock loss must be surfaced explicitly before any further mutation.
- Require each remediation action request to carry an idempotency key stable for the logical intended side effect.
- Use the remediation action ledger as the canonical idempotency surface rather than relying only on generic execution update idempotency caches.
- Carry bounded retry and cooldown controls such as max actions per target, max attempts per action kind, minimum cooldown between repeated identical actions, and terminal escalation conditions.
- Prevent automatic nested remediation by default: remediation tasks may not automatically spawn remediation, target themselves, or target another remediation task unless policy explicitly enables nested remediation.
- Default automatic self-healing depth to 1.
- Before executing a side-effecting action, re-read the target's current bounded health view and compare pinned target `runId`, current target `runId`, current target state, current target summary, and session identity.
- If the target materially changed, action execution should no-op, re-diagnose, or escalate to approval according to policy and record the reason.
- Preserve secret redaction, audit, and secret-reference rules even when remediation has stronger task authority.
- Treat force termination as high-risk and keep unsupported raw host, SQL, Docker, volume, network, secret-reading, and redaction-bypass actions outside the mutation path.

Non-Goals
- Concurrent mutation of the same target by multiple remediators.
- Repeating side effects for duplicate logical action requests.
- Silent mutation after lock loss.
- Infinite remediation loops or automatic self-remediation by default.
- Acting on stale target assumptions without a fresh precondition check.
- Using generic execution update idempotency as the only action idempotency control.
- Unbounded evidence imports into workflow history.
- Raw host shell, arbitrary SQL, arbitrary Docker, decrypted secret reads, or redaction bypasses.

Validation
- Verify only one active remediator can hold the default exclusive `target_execution` mutation lock for a target.
- Verify lock acquisition is idempotent, stale locks expire or can be recovered, and lock loss prevents further silent mutation.
- Verify duplicate logical action requests with the same idempotency key return the canonical action-ledger result without repeating side effects.
- Verify retry budgets and cooldowns block repeated destructive actions and produce terminal escalation when exhausted.
- Verify remediation tasks cannot target themselves, automatically spawn nested remediation, or target another remediation task unless explicitly enabled by policy.
- Verify side-effecting action execution re-reads the current bounded health view and handles material target changes by no-op, re-diagnosis, or escalation with a recorded reason.
- Verify evidence access remains server-mediated and does not embed presigned URLs, raw storage keys, raw filesystem paths, or unbounded logs in workflow history.
- Verify secret redaction and audit rules still apply under elevated remediation authority.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-455 blocks MM-454, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-455 is blocked by MM-456, whose embedded status is Backlog.

Needs Clarification
- None
