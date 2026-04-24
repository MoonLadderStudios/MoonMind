# Feature Specification: Finish Task Remediation Desired-State Implementation

**Feature Branch**: `242-finish-task-remediation`
**Created**: 2026-04-23
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-483 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `spec.md` (Input)

## Original Preset Brief

```text
# MM-483 MoonSpec Orchestration Input

## Source

- Jira issue: MM-483
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Finish Task Remediation desired-state implementation
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-483 from MM project
Summary: Finish Task Remediation desired-state implementation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-483 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-483: Finish Task Remediation desired-state implementation

User Story
As a MoonMind operator, I need the Task Remediation system described in `docs/Tasks/TaskRemediation.md` to be fully implemented end to end so remediation tasks can safely diagnose, act on, audit, and verify target executions without raw host access or unbounded loops.

Context
A code audit found that MoonMind already implements several remediation foundations: canonical `task.remediation` submission and pinned links, context artifacts, typed evidence reads, Mission Control relationship panels, authority decisions, and in-memory mutation guard decisions. The system is not complete against `docs/Tasks/TaskRemediation.md` because action execution, canonical action coverage, durable locks/ledgers, lifecycle integration, target-side summaries, and automatic policy-bounded self-healing remain missing or partial.

Source Documents
- `docs/Tasks/TaskRemediation.md`
- `specs/232-remediation-lifecycle-audit/tasks.md`
- `moonmind/workflows/temporal/remediation_actions.py`
- `moonmind/workflows/temporal/remediation_context.py`
- `moonmind/workflows/temporal/remediation_tools.py`
- `moonmind/workflows/temporal/service.py`
- `api_service/api/routers/executions.py`
- `frontend/src/entrypoints/task-detail.tsx`

Current Implemented Baseline
- `task.remediation` is accepted and normalized under `initialParameters.task.remediation`.
- Remediation target `workflowId`/`runId` links are persisted and queryable inbound/outbound.
- `reports/remediation_context.json` can be generated and linked.
- Evidence tools can read declared artifacts/logs and gate live follow.
- Authority/profile decisions and mutation guard decisions exist.
- Mission Control can show remediation relationships, evidence artifacts, and approval state.

Missing Work / Acceptance Criteria
1. Canonical action registry coverage exists for the documented action kinds: `execution.pause`, `execution.resume`, `execution.request_rerun_same_workflow`, `execution.start_fresh_rerun`, `execution.cancel`, `execution.force_terminate`, `session.interrupt_turn`, `session.clear`, `session.cancel`, `session.terminate`, `session.restart_container`, `provider_profile.evict_stale_lease`, `workload.restart_helper_container`, and `workload.reap_orphan_container`. Unsupported raw host, Docker, SQL, storage, and secret-read capabilities remain absent.
2. Accepted remediation actions execute through MoonMind-owned control-plane services or owning subsystem adapters, not through raw shell/Docker/SQL access. Each action declares target type, inputs, risk tier, preconditions, idempotency key behavior, verification requirements, and audit payload shape.
3. The remediation evidence/tool surface includes end-to-end `execute_action` and `verify_target` behavior. Action request, action result, before/after state refs, side effects, and verification outcomes are persisted as remediation artifacts.
4. Exclusive mutation locks and the remediation action ledger are durable across process restarts, Temporal retries, worker restarts, and replay-sensitive paths. They are no longer only in-memory service fields.
5. Create-time validation verifies selected `taskRunIds` belong to the target execution or selected steps. Malformed or foreign `taskRunIds` fail before workflow start with structured validation errors.
6. Remediation lifecycle integration is complete: `collecting_evidence`, `diagnosing`, `awaiting_approval`, `acting`, `verifying`, `resolved`, `escalated`, and `failed` are reflected in summaries/read models without replacing top-level `MoonMind.Run` state.
7. Required remediation artifacts are produced automatically by the runtime path, not only by helper calls/tests: `remediation.context`, `remediation.plan`, `remediation.decision_log`, `remediation.action_request`, `remediation.action_result`, `remediation.verification`, and `remediation.summary`.
8. Target-side linkage summaries are exposed through execution/task-run read models, including active remediation count, latest remediation title/status, latest action kind, active lock holder/scope, outcome, and updated time.
9. Cancellation, failure, and Continue-As-New behavior is implemented at the service/workflow boundary: remediation cancellation does not mutate the target except already-requested actions, final summary publication and lock release are attempted, and target workflow/run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor are preserved across continuation.
10. Action request/result output from `RemediationActionAuthorityService` is integrated with lifecycle artifact publication and audit evidence.
11. Target-managed session and workload mutations preserve subsystem-native continuity/control artifacts in addition to remediation audit artifacts.
12. Automatic self-healing, if enabled, is policy-driven and bounded: no implicit spawn-on-failure, max active remediations enforced, nested remediation off by default, depth limits enforced, and proposal/immediate modes are explicit.
13. Partial historical evidence, missing artifact refs, unavailable live follow, lock conflicts, stale leases already released, gone containers, unsafe force termination attempts, and remediator failure all produce bounded degraded/no-op/escalated/failed outcomes rather than silent success or infinite waits.
14. Mission Control exposes the completed action/approval/verification lifecycle without raw storage paths, presigned URLs, or secret-bearing data.
15. Tests cover workflow/activity or adapter boundaries for action execution, durable locks/ledger, `taskRunId` ownership validation, lifecycle artifacts, target-side read models, cancellation/failure, Continue-As-New, and Mission Control rendering. Run at minimum: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and `./tools/test_integration.sh` when artifact lifecycle/API routes change.

Out of Scope
- Raw admin console access.
- Direct host shell, arbitrary SQL, arbitrary Docker daemon operations, arbitrary storage-key reads, or secret redaction bypasses.
- Replacing `MoonMind.Run` with a new top-level remediation workflow type unless the canonical doc is intentionally updated first.

Implementation Notes
- Preserve Constitution compatibility requirements for Temporal-facing payloads. Any activity/workflow/update/signal shape change must be compatibility-safe or have an explicit cutover plan.
- Existing partial implementation should be reused where correct, but compatibility shims for superseded internal contracts should not be added.
- The current action authority module explicitly says it does not execute host/container/SQL/provider/storage operations; this story must add the safe execution boundary through owning services, not by weakening that guardrail.

Needs Clarification
- None
```

## User Story - Complete Task Remediation Runtime

**Summary**: As a MoonMind operator, I want remediation tasks to diagnose, act on, audit, and verify target executions through bounded MoonMind control-plane capabilities so that failed or stuck tasks can be repaired without raw host access or unbounded self-healing loops.

**Goal**: Task Remediation reaches the desired runtime contract for action coverage, safe action execution, durable mutation coordination, lifecycle artifacts, target-side summaries, bounded self-healing policy, Mission Control visibility, and verification evidence.

**Independent Test**: Create or simulate remediation runs for target executions that cover action execution, evidence collection, approval/action/verification lifecycle, durable locks and ledgers, target-side read models, cancellation/failure, continuation, degraded evidence, and Mission Control rendering; then verify every required artifact, summary field, audit record, and bounded outcome is present without exposing raw host/storage/secret access.

**Acceptance Scenarios**:

1. **Given** a remediation run requests a documented action kind, **When** the action is accepted, **Then** it is validated against the canonical action registry and executed only through a MoonMind-owned control-plane service or owning subsystem adapter.
2. **Given** a remediation action is requested or attempted, **When** operators inspect remediation evidence, **Then** action request, action result, before/after state refs, side effects, verification outcome, and audit metadata are persisted as remediation artifacts.
3. **Given** a remediation task acts on a shared target, **When** Temporal retries, worker restarts, or process restarts occur, **Then** exclusive mutation locks and the action ledger remain durable and prevent conflicting duplicate mutations.
4. **Given** a remediation request includes selected task run bindings, **When** the task is created, **Then** the platform rejects malformed or foreign `taskRunIds` before workflow start with structured validation errors.
5. **Given** a remediation run progresses from evidence collection through action and verification, **When** summaries and read models are inspected, **Then** lifecycle phases, required remediation artifacts, target-side linkage summaries, and Mission Control state are complete without replacing top-level `MoonMind.Run` state.
6. **Given** a remediation task is canceled, fails, or continues as new, **When** the workflow boundary is inspected, **Then** it avoids new target mutation except already-requested actions, attempts final summary publication and lock release, and preserves target identity, context, lock, ledger, approval, retry, and live-follow cursor state.
7. **Given** automatic self-healing is enabled by policy, **When** a target fails, stalls, or requests attention, **Then** remediation creation remains bounded by explicit trigger mode, active-count limits, nested-remediation defaults, depth limits, and proposal/immediate behavior.
8. **Given** evidence or target state is partial, stale, unsafe, or unavailable, **When** remediation proceeds, **Then** the system produces bounded degraded, no-op, escalated, failed, or denied outcomes rather than silent success or infinite waits.

### Edge Cases

- Unsupported raw host, Docker, SQL, storage, or secret-read action requests are rejected rather than routed through generic agent execution.
- Stale leases, gone containers, unsafe force termination attempts, missing artifact refs, unavailable live follow, lock conflicts, and failed preconditions produce explicit bounded results.
- Managed-session and workload mutations preserve subsystem-native continuity or control artifacts in addition to remediation audit artifacts.
- Historical targets with only partial evidence remain diagnosable when safe and mark unavailable evidence classes explicitly.
- Automatic remediation of remediation tasks remains off by default unless policy explicitly allows bounded nesting.

## Assumptions

- The MM-483 brief is a single runtime feature request because it has one operator-facing outcome and explicit non-goals, even though it spans backend, workflow, and Mission Control surfaces.
- Existing MM-451, MM-454, MM-456, MM-457, and MM-482 remediation foundations provide partial behavior; this story completes the remaining desired-state runtime contract rather than reworking those completed slices.
- The source implementation document `docs/Tasks/TaskRemediation.md` is treated as runtime source requirements because the selected mode is runtime.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` sections 5 through 7): Remediation remains a normal `MoonMind.Run` with nested `task.remediation`, a non-dependency target relationship, server-mediated evidence access, and typed allowlisted administrative actions. Scope: in scope, mapped to FR-001, FR-002, FR-006, FR-007, FR-022, and FR-034.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` sections 7.3 through 7.6): Create-time validation requires target identity, pinned run resolution, task-run ownership validation, authority/action policy validation, bounded nesting checks, and optional automatic self-healing policy. Scope: in scope, mapped to FR-008 through FR-011 and FR-028 through FR-031.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` sections 8 through 9): Remediation links, context artifacts, evidence refs, live-follow state, and target-side read models must remain durable and inspectable in both directions. Scope: in scope, mapped to FR-012, FR-013, FR-021 through FR-024, and FR-032.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` sections 10 through 12): Remediation actions must use a canonical registry, bounded action kinds, authority decisions, explicit approvals, safe execution boundaries, idempotency keys, verification requirements, and durable exclusive locks. Scope: in scope, mapped to FR-001 through FR-007, FR-014 through FR-020, and FR-033.
- **DESIGN-REQ-005** (`docs/Tasks/TaskRemediation.md` sections 13 through 14): Runtime lifecycle phases, required remediation artifacts, final summaries, target-side continuity artifacts, and control-plane audit events must be produced automatically and remain queryable. Scope: in scope, mapped to FR-021 through FR-027 and FR-035.
- **DESIGN-REQ-006** (`docs/Tasks/TaskRemediation.md` sections 15 through 17): Security boundaries, degradation behavior, loop prevention, cancellation/failure semantics, continuation preservation, and Mission Control presentation must fail closed and avoid secret/raw access exposure. Scope: in scope, mapped to FR-028 through FR-038.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose canonical registry coverage for `execution.pause`, `execution.resume`, `execution.request_rerun_same_workflow`, `execution.start_fresh_rerun`, `execution.cancel`, `execution.force_terminate`, `session.interrupt_turn`, `session.clear`, `session.cancel`, `session.terminate`, `session.restart_container`, `provider_profile.evict_stale_lease`, `workload.restart_helper_container`, and `workload.reap_orphan_container`.
- **FR-002**: The remediation action registry MUST reject unsupported raw host, Docker, SQL, arbitrary storage, and secret-read capabilities.
- **FR-003**: Every accepted remediation action kind MUST declare target type, inputs, risk tier, preconditions, idempotency key behavior, verification requirements, and audit payload shape.
- **FR-004**: Accepted remediation actions MUST execute through MoonMind-owned control-plane services or owning subsystem adapters rather than raw shell, Docker daemon, SQL, storage-key, or secret access.
- **FR-005**: The remediation evidence/tool surface MUST support end-to-end `execute_action` and `verify_target` behavior.
- **FR-006**: Action request, action result, before/after state refs, side effects, and verification outcomes MUST be persisted as remediation artifacts.
- **FR-007**: Action request/result output from `RemediationActionAuthorityService` MUST be integrated with lifecycle artifact publication and audit evidence.
- **FR-008**: Create-time validation MUST verify selected `taskRunIds` belong to the target execution or selected steps.
- **FR-009**: Malformed or foreign `taskRunIds` MUST fail before workflow start with structured validation errors.
- **FR-010**: Automatic self-healing, when enabled, MUST be policy-driven and bounded by explicit trigger mode, active remediation limits, nested remediation defaults, depth limits, and proposal/immediate behavior.
- **FR-011**: The system MUST NOT implicitly spawn remediation tasks on failure unless enabled by explicit policy.
- **FR-012**: Exclusive mutation locks for remediation acting MUST be durable across process restarts, Temporal retries, worker restarts, and replay-sensitive paths.
- **FR-013**: The remediation action ledger MUST be durable across process restarts, Temporal retries, worker restarts, and replay-sensitive paths.
- **FR-014**: Duplicate action attempts under retry or replay MUST be idempotent or safely keyed to avoid duplicate destructive effects.
- **FR-015**: Lock conflicts MUST produce explicit bounded outcomes and MUST NOT silently allow conflicting mutations.
- **FR-016**: Target-managed session and workload mutations MUST preserve subsystem-native continuity/control artifacts in addition to remediation audit artifacts.
- **FR-017**: Stale leases that are already released MUST produce a no-op or degraded outcome rather than silent success.
- **FR-018**: Missing or gone workload containers MUST produce a bounded no-op, escalated, or failed outcome.
- **FR-019**: Unsafe force termination attempts MUST be denied or escalated with a bounded reason.
- **FR-020**: The system MUST preserve Constitution compatibility requirements for Temporal-facing payloads through compatible workflow/activity/update/signal shapes or an explicit cutover plan.
- **FR-021**: Remediation lifecycle integration MUST expose collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, and failed in summaries/read models without replacing top-level `MoonMind.Run` state.
- **FR-022**: Required remediation artifacts MUST be produced automatically by the runtime path for context, plan, decision log, action request, action result, verification, and summary.
- **FR-023**: Required remediation artifacts MUST not rely only on helper calls or tests to appear in normal runtime paths.
- **FR-024**: Target-side linkage summaries MUST expose active remediation count, latest remediation title/status, latest action kind, active lock holder/scope, outcome, and updated time through execution/task-run read models.
- **FR-025**: Mission Control MUST expose completed action, approval, verification, artifact, and target-linkage lifecycle state without raw storage paths, presigned URLs, or secret-bearing data.
- **FR-026**: Control-plane audit evidence MUST identify action kind, risk tier, actor or execution principal, target workflow/run identity, remediation workflow/run identity, approval decision when relevant, timestamps, and bounded metadata.
- **FR-027**: Target-side read models MUST allow operators to inspect remediation relationships without parsing deep artifact bodies.
- **FR-028**: Cancellation MUST NOT mutate the target except for actions already requested before cancellation.
- **FR-029**: Cancellation, failure, and terminal completion MUST attempt final summary publication and lock release when possible.
- **FR-030**: Continue-As-New behavior MUST preserve target workflow/run identity, remediation context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.
- **FR-031**: Remediator failure MUST produce a bounded failed or escalated outcome rather than silent success or infinite waits.
- **FR-032**: Partial historical evidence, missing artifact refs, unavailable live follow, and unavailable evidence classes MUST be recorded as degraded evidence.
- **FR-033**: Precondition failures MUST produce explicit no-op, degraded, escalated, or failed outcomes rather than silent success.
- **FR-034**: Remediation tasks MUST never receive presigned URLs, raw storage keys, raw local filesystem paths, raw secrets, or unredacted secret-bearing data as durable context.
- **FR-035**: Mission Control and API presentation MUST show remediation lifecycle state while preserving artifact preview/redaction rules.
- **FR-036**: Tests MUST cover workflow/activity or adapter boundaries for action execution, durable locks/ledger, `taskRunId` ownership validation, lifecycle artifacts, target-side read models, cancellation/failure, Continue-As-New, and Mission Control rendering.
- **FR-037**: The implementation MUST preserve the Jira issue key MM-483 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- **FR-038**: Existing partial implementation SHOULD be reused where correct, but compatibility shims for superseded internal contracts MUST NOT be added.

### Key Entities

- **Remediation Action Definition**: A typed action registry entry describing kind, target type, inputs, risk tier, preconditions, idempotency behavior, verification requirements, and audit shape.
- **Remediation Action Request**: The validated request to execute a typed remediation action against a target through an owning subsystem.
- **Remediation Action Result**: The durable outcome of an attempted action including side effects, before/after refs, verification status, and bounded failure or no-op reason.
- **Remediation Mutation Lock**: A durable exclusive lock that prevents conflicting remediation mutations against a shared target.
- **Remediation Action Ledger**: A durable record of requested, attempted, skipped, no-op, denied, failed, or verified remediation actions.
- **Remediation Runtime Artifact Set**: The automatically produced context, plan, decision log, action request, action result, verification, and summary artifacts.
- **Target-Side Remediation Summary**: Compact execution/task-run read-model metadata for inbound remediations, lock state, latest action, outcome, and update time.
- **Self-Healing Policy**: Bounded policy controlling if, when, and how remediation tasks are automatically proposed or created.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove every documented remediation action kind is registered with required metadata and unsupported raw capabilities are rejected.
- **SC-002**: Tests prove accepted actions execute through owning control-plane or subsystem boundaries and persist action request/result/verification artifacts.
- **SC-003**: Tests prove mutation locks and action ledgers remain durable and idempotent across retry or restart simulations.
- **SC-004**: Tests prove malformed or foreign `taskRunIds` fail at create time with structured validation errors.
- **SC-005**: Tests prove runtime paths automatically publish required remediation artifacts and lifecycle summaries without relying only on helper calls.
- **SC-006**: Tests prove target-side read models expose remediation counts, latest status/action, lock scope, outcome, and updated time.
- **SC-007**: Tests prove cancellation, remediator failure, and Continue-As-New preserve or publish the required state and avoid new target mutation after cancellation except already-requested actions.
- **SC-008**: Tests prove self-healing is disabled unless explicit policy enables bounded proposal or immediate behavior.
- **SC-009**: Tests prove partial evidence, missing artifacts, unavailable live follow, stale leases, gone containers, unsafe force termination, lock conflicts, and failed preconditions produce bounded degraded/no-op/escalated/failed outcomes.
- **SC-010**: Mission Control tests prove operators can inspect action, approval, verification, artifact, and target-linkage lifecycle state without raw paths, presigned URLs, or secret-bearing data.
