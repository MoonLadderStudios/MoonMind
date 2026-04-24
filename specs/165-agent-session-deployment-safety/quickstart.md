# Quickstart: Agent Session Deployment Safety

## Scope Guard

This is runtime-mode work. A valid implementation includes production runtime code changes plus validation tests. Documentation, specifications, and checklists are supporting artifacts only.

Use test-driven sequencing: add or update the focused validation that proves a runtime gap before treating the production runtime change as complete.

The delayed standalone-image path is out of scope.

## 1. Audit Current Runtime Surfaces

Use targeted searches before editing so completed behavior is preserved rather than duplicated.

```bash
rg -n "@workflow.update|@workflow.signal|control_action|SteerTurn|InterruptTurn|TerminateSession|CancelSession|continue_as_new|all_handlers_finished" moonmind/workflows/temporal/workflows/agent_session.py
rg -n "steer_turn|interrupt_turn|cancel_session|terminate_session|clear_session|reconcile" moonmind/workflows/temporal/runtime
rg -n "ManagedSessionReconcile|schedule" moonmind/workflows/temporal
rg -n "SearchAttribute|upsert|set_current_details|summary|heartbeat|ApplicationError" moonmind/workflows/temporal
```

## 2. Implement or Close Gaps

Focus changes in the existing runtime modules:

```text
moonmind/workflows/temporal/workflows/agent_session.py
moonmind/workflows/temporal/workflows/managed_session_reconcile.py
moonmind/workflows/temporal/workflows/run.py
moonmind/workflows/temporal/activity_runtime.py
moonmind/workflows/temporal/client.py
moonmind/workflows/temporal/worker_runtime.py
moonmind/workflows/temporal/workers.py
moonmind/workflows/temporal/runtime/codex_session_runtime.py
moonmind/workflows/temporal/runtime/managed_session_controller.py
moonmind/workflows/temporal/runtime/managed_session_store.py
moonmind/workflows/temporal/runtime/managed_session_supervisor.py
moonmind/schemas/managed_session_models.py
```

Do not move runtime side effects into workflow code. Keep workflow payloads compact and deterministic.

## 3. Run Focused Validation During Iteration

Use focused tests for the managed session workflow and runtime boundary.

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only \
  tests/unit/workflows/temporal/workflows/test_agent_session.py \
  tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py \
  tests/unit/workflows/temporal/test_agent_session_replayer.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py \
  tests/unit/workflows/temporal/test_client_schedules.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/services/temporal/runtime/test_codex_session_runtime.py \
  tests/unit/services/temporal/runtime/test_managed_session_controller.py
```

If a change touches local Temporal integration behavior, run the workflow lifecycle test directly:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py -q
```

If a change touches an `integration_ci` seam, run the hermetic integration suite:

```bash
./tools/test_integration.sh
```

## 4. Verify Sensitive Content Does Not Enter Bounded Surfaces

Review matches manually; a match is a prompt for inspection, not automatically a failure.

```bash
rg -n "prompt|transcript|scrollback|raw log|password=|token=|ghp_|github_pat_|AKIA|AIza" \
  moonmind/workflows/temporal \
  tests/unit/workflows/temporal \
  tests/integration/services/temporal/workflows
```

Bounded workflow metadata, Search Attributes, summaries, telemetry dimensions, schedules, and replay fixtures must only contain compact identifiers, statuses, booleans, and artifact refs.

## 5. Run Final Required Verification

Before completing implementation, run the full required unit suite from the repository root:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

For workflow-shape changes, also run the managed-session replay validation and confirm patching or an explicit cutover is present before rollout.

For deployment-safety review, validate the cutover playbook and replay gates together:

```bash
SPECIFY_FEATURE=165-agent-session-deployment-safety \
  ./tools/validate_agent_session_deployment_safety.py --base-ref origin/main
rg -n "SteerTurn|Continue-As-New|CancelSession|TerminateSession|Search Attribute|replay-safe rollout|replay" \
  docs/ManagedAgents/AgentSessionDeploymentSafetyCutover.md \
  specs/165-agent-session-deployment-safety/contracts/agent-session-deployment-safety.md \
  specs/165-agent-session-deployment-safety/tasks.md
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py \
  tests/unit/workflows/temporal/test_agent_session_replayer.py \
  tests/unit/workflows/temporal/workflows/test_agent_session.py
```

## 6. Completion Checklist

- Production runtime code was changed or verified as already compliant for each relevant requirement.
- Tests cover workflow boundary, runtime/controller behavior, lifecycle cleanup, cancel/terminate distinction, steer/interrupt, idempotency/races, Continue-As-New, reconcile, and replay safety.
- No prompts, transcripts, scrollback, raw logs, credentials, secrets, or unbounded provider output are introduced into bounded operational surfaces.
- Patching or a cutover plan protects incompatible durable workflow changes.
- The standalone-image path remains untouched.
