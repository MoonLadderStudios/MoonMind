# Remaining work: `docs/Temporal/VisibilityAndUiQueryModel.md`

**Source:** [`docs/Temporal/VisibilityAndUiQueryModel.md`](../../Temporal/VisibilityAndUiQueryModel.md)  
**Last synced:** 2026-03-24

## Open items

- **Doc status:** Draft — elevate when Visibility contract is fully implemented.
- **First-class `temporal` dashboard source:** Runtime config / route shell still lack a first-class `temporal` source (see §4).
- **§14 projection rules:** Enforce when projections mirror Temporal; collapse any transitional language after single-substrate cutover.
- **Implementation gaps (formerly §15 in source):** True Temporal Visibility–backed query/count for `/api/executions`; `mm_owner_type` as first-class Visibility; `ownerType` / `entry` / `repo` / `integration` filters on adapter API; first-class `temporal` dashboard source; `taskId == workflowId` enforcement on task surfaces; canonical top-level `title` / `summary` / `entry` / `ownerType` / `waitingReason` / `attentionRequired` on list rows; retire compatibility dashboards that collapse waiting states; stop hiding `workflowId` in debug-only metadata.
- **Search attributes / filters:** Needed before dashboard depends on Temporal-backed list filtering in earnest.
