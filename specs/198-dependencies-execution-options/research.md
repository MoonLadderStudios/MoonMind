# Research: Dependencies and Execution Options

## Dependency Picker Boundary

Decision: Use the existing Create page dependency picker backed by the Temporal execution list endpoint filtered to `workflowType=MoonMind.Run` and `entry=run`.
Rationale: The story is about preserving the Create page runtime behavior, not adding a new dependency service. The current endpoint already provides recent run candidates and keeps dependency references as execution IDs.
Alternatives considered: Add a dedicated dependency-search endpoint; rejected because the existing list surface satisfies the current bounded picker requirement and avoids new backend surface area.

## Dependency Limits And Duplicate Handling

Decision: Enforce the 10 direct dependency cap and duplicate rejection in browser state before submission, with tests verifying the picker cannot add duplicate or eleventh entries.
Rationale: The Jira brief explicitly requires client-side duplicate rejection and a direct dependency cap. Browser enforcement gives immediate feedback and keeps invalid payloads out of normal submission.
Alternatives considered: Rely only on backend validation; rejected because the source requirement calls for client-side duplicate rejection and continued manual authoring after dependency issues.

## Runtime And Provider Profiles

Decision: Preserve runtime defaults and provider-profile options from server-provided dashboard configuration and provider profile list responses.
Rationale: MoonMind runtime configurability requires runtime-specific defaults and options to come from the control plane instead of hardcoded client assumptions.
Alternatives considered: Keep one global model/profile list across runtimes; rejected because the story requires runtime-specific provider-profile options.

## Merge Automation Availability

Decision: Keep merge automation available only for ordinary PR-publishing tasks, omit it for `branch`/`none`, and suppress it for direct resolver-style tasks.
Rationale: Merge automation is a configuration for ordinary PR-publishing work that later routes through `pr-resolver`; exposing it for direct resolver tasks or non-PR publishing would misrepresent the workflow and produce invalid payloads.
Alternatives considered: Allow merge automation for any publish mode and let backend reject; rejected because the UI must hide or disable unavailable options and avoid stale enabled payloads.

## Jira And Image Flow Isolation

Decision: Treat Jira import and image upload as draft content inputs that must not alter repository validation, publish validation, runtime gating, dependency limits, or resolver-style restrictions.
Rationale: The story is specifically about preserving execution controls when optional input channels are used.
Alternatives considered: Let Jira import or image upload re-normalize the whole task draft; rejected because it risks weakening validation and crossing ownership boundaries between input content and execution controls.

## Test Strategy

Decision: Use focused Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx` for both unit-level state behavior and integration-style request payload behavior, then run the repository unit runner.
Rationale: The Create page behavior is browser state and request-shape logic exercised through mocked MoonMind REST endpoints. No Docker-backed service integration is required for the selected story.
Alternatives considered: Add compose-backed integration tests; rejected because the high-risk behavior is the UI state/payload contract and existing test harness already covers that boundary.
