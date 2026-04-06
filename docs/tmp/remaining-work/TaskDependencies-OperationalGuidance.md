# Task Dependencies Operational Guidance

This note covers the operator-facing readiness items for task dependencies rollout.

## Current v1 contract

- A run may declare up to 10 direct prerequisite `workflowId` values in `payload.task.dependsOn`.
- Direct dependencies only: a dependent run waits on the exact prerequisite runs it declares.
- A dependency is satisfied only when the prerequisite reaches MoonMind terminal state `completed`.
- Any other terminal prerequisite outcome fails the dependent run immediately.

## UI and state expectations

- `/tasks/new` lets operators pick existing `MoonMind.Run` prerequisites without editing raw JSON.
- `/tasks/list` and `/tasks/{workflowId}` treat `waiting_on_dependencies` as a waiting state, not an executing state.
- Task detail surfaces both prerequisite runs and reverse-link dependents.

## Failure and remediation

- If a prerequisite fails, cancelation or timeout on the prerequisite propagates to the dependent as a dependency failure.
- Remediate the prerequisite first, then rerun or recreate the downstream run after the upstream dependency finishes in `completed`.
- Canceling a dependent run does not cancel any prerequisite runs.
- Pausing a dependent run does not stop dependency signal intake; prerequisite failures still terminate the dependent gate.

## Observability checks

- Filter `/tasks/list` by `waiting_on_dependencies` when investigating blocked runs.
- Use the dependency panels on task detail to identify the blocked prerequisite or downstream dependents.
- Service logs already emit dependency-resolution fan-out failures with the prerequisite and dependent workflow IDs.
