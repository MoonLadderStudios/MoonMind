# Remaining work: `docs/Temporal/VisibilityAndUiQueryModel.md`

Updated: 2026-04-04

## Step-ledger rollout

- Keep step rows, attempts, and checks out of Search Attributes and Memo in implementation as well as in docs.
- Reconcile `mm_updated_at` mutations so only meaningful step/user-visible transitions change recency ordering.
- Add projection/query tests covering bounded progress hints without leaking full step state into Visibility or Memo.
