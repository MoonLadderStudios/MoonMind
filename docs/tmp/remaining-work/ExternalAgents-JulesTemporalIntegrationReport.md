# Remaining work: `docs/tmp/JulesTemporalIntegrationReport.md`

**Source:** [`docs/tmp/JulesTemporalIntegrationReport.md`](../JulesTemporalIntegrationReport.md)  
**Last synced:** 2026-03-24

## Open items

### §2 Implementation tasks (from source table)

- **Testing & QA:** Partial — expand unit/contract/integration coverage per §3.
- **Observability & artifact storage:** Partial — metrics/dashboards; final Jules snapshots in artifact backend.
- **UI/Dashboard:** Remaining — show `external_operation_id` vs workflow ID, normalized status, `externalUrl` link.

### §4 Event contract migration

- Callback versioning, schema registry, upgrade path when callbacks ship.

### §5+ Observability checklist

- Traceability, metrics, dashboards, alerts — complete rows in source doc.

### Doc edits suggested in §1

- Clarify polling vs callback wording in proposal workflow, correlation, failure handling — merge into canonical docs when stable.
