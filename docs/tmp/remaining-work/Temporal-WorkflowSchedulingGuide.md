# Remaining work: `docs/Temporal/WorkflowSchedulingGuide.md`

**Source:** [`docs/Temporal/WorkflowSchedulingGuide.md`](../../Temporal/WorkflowSchedulingGuide.md)  
**Last synced:** 2026-03-24

## Open items

- **Recurring cadence (target state):** **Temporal Schedules** (plus product/DB metadata as needed) per [`docs/tmp/TemporalSchedulingPlan.md`](../TemporalSchedulingPlan.md) and [`docs/tmp/TemporalSchedulingImprovements.md`](../TemporalSchedulingImprovements.md). Do **not** treat an app-owned “due scan → enqueue” loop as the permanent scheduling engine for Temporal-backed work.
- **Transitional coexistence:** Until [`docs/tmp/SingleSubstrateMigration.md`](../SingleSubstrateMigration.md) finishes, some docs/code may still mention queue-backed dispatch—keep that explicitly **bounded** and on the path to removal, not a second permanent center of gravity.
- **`RecurringTasksService` reconciliation:** Converge primary firing on Temporal Schedule CRUD + server-owned cadence; align with [`docs/Temporal/TemporalScheduling.md`](../../Temporal/TemporalScheduling.md) §10 and the execution-improvements doc above.
