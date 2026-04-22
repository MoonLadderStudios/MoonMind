# Feature Specification: Remediation Mutation Guards

**Feature Branch**: `230-remediation-mutation-guards`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-455 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-455-moonspec-orchestration-input.md`

## Original Preset Brief

```text
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
```

## User Story - Guard Remediation Mutations

**Summary**: As the orchestration platform, I want remediation mutation attempts guarded by exclusive locks, action-ledger idempotency, retry budgets, cooldowns, nested remediation limits, and target freshness checks so that remediation cannot conflict with another remediator, repeat destructive side effects, or act on stale target state.

**Goal**: A side-effecting remediation action can proceed only after the system confirms a valid mutation lock, a stable idempotency decision, budget/cooldown allowance, nested remediation policy, and fresh target precondition state.

**Independent Test**: Create linked remediation executions for one target, attempt concurrent and duplicate side-effecting actions, exhaust retry and cooldown limits, attempt self/nested remediation, and mutate the target between diagnosis and action; verify the system allows only the valid guarded action and records explicit no-op, rejection, re-diagnosis, or escalation reasons.

**Acceptance Scenarios**:

1. **Given** two remediation runs target the same execution, **When** both attempt a side-effecting mutation, **Then** only one run can hold the default exclusive `target_execution` mutation lock and the other receives an explicit non-mutating outcome.
2. **Given** a remediation run loses or cannot recover its mutation lock, **When** it attempts another side-effecting mutation, **Then** mutation is blocked and the lock-loss reason is surfaced instead of silently continuing.
3. **Given** a duplicate logical action request uses the same idempotency key and request shape, **When** it is evaluated again, **Then** the canonical action-ledger result is returned and the side effect is not repeated.
4. **Given** a remediation run exceeds its action budget or violates a cooldown for a repeated action kind, **When** another matching action is requested, **Then** the system denies or escalates the request with a terminal budget/cooldown reason.
5. **Given** a remediation task attempts to target itself, automatically spawn nested remediation, or target another remediation task without explicit policy, **When** the request is evaluated, **Then** it is rejected by default.
6. **Given** the target run or state materially changes after the pinned snapshot, **When** a side-effecting action is about to execute, **Then** the system re-reads bounded target health and no-ops, re-diagnoses, or escalates according to policy while recording the reason.

### Edge Cases

- Lock acquisition retries with the same remediation run and target return the same lock decision rather than creating multiple active locks.
- Expired locks can be recovered or replaced only through explicit stale-lock rules.
- Idempotency keys reused with materially different request shape are rejected or treated as separate non-duplicating decisions according to the ledger contract.
- Dry-run or read-only diagnosis does not require the exclusive mutation lock.
- Missing target run snapshots, missing idempotency keys, unavailable target health, and unknown target states fail closed or escalate with bounded reasons.
- Secret-bearing action parameters, storage details, and raw filesystem paths remain redacted in lock, ledger, budget, and target-change outputs.

## Assumptions

- The MM-455 story is a single runtime feature slice focused on mutation guardrails for remediation action execution, not on adding new action kinds or new remediation submission flows.
- Existing remediation create/link, evidence/context, authority, and action registry stories provide the surrounding remediation surfaces; this story adds the missing lock, ledger, budget, nested-remediation, and target freshness enforcement.
- The default v1 mutation lock scope is `target_execution` in exclusive mode, while other canonical lock scopes may be represented in contracts and validation without requiring every scope to be fully exercised by a production action.

## Source Design Requirements

- **DESIGN-REQ-009** (`docs/Tasks/TaskRemediation.md` section 6): Remediation tasks target one logical execution and one pinned run snapshot, keep evidence server-mediated, require typed allowlisted actions, preserve idempotent side effects, require exclusive mutation locking, keep secrets redacted, disable nested remediation by default, and bound unresolved evidence. Scope: in scope, mapped to FR-001 through FR-018.
- **DESIGN-REQ-014** (`docs/Tasks/TaskRemediation.md` sections 12.1 through 12.3): Side-effecting remediation needs mutation locks with canonical scopes, default exclusive `target_execution` behavior, idempotent acquisition, stale lock recovery, explicit lock loss, and no silent mutation after lock loss. Scope: in scope, mapped to FR-004 through FR-009.
- **DESIGN-REQ-015** (`docs/Tasks/TaskRemediation.md` section 12.4): Every remediation action request must provide a stable idempotency key and the remediation action ledger is the canonical duplicate-suppression surface. Scope: in scope, mapped to FR-010 through FR-012.
- **DESIGN-REQ-016** (`docs/Tasks/TaskRemediation.md` sections 12.5 and 12.6): Remediation must enforce retry budgets, cooldowns, terminal escalation, disabled automatic nested remediation, self-target rejection, explicit nested-remediation policy, and default self-healing depth of 1. Scope: in scope, mapped to FR-013 through FR-017.
- **DESIGN-REQ-022** (`docs/Tasks/TaskRemediation.md` sections 9.7 and 12.7): Before side-effecting action execution, remediation must re-read bounded target health and compare pinned run, current run, current state, target summary, and session identity to decide no-op, re-diagnosis, or escalation when the target changed. Scope: in scope, mapped to FR-018 through FR-022.
- **DESIGN-REQ-023** (`docs/Tasks/TaskRemediation.md` sections 6 and 12): Remediation must degrade, escalate, fail, or deny with bounded reasons instead of entering infinite waits or raw-access fallbacks when locks, evidence, budgets, target state, or action preconditions cannot be resolved. Scope: in scope, mapped to FR-023 through FR-025.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST preserve remediation target identity as one logical target execution and one pinned target run snapshot before evaluating side-effecting remediation actions.
- **FR-002**: The system MUST keep evidence and target health access server-mediated through authorized refs, bounded read models, or typed observability views during mutation-guard evaluation.
- **FR-003**: Side-effecting remediation actions MUST be typed and allowlisted before mutation guard evaluation can produce an executable result.
- **FR-004**: The system MUST require an exclusive mutation lock before any side-effecting remediation action mutates a shared target.
- **FR-005**: The default v1 mutation lock MUST use `target_execution` scope with `exclusive` mode.
- **FR-006**: Lock records MUST identify lock ID, scope, target workflow/run, holder workflow/run, creation time, expiration time, and mode.
- **FR-007**: Lock acquisition for the same logical holder and target MUST be idempotent.
- **FR-008**: Stale locks MUST expire or be recoverable through explicit rules before another holder mutates the same target.
- **FR-009**: A remediation run that loses its mutation lock MUST receive an explicit non-mutating outcome and MUST NOT silently continue mutating.
- **FR-010**: Every side-effecting remediation action request MUST include an idempotency key stable for the logical intended side effect.
- **FR-011**: The remediation action ledger MUST be the canonical idempotency surface for action requests and MUST NOT rely only on generic execution update idempotency caches.
- **FR-012**: Duplicate action requests with the same remediation workflow, idempotency key, action kind, target, dry-run state, and request shape MUST return the canonical ledger result without repeating side effects.
- **FR-013**: The system MUST enforce a bounded maximum number of side-effecting actions per remediation target.
- **FR-014**: The system MUST enforce a bounded maximum number of attempts per action kind.
- **FR-015**: The system MUST enforce a minimum cooldown between repeated identical action requests.
- **FR-016**: Exhausted retry budgets or cooldown violations MUST produce explicit denial or terminal escalation outcomes.
- **FR-017**: Remediation tasks MUST NOT automatically spawn remediation, target themselves, or target another remediation task unless policy explicitly enables nested remediation.
- **FR-018**: Automatic self-healing depth MUST default to 1.
- **FR-019**: Before a side-effecting action executes, the system MUST re-read the target's current bounded health view.
- **FR-020**: The pre-action freshness check MUST compare pinned target run ID, current target run ID, current target state, current target summary, and session identity.
- **FR-021**: If the target materially changed since the pinned snapshot, action execution MUST no-op, re-diagnose, or escalate according to policy and record the reason.
- **FR-022**: Missing or unavailable current bounded health MUST produce a bounded non-mutating outcome rather than an unbounded wait or stale action.
- **FR-023**: Mutation guard outputs MUST preserve secret redaction, audit boundaries, and secret-reference rules even under elevated remediation authority.
- **FR-024**: Unsupported raw host, SQL, Docker, volume, network, secret-reading, and redaction-bypass paths MUST remain outside the mutation guard path and MUST NOT be used as fallbacks.
- **FR-025**: Durable lock, ledger, budget, cooldown, nested-remediation, and target-change decisions MUST preserve MM-455 traceability through spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Remediation Mutation Lock**: A bounded exclusive claim on a target scope that identifies target workflow/run, holder workflow/run, mode, creation time, expiration time, and recovery behavior.
- **Action Ledger Entry**: The canonical duplicate-suppression record for one logical remediation action request and its decision/result.
- **Retry Budget**: A policy-bound counter that limits total side-effecting actions for a target and attempts per action kind.
- **Cooldown Window**: A policy-bound minimum interval before the same logical action can be attempted again.
- **Target Freshness Snapshot**: A bounded comparison between the pinned target state and current target health used before side-effecting action execution.
- **Nested Remediation Policy**: The policy decision controlling whether remediation can target itself, target another remediation task, or spawn another remediation task.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove concurrent side-effecting mutation attempts for one target allow only one active default exclusive `target_execution` lock holder.
- **SC-002**: Tests prove lock acquisition is idempotent, stale lock recovery is explicit, and lock loss prevents silent continued mutation.
- **SC-003**: Tests prove duplicate side-effecting action requests with the same idempotency key and request shape return the canonical action-ledger result without repeating side effects.
- **SC-004**: Tests prove missing idempotency keys or unsafe idempotency reuse fail closed before side effects.
- **SC-005**: Tests prove action budgets, per-kind attempt limits, and cooldown windows deny or escalate repeated destructive actions with explicit reasons.
- **SC-006**: Tests prove self-targeting, automatic nested remediation, and remediation-to-remediation targets are rejected by default.
- **SC-007**: Tests prove pre-action target freshness checks re-read bounded target health and no-op, re-diagnose, or escalate when the target run, state, summary, or session identity changed.
- **SC-008**: Tests prove missing current target health produces a bounded non-mutating outcome instead of unbounded waiting or stale mutation.
- **SC-009**: Tests prove durable lock, ledger, budget, cooldown, and target-change outputs preserve redaction boundaries.
- **SC-010**: Traceability verification confirms MM-455 and DESIGN-REQ-009, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022, and DESIGN-REQ-023 are preserved in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
