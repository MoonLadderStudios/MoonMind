# Temporal Worker Deployment Runbook

This document outlines the operational procedures for deploying updates to MoonMind Temporal workers safely, minimizing disruption to in-flight workflows.

## Version Registration

MoonMind uses the Temporal Python SDK's worker versioning APIs (`build_id` on the worker). Your **Temporal Server must support worker versioning** for the namespace where MoonMind runs; if the cluster is too old or the feature is disabled for that namespace, workers may fail to start—check server release notes and namespace settings before rolling this out.

When a worker starts, it registers a unique `build_id` from the `MOONMIND_BUILD_ID` environment variable. If not set, it falls back to the current Git SHA (or `"unknown"` if Git metadata is unavailable, e.g. in minimal images).

```bash
# Example deployment
export MOONMIND_BUILD_ID="v1.2.0-$(git rev-parse --short HEAD)"
docker compose up -d temporal-worker
```

Workers report their build ID to the Temporal cluster, allowing the system to route tasks to compatible workers.

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

## Rollback Procedure

If a deployed worker introduces critical bugs:

1. **Identify the previous stable Build ID:** Check the deployment history or Temporal UI for the last known good build ID.
2. **Revert the deployment:** Redeploy the previous container image and ensure `MOONMIND_BUILD_ID` matches the stable version.
3. **Monitor In-Flight Workflows:** Since the rollback restores previous workflow logic, any executions started on the buggy version may encounter non-determinism errors if their history diverged. You may need to manually terminate and restart affected workflows.
