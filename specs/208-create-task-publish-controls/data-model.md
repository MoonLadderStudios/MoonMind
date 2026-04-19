# Data Model: Create Task Publish Controls

## Publish Mode Selection

Represents the visible Create-page publish choice.

Values:
- `none`: no publishing after task execution.
- `branch`: publish work back to the selected branch.
- `pr`: create or update a pull request without merge automation.
- `pr_with_merge_automation`: create or update a pull request and enable existing MoonMind merge automation behavior.

Validation:
- `pr_with_merge_automation` is valid only for ordinary PR-publishing task authoring.
- Resolver-style tasks and constrained presets must not keep `pr_with_merge_automation` selected.
- The visible value is UI-only; it must normalize before submission.

Submission mapping:
- `none` -> `publishMode=none`, `task.publish.mode=none`, no `mergeAutomation`.
- `branch` -> `publishMode=branch`, `task.publish.mode=branch`, no `mergeAutomation`.
- `pr` -> `publishMode=pr`, `task.publish.mode=pr`, no `mergeAutomation`.
- `pr_with_merge_automation` -> `publishMode=pr`, `task.publish.mode=pr`, `mergeAutomation.enabled=true`.

## Merge Automation Intent

Represents the existing runtime behavior request to let MoonMind wait for PR readiness and use resolver handling.

Fields:
- `enabled`: boolean submitted only when the visible publish selection is PR with Merge Automation and current task constraints allow it.

Validation:
- Must be omitted for None and Branch.
- Must be omitted for direct `pr-resolver` and `batch-pr-resolver` tasks.
- Must be omitted or cleared when runtime or preset constraints force publish mode to None.
- Must not imply direct auto-merge or bypass resolver behavior.

## Stored Publish State

Represents publish state reconstructed from create, edit, or rerun input snapshots.

Inputs:
- stored publish mode from top-level payload and/or nested task publish object.
- stored merge automation enabled flag, when present.
- runtime, selected skill, and preset constraints.

State transitions:
- Stored None -> visible None.
- Stored Branch -> visible Branch.
- Stored PR without merge automation -> visible PR.
- Stored PR with merge automation enabled -> visible PR with Merge Automation when currently allowed.
- Stored PR with merge automation enabled but currently disallowed -> visible PR or None according to existing publish constraints, with merge automation not submitted.

## Steps Card Publish Control Group

Represents the visible authoring group in the Steps card footer.

Members:
- GitHub Repo.
- Branch.
- Publish Mode.

Validation:
- Branch and Publish Mode expose accessible names even when compact styling omits visible labels above the controls.
- Publish Mode stays adjacent to Branch when room allows and remains in the same control group when wrapping.
- Execution context must not contain duplicate Publish Mode or standalone Enable merge automation controls.
