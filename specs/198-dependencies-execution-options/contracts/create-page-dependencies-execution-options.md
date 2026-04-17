# Contract: Create Page Dependencies and Execution Options

## Scope

This contract defines observable Create page behavior for MM-379. It covers dependency selection, runtime/provider profile options, publish mode, merge automation, resolver-style restrictions, and validation isolation from Jira import and image upload flows.

## Dependency Picker

- The page exposes an `Existing run` selector backed by existing `MoonMind.Run` executions.
- The page exposes an `Add dependency` action.
- The page displays selected dependencies and supports removing each one.
- Dependency options exclude already selected dependencies.
- The page rejects adding a dependency when no run is selected.
- The page rejects duplicate direct dependencies.
- The page rejects adding more than 10 direct dependencies.
- If dependency option fetch fails, the page displays a recoverable message and still permits valid manual task creation without dependencies.
- Submitted task payloads include selected dependencies as `payload.task.dependsOn`.

## Runtime And Provider Profiles

- The page exposes `Runtime`, `Provider profile`, `Model`, and `Effort` controls.
- Runtime options come from server-provided configuration.
- Provider profile options are fetched for the selected runtime.
- Changing runtime updates provider profile options to that runtime's profile set.
- Runtime defaults for model and effort come from server-provided per-runtime defaults unless the author has explicitly overridden the model.

## Publish And Merge Automation

- The page exposes `Publish Mode` values `pr`, `branch`, and `none`.
- The merge automation control is available only for ordinary task creation when effective publish mode is `pr`.
- The merge automation control is hidden or disabled when publish mode is `branch` or `none`.
- The merge automation control is hidden or disabled when the effective task skill is `pr-resolver` or `batch-pr-resolver`.
- When merge automation is selected and available, the submitted payload includes:
  - `payload.publishMode = "pr"`
  - `payload.task.publish.mode = "pr"`
  - `payload.mergeAutomation.enabled = true`
- When merge automation is unavailable, the submitted payload omits enabled merge automation fields.
- User-facing copy explains that merge automation uses `pr-resolver` after the PR readiness gate opens and does not imply direct auto-merge or bypass resolver behavior.

## Validation Isolation

- Jira import must not weaken repository validation.
- Image upload must not weaken repository validation.
- Jira import and image upload must not alter publish mode validity rules.
- Jira import and image upload must not alter runtime support checks or runtime-specific provider profile options.
- Jira import and image upload must not bypass duplicate dependency checks or the 10 dependency cap.

## Test Obligations

- Focused Create page tests cover dependency fetch failure, duplicate rejection, 10-item cap, runtime-specific provider profile options, merge automation payload shape, resolver-style suppression, and validation isolation from Jira/image flows.
