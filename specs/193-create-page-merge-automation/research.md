# Research: Create Page Merge Automation

## Merge Automation Request Shape

Decision: Submit `mergeAutomation: { enabled: true }` at the task creation parameters level when an ordinary PR-publishing task has merge automation selected.

Rationale: Existing `MoonMind.Run` code already detects enabled merge automation from `mergeAutomation`, `task.mergeAutomation`, or `task.publish.mergeAutomation`. Submitting the top-level field next to `publishMode` uses an already-supported contract and keeps merge automation as PR publish configuration instead of inventing a new task type.

Alternatives considered: Nesting only under `task.publish.mergeAutomation` was rejected because the Jira brief explicitly names `mergeAutomation.enabled=true`, and the existing workflow accepts the top-level shape directly. Introducing a new backend endpoint or task type was rejected because the feature must preserve the existing Create page submission flow.

## Availability Rules

Decision: Treat merge automation as available only when the normalized publish mode is `pr` and the selected primary skill is not `pr-resolver` or `batch-pr-resolver`.

Rationale: The existing Create page already forces resolver-style primary skills to publish mode `none`, and resolver child runs must not create another PR or start parent-owned merge automation. Tying availability to current form state lets stale selections be cleared before submission.

Alternatives considered: Showing a disabled option for every mode was rejected because it increases UI noise and makes resolver task semantics less clear. Allowing resolver-style tasks to carry disabled metadata was rejected because stale request fields could conflict with the forced `publish.mode=none` invariant.

## User-Facing Copy

Decision: The control copy must state that merge automation waits for the PR readiness gate and then uses `pr-resolver`; it must not promise direct auto-merge or bypass review behavior.

Rationale: The active merge automation workflow launches `pr-resolver` as a child. The UI should describe the real behavior so operators understand that merge handling remains resolver-mediated.

Alternatives considered: Labeling the control "Auto merge" was rejected because it implies a direct merge path and conflicts with the Jira brief.

## Test Strategy

Decision: Add focused Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx` for visibility, clearing stale state, payload inclusion, payload absence, and resolver-skill exclusion. Run existing backend merge automation tests to confirm the submitted contract is still accepted by `MoonMind.Run`.

Rationale: The requested change is primarily a browser form and request-shape feature. Existing workflow tests already cover merge automation parsing and startup from submitted parameters, so new Python tests are only needed if implementation changes backend normalization.

Alternatives considered: Full browser e2e tests were rejected for this story because the existing Create page test harness already inspects the exact submitted payload without network flakiness.
