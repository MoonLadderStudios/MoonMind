# Contract: Merge Outcome Propagation

## Merge Automation Child Result

`MoonMind.MergeAutomation` returns a compact result to the parent `MoonMind.Run`:

```json
{
  "status": "merged",
  "prNumber": 123,
  "prUrl": "https://github.com/owner/repo/pull/123",
  "cycles": 2,
  "resolverChildWorkflowIds": ["merge-auto-resolver:mm-parent:1"],
  "lastHeadSha": "abc123",
  "blockers": []
}
```

Allowed terminal `status` values:
- `merged`
- `already_merged`
- `blocked`
- `failed`
- `expired`
- `canceled`

Parent mapping:
- `merged` and `already_merged` complete the parent successfully.
- `blocked`, `failed`, and `expired` fail the parent with an operator-readable merge automation reason.
- `canceled` cancels the parent and must not be reported as success or failure.
- Missing or unsupported statuses fail deterministically with an operator-readable reason.

## Parent Dependency Signal

The original parent `MoonMind.Run` workflow id remains the dependency target for downstream `dependsOn` relationships.

Required behavior:
- A downstream dependency is satisfied only when the parent reaches terminal success.
- Parent failed and canceled outcomes do not satisfy success-only dependencies.
- Merge automation child workflow ids and resolver child workflow ids are reported for observability only; they are not dependency targets.

## Cancellation Propagation

Parent cancellation while merge automation is active must request child cancellation through Temporal child workflow cancellation semantics.

Merge automation cancellation while a resolver child run is active must request cancellation of that resolver child.

Required reporting:
- Parent and merge automation summaries identify cancellation rather than success.
- Cleanup or cancellation summaries must be truthful when cleanup is best-effort or confirmation is unavailable.
- Secret-like provider details must not be included in summaries or blockers.
