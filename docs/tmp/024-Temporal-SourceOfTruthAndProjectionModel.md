# Remaining work: `docs/Temporal/SourceOfTruthAndProjectionModel.md`

**Source:** [`docs/Temporal/SourceOfTruthAndProjectionModel.md`](../../Temporal/SourceOfTruthAndProjectionModel.md)  
**Last synced:** 2026-03-24

## Open items

- **Migration stance:** “During migration” projection rules remain until queue/system sources are removed or read-only; track [`docs/tmp/SingleSubstrateMigration.md`](../SingleSubstrateMigration.md).
- **§5.2–5.3:** Move toward Temporal as sole write path for lifecycle; shrink projection to adapter/cache as checkpoints in the migration plan require.
- **§8.2 exception matrix:** Delete rows as each mixed-source route is eliminated.
