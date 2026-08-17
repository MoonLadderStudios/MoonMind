---
name: omnigent-execution-intent-3706
description: What was and wasn't implemented for the compiled Omnigent execution intent (#3706)
metadata:
  type: project
---

Issue MoonLadderStudios/MoonMind#3706 (Omnigent control plane 5/11) introduced
`CompiledOmnigentExecutionIntent` (schema `moonmind.omnigent.compiled-execution-intent/v1`).

Implemented (commit on branch `github-issue-implement-moonladderstudios-afbe27f2`):
- `moonmind/schemas/omnigent_execution_intent.py` — the contract + unknown-version compatibility policy.
- `moonmind/omnigent/execution_intent.py` — `compile_execution_intent` + `derive_execution_intent_from_request` (migration adapter) + `compile_remediation_authority`.
- Additive wiring in `moonmind/omnigent/profile_bound_execution.py` `OmnigentProfileBoundExecutionCoordinator.execute` (stage `execution_intent_compilation`), persisted as a lifecycle event keyed to the intent digest. The contract composes the existing `WorkspaceIntentRecord` and the effective-launch snapshot (`compile_effective_launch`), and pins the typed `RemediationLoopSpec` by digest.

Deliberately deferred (compatibility-sensitive; out of a bounded change; brief says histories keep their shape and the intent is mandatory only for new generations):
- Threading the intent ref+digest into the Temporal `MoonMind.AgentRun` (`AgentExecutionRequest`) and `MoonMind.AgentSession` (`CodexManagedSessionWorkflowInput`) payloads.
- Frontend type generation and submission-blocking UI; `annotations.remediationLoop` is still read in `frontend/src/entrypoints/workflow-detail.tsx` and `run.py` `_initialize_remediation_loop_controller` (the free-form path is only structurally closed at the admission compiler, not removed from the plan-annotation flow).
- Chat capability resolution (`workflow_chat_facade`/`effective_capabilities`) consuming the single compiled digest.

The #3684 class is closed at admission: a run declaring a remediation loop id whose controller mapping was stripped fails closed with `OMNIGENT_EXECUTION_INTENT_INCOMPLETE_AUTHORITY`.

Note: `tests/unit/omnigent/test_oauth_profile_lifecycle.py` has 28 pre-existing failures in this sandbox from unset `WORKFLOW_WORKSPACE_DAEMON_ROOT` (not related to this change).
