# Remaining work: `docs/Temporal/TemporalArchitecture.md`

Updated: 2026-04-04

## Step-ledger rollout

- Implement the workflow-owned step ledger in `MoonMind.Run` and expose it through query/update-safe deterministic state.
- Keep Visibility and Memo bounded while surfacing step truth through query/detail APIs.
- Add workflow-boundary coverage for meaningful step transitions, check/review transitions, and latest-run query behavior.
