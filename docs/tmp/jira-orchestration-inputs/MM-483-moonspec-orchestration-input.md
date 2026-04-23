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
