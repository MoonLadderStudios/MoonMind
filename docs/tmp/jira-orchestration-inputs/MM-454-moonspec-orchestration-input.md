# MM-454 MoonSpec Orchestration Input

## Source

- Jira issue: MM-454
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Provide a typed remediation action registry with audited request and result contracts
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-454 from MM project
Summary: Provide a typed remediation action registry with audited request and result contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-454 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-454: Provide a typed remediation action registry with audited request and result contracts

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 11. Remediation action registry
  - 10.6 High-risk actions
  - 17. Recommended v1
- Coverage IDs:
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-023
  - DESIGN-REQ-024

User Story
As a remediation task, I can request only typed, allowlisted administrative actions and receive durable request/result artifacts with risk, precondition, idempotency, verification, and audit data.

Acceptance Criteria
- `list_allowed_actions` returns only policy-compatible typed action kinds with risk and input metadata.
- `execute_action` validates action kind, target class, inputs, risk policy, preconditions, and idempotency key before invoking owning control-plane code.
- Action request artifacts include `schemaVersion`, `actionId`, `actionKind`, `requester`, target workflow/run/resource, `riskTier`, `dryRun`, `idempotencyKey`, and bounded params.
- Action result artifacts include status, `appliedAt` when applicable, before/after refs, `verificationRequired`, `verificationHint`, and bounded `sideEffects`.
- Unsupported raw host, SQL, Docker, volume, network, secret-reading, or redaction-bypass operations fail fast and are audited as rejected.
- V1 registry scope matches the recommended small action set unless a supported action is unavailable, in which case it is omitted rather than exposed as raw access.

Requirements
- Administrative actions are MoonMind-owned typed capabilities.
- Managed-session and Docker workload actions go through their owning control planes.
- Every side-effecting action declares risk and verification requirements.

Relevant Implementation Notes
- Preserve MM-454 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation action registry behavior, high-risk action handling, and recommended v1 scope.
- Expose remediation actions through MoonMind-owned typed capabilities such as `remediation.list_allowed_actions()` and `remediation.execute_action(actionKind, params, dryRun?)`; do not require the remediation runtime to use UI scraping or raw administrative access.
- Model registry entries as allowlisted action kinds with explicit target type, allowed inputs, risk tier, preconditions, idempotency rules, verification requirements, and audit payload shape.
- Include the recommended v1 action set where supported: `execution.pause`, `execution.resume`, `execution.request_rerun_same_workflow`, `session.interrupt_turn`, `session.clear`, `provider_profile.evict_stale_lease`, and `session.restart_container` only when implemented through the owning plane.
- Keep `execution.start_fresh_rerun`, `execution.cancel`, `execution.force_terminate`, `session.cancel`, `session.terminate`, `workload.restart_helper_container`, and `workload.reap_orphan_container` aligned with the canonical registry semantics if they are included or deferred.
- Treat high-risk actions with explicit `low`, `medium`, or `high` risk tiers, and let approval policy decide whether each action is auto-allowed, requires operator approval, or is disabled.
- Request artifacts must use a durable v1 contract that records schema version, action identity, action kind, requester, target workflow/run/resource, risk, dry-run mode, idempotency key, and bounded parameters.
- Result artifacts must use a durable v1 contract that records status, application timestamp when applicable, before/after refs, verification requirement, verification hint, and bounded side effects.
- Supported result status values should include `applied`, `no_op`, `rejected`, `precondition_failed`, `approval_required`, `timed_out`, and `failed`.
- Reject unsupported raw host shell, arbitrary SQL, arbitrary Docker image/run, arbitrary volume mount, arbitrary network egress change, decrypted secret read, and redaction-bypass requests as typed audited rejections.
- Ensure side-effecting action execution validates action kind, target class, input shape, risk policy, preconditions, idempotency key, and ownership/control-plane routing before invoking the owning service.
- Verify action effects through target-specific checks such as lease state after stale lease eviction, session identity and continuity boundary after container restart, and target run state/runId rollover after same-workflow rerun requests.

Non-Goals
- Arbitrary raw host shell access.
- Arbitrary SQL or direct database editing by the remediation runtime.
- Arbitrary Docker daemon, image, volume, or network access.
- Reading decrypted secret contents.
- Bypassing artifact redaction rules.
- Exposing unsupported actions as raw access fallbacks.

Validation
- Verify `list_allowed_actions` returns only policy-compatible typed actions with risk and input metadata for the active policy/profile.
- Verify `execute_action` rejects unsupported action kinds, wrong target classes, malformed inputs, failed preconditions, missing/duplicate unsafe idempotency keys, and policy-disallowed risk tiers before invoking owning control-plane code.
- Verify action request artifacts include the required v1 fields and keep params bounded.
- Verify action result artifacts include the required v1 fields, bounded side effects, and verification guidance.
- Verify high-risk actions require approval or are disabled according to policy.
- Verify unsupported raw host, SQL, Docker, volume, network, secret-reading, and redaction-bypass requests fail fast and are audited as rejected.
- Verify managed-session and Docker workload actions route through their owning control planes rather than direct host/runtime access.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-454 blocks MM-453, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-454 is blocked by MM-455, whose embedded status is Backlog.

Needs Clarification
- None
