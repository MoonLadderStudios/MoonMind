# Remaining work: `docs/Temporal/TemporalPlatformFoundation.md`

**Source:** [`docs/Temporal/TemporalPlatformFoundation.md`](../../Temporal/TemporalPlatformFoundation.md)  
**Last synced:** 2026-03-24

## Open items

- **Operational gates:** Pre-rollout validation for upgrades, SQL visibility schema rehearsal, shard-count decision — treat as living runbook tasks; mark done per environment.
- **Shard scaling:** If `numHistoryShards = 1` is chosen, any scale-out is a **cluster migration** — track as a separate program when needed.
- **Cluster migration / cutover:** Documented as future risk; no code change in this repo tracker beyond ops readiness.
