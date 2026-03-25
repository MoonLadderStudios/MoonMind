# Remaining work: `docs/Temporal/IntegrationsMonitoringDesign.md`

**Source:** [`docs/Temporal/IntegrationsMonitoringDesign.md`](../../Temporal/IntegrationsMonitoringDesign.md)  
**Last synced:** 2026-03-24

## Open items

- **§9.4 Migration note:** Retire `taskId`-compatibility framing when list/detail are Temporal-native everywhere.
- **§15 Implementation order:** All six steps (activity contract, callback correlation storage, `ExternalEvent` handling, polling/CAN policy, visibility `mm_*` updates, tests) — track as implementation backlog; design is draft until closed.
- **§16 Open decisions:** First migration after Jules, detail lookup vs correlation, latency SLOs, cancellation truth, per-integration worker queues.
