# UI Contract: Create Page Publish Controls

## Publish Mode Options

The Create page exposes one compact `Publish Mode` control in the Steps card control group.

Required visible options:
- None
- Branch
- PR
- PR with Merge Automation

The final label for the combined option may be shortened, but it must be accessible and unambiguous.

## Availability Rules

- None, Branch, and PR remain available according to existing publish constraints.
- PR with Merge Automation is available only when:
  - page mode is create,
  - the effective publish behavior is PR publishing,
  - the effective task skill is not `pr-resolver` or `batch-pr-resolver`,
  - no preset or runtime constraint forces publishing to None.
- If constraints change while PR with Merge Automation is selected, the page must clear or normalize the selection visibly before submit.

## Submission Contract

The UI-only combined choice must normalize before request submission.

| Visible selection | Top-level `publishMode` | `task.publish.mode` | Top-level `mergeAutomation` |
| --- | --- | --- | --- |
| None | `none` | `none` | omitted |
| Branch | `branch` | `branch` | omitted |
| PR | `pr` | `pr` | omitted |
| PR with Merge Automation | `pr` | `pr` | `{ "enabled": true }` |

The request must not submit `pr_with_merge_automation` or any other new backend publish-mode value.

## Edit And Rerun Hydration

The page must normalize stored state into the visible control:
- stored None -> None
- stored Branch -> Branch
- stored PR and no merge automation -> PR
- stored PR and merge automation enabled -> PR with Merge Automation, when currently allowed

When currently disallowed, merge automation must not remain selected silently.

## Accessibility And Layout

- Branch and Publish Mode must expose accessible names.
- Publish Mode appears next to Branch when space allows.
- Branch and Publish Mode remain in the same Steps card control group when wrapping.
- Execution context must not expose a separate Enable merge automation checkbox.

## Copy

When the combined PR with Merge Automation choice is available or selected, the UI must explain that MoonMind waits for PR readiness and uses pr-resolver behavior. It must not imply direct auto-merge or bypass resolver handling.
