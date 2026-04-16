# Research: Merge Gate

## Workflow Boundary

Decision: Add a distinct `MoonMind.MergeGate` workflow that is started by `MoonMind.Run` only after a pull request is confirmed and merge automation is enabled.
Rationale: The parent implementation run should complete independently of external review latency, while the merge gate can wait, receive signals, poll, and expose blockers without keeping implementation work in a running state.
Alternatives considered: Keeping the gate inside `MoonMind.Run` was rejected because it couples implementation completion to review latency and bloats parent workflow history. Launching pr-resolver immediately after PR creation was rejected because it can race CI and automated review providers.

## Readiness Evaluation

Decision: Evaluate GitHub checks, automated review completion, optional Jira status, PR closed/stale revision state, and policy denial through activities that return compact readiness evidence.
Rationale: Temporal workflows must remain deterministic and should carry compact state only. Activity-backed evaluation also keeps provider-specific calls behind integration boundaries.
Alternatives considered: Reading GitHub/Jira directly in workflow code was rejected as nondeterministic. Storing full check runs, comments, or review bodies in workflow state was rejected because it risks large Temporal histories and secret leakage.

## Resolver Launch

Decision: Create the resolver follow-up as a separate `MoonMind.Run` whose task selects the `pr-resolver` skill and sets publish mode to `none`.
Rationale: pr-resolver already owns git/PR mutation and merge behavior. Running it as a normal MoonMind execution preserves skill boundaries, observability, and artifact handling while avoiding a second publish step.
Alternatives considered: Calling pr-resolver logic directly from the merge gate was rejected because skills are executed inside MoonMind runs. Creating a new resolver-specific workflow was rejected because it duplicates existing `MoonMind.Run` skill execution semantics.

## Duplicate Prevention

Decision: Store resolver launch state in the merge gate workflow and use deterministic workflow IDs or idempotency keys for the resolver follow-up creation activity.
Rationale: Gate retries, replays, duplicate webhooks, and repeated polling must not create multiple resolver runs for the same PR revision.
Alternatives considered: Relying only on external database de-duplication was rejected because workflow state should remain the source of truth for whether this gate has launched its resolver.

## Post-Remediation Readiness

Decision: Reuse the same readiness evaluation rules inside the resolver path as a transient wait condition after pr-resolver pushes remediation commits; do not create another top-level merge gate.
Rationale: The initial gate separates implementation completion from merge automation. After resolver ownership begins, additional waits are part of resolver orchestration, and adding more top-level gates would fragment state and make ownership unclear.
Alternatives considered: Starting a new merge gate after every resolver push was rejected because it can create recursive gate chains and difficult operator semantics.

## Testing Strategy

Decision: Use unit tests for Pydantic/request models, readiness classification helpers, and launch idempotency keys; use Temporal workflow-boundary tests for parent-to-gate startup, blocker waiting, gate-open resolver launch, duplicate-event handling, and stale/closed/denied stop paths.
Rationale: MM-341 changes workflow/activity contracts and long-lived orchestration behavior, so isolated unit tests are insufficient. Boundary tests are required by repository guidance for Temporal-facing contract changes.
Alternatives considered: Testing only GitHub service helpers was rejected because it would miss parent workflow completion and merge-gate replay/idempotency behavior.
