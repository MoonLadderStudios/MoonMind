# Remaining work: `docs/Temporal/WorkerPauseSystem.md`

**Source:** [`docs/Temporal/WorkerPauseSystem.md`](../../Temporal/WorkerPauseSystem.md)  
**Last synced:** 2026-03-24

## Open items

### Phase 1 (Temporal alignment)

- Mission Control banner uses **Temporal Visibility** (and related APIs) instead of queue tables for worker pause messaging (where still applicable). **End state:** operator truth from Temporal, not parallel queue bookkeeping.
- Runbooks standardized on `worker.shutdown()` for drain upgrades.

### Phase 2 (Advanced suspend)

- Deep quiesce: `workflow.wait_condition()` + batch signals for safe suspension of active execution.

Related roadmap: [`docs/tmp/Roadmap.md`](../Roadmap.md) item 1.7 (dashboard wiring).
