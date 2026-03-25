# Remaining work: `docs/Api/ExecutionsApiContract.md`

**Source:** [`docs/Api/ExecutionsApiContract.md`](../../Api/ExecutionsApiContract.md)  
**Last synced:** 2026-03-24

## Open items

- **Doc status:** Draft — promote to stable when contract matches shipped API and migration posture is finalized.
- **Product migration:** `/api/executions` is still framed as a Temporal lifecycle adapter during the broader task-oriented migration; align with [`docs/tmp/SingleSubstrateMigration.md`](../SingleSubstrateMigration.md) and remove “during migration” framing once the compatibility layer is retired or simplified.
- **§16 compatibility:** Revisit list/detail semantics, pagination token naming (`invalid_pagination_token` cleanup), and “sole public API” claims once queue/system paths are fully subsumed or documented as legacy-only.
- **Projection:** Contract assumes `TemporalExecutionService` + `temporal_executions` projection; reconcile doc if projection becomes cache-only or is removed.
