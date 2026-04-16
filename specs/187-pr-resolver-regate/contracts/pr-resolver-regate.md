# Contract: PR Resolver Child Re-Gating

## Resolver Child Invocation

When `MoonMind.MergeAutomation` launches a resolver attempt, it must start a child workflow with this logical shape:

```json
{
  "workflowType": "MoonMind.Run",
  "initialParameters": {
    "publishMode": "none",
    "task": {
      "tool": {
        "type": "skill",
        "name": "pr-resolver",
        "version": "1.0"
      },
      "inputs": {
        "repo": "owner/repo",
        "pr": "123",
        "mergeMethod": "squash"
      }
    }
  }
}
```

Required behavior:
- `publishMode` is top-level under `initialParameters`.
- `publishMode` is exactly `none`.
- The tool identity is exactly `skill/pr-resolver/1.0`.
- Resolver attempts are child `MoonMind.Run` workflows, not direct skill activity calls inside `MoonMind.MergeAutomation`.

## Resolver Child Result

Resolver children return a compact machine-readable disposition:

```json
{
  "status": "success",
  "mergeAutomationDisposition": "reenter_gate",
  "headSha": "def456"
}
```

Allowed `mergeAutomationDisposition` values:
- `merged`
- `already_merged`
- `reenter_gate`
- `manual_review`
- `failed`

Disposition handling:
- `merged` and `already_merged` complete merge automation successfully.
- `reenter_gate` returns to gate evaluation and increments the cycle.
- `manual_review` and `failed` produce non-success merge automation outcomes.
- Missing or unsupported dispositions produce deterministic non-success outcomes.

## Head-SHA Freshness

When a resolver returns `reenter_gate`, merge automation must not reuse external readiness from the previous head SHA.

Required behavior:
- Track the current head SHA for gate evaluation.
- Update the tracked head SHA from resolver output when present.
- Require the next gate-open condition to be fresh for the tracked head.
- Preserve shared blocker categories between gate and resolver evidence.
