# Contract: Create Page Merge Automation

## UI Availability

The Create page exposes a merge automation checkbox only when:

- `Publish Mode` is `pr`
- the selected primary skill is not `pr-resolver`
- the selected primary skill is not `batch-pr-resolver`

When the option is not available, the page must not submit merge automation fields.

## Submitted Payload

When the operator selects merge automation for an ordinary PR-publishing task, the request body sent to the existing create endpoint includes:

```json
{
  "type": "task",
  "payload": {
    "task": {
      "publish": {
        "mode": "pr"
      }
    },
    "publishMode": "pr",
    "mergeAutomation": {
      "enabled": true
    }
  }
}
```

Other task fields are omitted from this contract for clarity.

## Disabled Or Unavailable Payload

When merge automation is unchecked, `Publish Mode` is `branch` or `none`, or the primary skill is resolver-style, the submitted payload omits `mergeAutomation`.

Resolver-style task submissions must continue to include:

```json
{
  "payload": {
    "task": {
      "publish": {
        "mode": "none"
      }
    }
  }
}
```

## Operator Copy

The control text must say that merge automation waits for PR readiness and uses `pr-resolver` for merge handling. It must not describe the option as direct auto-merge or as bypassing `pr-resolver`.
