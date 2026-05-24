# MM-730 Hard Switch Cutover Release Note

MoonMind no longer exposes Tasks as a product/runtime concept. Use Workflow Execution, workflowId, runId, and Step Execution.

This is a breaking release. Compatibility redirects and task-shaped aliases are not kept after the cutover boundary. Operators must keep the previous worker build draining on the legacy Task Queue for in-flight `MoonMind.Run` histories, or record an explicit pause/resume or terminate/restart decision before enabling `TEMPORAL_USER_WORKFLOW_CONTRACT_MODE=renamed_contract`.

The renamed-contract worker serves `MoonMind.UserWorkflow` on a distinct Task Queue. A worker build must not register both `MoonMind.Run` and `MoonMind.UserWorkflow`.
