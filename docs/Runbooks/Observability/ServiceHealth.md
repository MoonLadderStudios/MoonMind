# MoonMind service-health alert runbook

## Triage

1. Confirm impact and duration in `moonmind-overview`; do not infer an outage from one run.
2. Open the authorized trace/log/Temporal link and identify the bounded failure class and affected component.
3. Check readiness, poller/queue health, provider profile leases/cooldowns, Omnigent heartbeat/event freshness, and artifact read/write health in that order.
4. Preserve diagnostics and incident artifacts. Never put workflow, user, repository, session, or artifact identities into metric labels.

## Safe mitigation

Drain an unhealthy worker, reroute only through an explicitly authorized profile/runtime, or reduce admission while queues recover. Do not silently substitute credentials, models, billing-relevant settings, or a less constrained runtime. If artifact writes or audit persistence are failing, stop new authority-sensitive operations rather than risk evidence loss.

## Escalation and recovery

Escalate to the alert owner when impact persists beyond the alert `for` interval, data loss is possible, or replay/idempotency is uncertain. Record affected durable workflow ranges without placing identities in telemetry labels. Recovery requires readiness restored, queue/event lag draining, successful representative API → workflow → Omnigent → artifact execution, durable diagnostics/audit publication, and the alert's recovery condition sustained for one hour.
