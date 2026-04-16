# Data Model: Create Page Merge Automation

## Create Task Draft

Represents the Create page form state before submission.

Fields:
- `publishMode`: one of `pr`, `branch`, or `none`.
- `primarySkillId`: selected primary skill id, or `auto`/empty when no explicit skill is selected.
- `mergeAutomationEnabled`: local boolean state controlled by the merge automation option.
- `taskPayload`: normalized task object submitted to the existing task create endpoint.

Validation:
- `mergeAutomationEnabled` may remain true only when `publishMode` is `pr` and `primarySkillId` is not a resolver-style skill.
- Switching to `branch`, `none`, `pr-resolver`, or `batch-pr-resolver` clears or suppresses enabled merge automation before submission.

## Publish Configuration

Represents the existing publish contract.

Fields:
- `publishMode`: top-level task submission value.
- `task.publish.mode`: normalized nested publish mode in the submitted task payload.

Validation:
- PR publishing with merge automation must preserve both `publishMode=pr` and `task.publish.mode=pr`.
- Resolver-style tasks continue to submit `task.publish.mode=none`.

## Merge Automation Configuration

Represents optional configuration consumed by `MoonMind.Run`.

Fields:
- `enabled`: boolean. `true` opts the parent run into merge automation after PR publishing.

Validation:
- Omit the object entirely when disabled or unavailable.
- Submit `{ "enabled": true }` only for ordinary PR-publishing task drafts.

## Resolver-Style Task

Represents a direct task targeting PR resolver skills.

Fields:
- `primarySkillId`: `pr-resolver` or `batch-pr-resolver`.
- `task.publish.mode`: forced to `none`.

Validation:
- Must not expose or submit merge automation configuration.
