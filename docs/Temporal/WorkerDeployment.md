# Temporal Worker Deployment Runbook

## Overview
MoonMind utilizes Temporal for orchestrating workflows and background activities. 
As MoonMind's execution is Temporal-native, we rely on Temporal's standard mechanisms for versioning, ensuring backward compatibility, and providing safe deployment rollouts.

## Worker Versioning Registration
MoonMind workers inject a Build Identifier (`MOONMIND_BUILD_ID`) dynamically upon startup (defaulting to the Git SHA). 
When utilizing the Python SDK's versioning API (`use_worker_versioning=True`), this links the tasks being handled to the explicit version of the worker handling them.

## Compatibility Matrix
- When changing activity implementations, changes are generally backward-compatible unless they change the activity input or output schema.
- When changing workflow implementation shapes (loop conditions, activity call ordering, signal handling logic), it **must** be versioned. 

## Two-Version Side-By-Side Strategy for Workflow Changes
If a change modifies the workflow topology:
1. Wrap the new logic in a `workflow.patched("patch-name")` branch. The old logic runs in the `else` branch.
2. The deployed worker will register the patch, ensuring new executions follow the new path, and older replay executions follow the old logic to maintain determinism.
3. Once all older executions have completed, the patch and the old logic can be safely removed, finalizing the new default behavior.

## Rollback Procedure
If a regression is identified in the deployed workflow logic:
- A previous Docker image or Git SHA should be deployed immediately.
- Because Temporal workers execute deterministic code, tasks from newly started workflows will naturally route to the rolled-back worker pool once the new, erroneous build ID is removed.
- Use `MOONMIND_BUILD_ID` overrides if a specific rollback target is desired without a code redeploy.
