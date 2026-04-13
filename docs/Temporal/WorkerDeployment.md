# Temporal Worker Deployment Runbook

This document outlines the operational procedures for deploying updates to MoonMind Temporal workers safely, minimizing disruption to in-flight workflows.

## Version Registration

MoonMind uses the Temporal Python SDK's worker versioning APIs through `WorkerDeploymentConfig`. Your **Temporal Server must support worker versioning** for the namespace where MoonMind runs; if the cluster is too old or the feature is disabled for that namespace, workers may fail to start, so check server release notes and namespace settings before rolling this out.

When worker versioning is enabled, a worker registers a deployment version using:

- deployment name: `moonmind-<worker-fleet>`
- build ID: `MOONMIND_BUILD_ID`, then the baked image metadata file, then the current Git SHA
- default behavior: `TEMPORAL_WORKER_VERSIONING_DEFAULT_BEHAVIOR`

If no build ID can be resolved while worker versioning is enabled, worker startup fails fast. Minimal images should set `MOONMIND_BUILD_ID` explicitly.
When worker versioning is disabled, MoonMind still passes an explicit `build_id`
to the Temporal worker to avoid the SDK's default module-hash build ID fallback.
If no configured or Git-derived build ID is available in disabled mode, the worker
uses `unknown`.

```bash
# Example deployment
export MOONMIND_BUILD_ID="v1.2.0-$(git rev-parse --short HEAD)"
export TEMPORAL_WORKER_VERSIONING_DEFAULT_BEHAVIOR="Auto-Upgrade"
docker compose up -d temporal-worker
```

Workers report their build ID to the Temporal cluster, allowing the system to route tasks to compatible workers.

`TEMPORAL_WORKER_VERSIONING_DEFAULT_BEHAVIOR` accepts:

- `Auto-Upgrade`: default for normal deployments and new executions.
- `Pinned`: use only for an explicit cutover window where started workflows must remain bound to a deployment version.
- `Disabled`: local escape hatch only. Worker startup fails unless
  `MOONMIND_ALLOW_DISABLED_TEMPORAL_WORKER_VERSIONING=1` is also set. Do not
  deploy incompatible `MoonMind.AgentSession` workflow-shape changes with
  versioning disabled.

## Compatibility Matrix

When modifying workflows, you must determine whether the changes are backwards-compatible:

| Change Type | Compatible? | Required Action |
| --- | --- | --- |
| Activity implementation (no signature change) | ✅ Yes | Normal deployment |
| Workflow change that alters **recorded** history (activity order, branching, new activities, different `workflow.patched` outcomes) | ❌ No | `workflow.patched` gate, worker/build routing, or new workflow type |
| Workflow refactor that is **replay-identical** (same commands/events; verify with replay tests) | ✅ Yes | Normal deployment after validation |
| Adding new Signals or Queries | ✅ Yes | Normal deployment |
| **Changing Activity signatures** | ❌ No | Requires new Activity name/version |
| **Changing Workflow name or parameters** | ❌ No | Register new Workflow type |

## Two-Version Side-by-Side Strategy

For backwards-incompatible workflow changes, the safest approach is a side-by-side deployment:

1. **Gate changes in code:** Use `workflow.patched("refactor-loop-1.2")` (or the current patch id in `moonmind/workflows/temporal/workflows/run.py`) to branch logic between the old and new behavior.
2. **Deploy new workers:** Deploy workers running the updated code alongside old workers. New executions will pick up the new worker version, while existing executions can safely replay against the patched logic.
3. **Wait for completion:** Wait for all workflows using the old logic to complete.
4. **Remove old logic:** In a subsequent release, remove the `workflow.patched` gate and the old logic.

Example (illustrative—real code lives on `MoonMindRunWorkflow`):
```python
if workflow.patched("refactor-loop-1.2"):
    _poll_terminal = True  # leave poll loop via terminal flag
else:
    self._resume_requested = True  # replay path for histories without the patch; cleared after the loop
```

## Agent Session Cutovers

`MoonMind.AgentSession` owns long-lived Codex managed-session state, so changes to handler names, update payloads, Continue-As-New payloads, search attributes, or cancel/terminate semantics require all of the following before rollout:

1. Keep worker versioning enabled on the workflow fleet.
2. Add or update a replay test for a representative `MoonMind.AgentSession` history.
3. Use `workflow.patched(...)` only for replay-sensitive command-shape transitions that must coexist with older histories.
4. For `SteerTurn`, Continue-As-New activation, cancel/terminate semantic changes, or new visibility metadata, deploy with `Auto-Upgrade` for normal rollout or `Pinned` for a bounded migration window, then remove any patch gate only after the oldest affected histories have completed or aged out.

CI runs `tools/validate_agent_session_deployment_safety.py` after the unit suite.
When deployment-sensitive AgentSession paths change, that gate requires worker
versioning, the managed-session replay test module, and the cutover playbook
topics to be present before rollout.

## Rollback Procedure

If a deployed worker introduces critical bugs:

1. **Identify the previous stable Build ID:** Check the deployment history or Temporal UI for the last known good build ID.
2. **Revert the deployment:** Redeploy the previous container image and ensure `MOONMIND_BUILD_ID` matches the stable version.
3. **Monitor In-Flight Workflows:** Since the rollback restores previous workflow logic, any executions started on the buggy version may encounter non-determinism errors if their history diverged. You may need to manually terminate and restart affected workflows.
