# Production metrics and dashboards

`moonmind.observability.metrics.REGISTRY` is the machine-readable authority for names, types, units, bounded labels, owners, and consumers. Enabled exporters reject unknown label keys and normalize unknown bounded values to `other`; disabled telemetry remains a no-op. Workflow, run, step, session, user, repository, branch, artifact, raw error, and arbitrary provider/model identities belong in traces, logs, or authorized read APIs—not labels.

## Registry and migration

| Domain | Instruments | Labels | Owner | Consumers |
| --- | --- | --- | --- | --- |
| API | `moonmind_api_requests`, `moonmind_api_request_duration_seconds` | outcome | API | overview, availability/latency SLO |
| workflows | `moonmind_workflow_started`, `moonmind_workflow_duration_seconds` | outcome | workflows | overview, reliability |
| effect reconciliation | `moonmind_duplicate_effect_observations` | component | workflows | workflow, reliability |
| Temporal | `moonmind_task_schedule_to_start_seconds` | component | Temporal | fleet/queue dashboard |
| providers | `moonmind_provider_requests`, `moonmind_usage_attribution_coverage_ratio` | outcome/runtime_family | profiles | provider/cost dashboard |
| Omnigent | `moonmind_omnigent_session_start_seconds`, `moonmind_omnigent_event_lag_seconds` | outcome/runtime_family | runtime | bridge/host dashboard |
| artifacts/operator | `moonmind_artifact_operations`, `moonmind_observability_stream_lag_seconds` | outcome/component | artifacts/operator | artifact/Live Logs/Chat |
| security | `moonmind_policy_decisions` | outcome | security | intervention dashboard |

Durations use seconds and deployments choose histogram buckets that contain the SLO boundary. Ratios range from zero to one. Existing StatsD names are migration inputs only. A rename requires a tombstone containing the old and replacement names, dual-emission release, dashboard-parity evidence, retention window, and removal release.

## Provisional SLOs

Policy/user denials and planned maintenance are excluded. Initial objectives remain non-paging until 30 days of representative baseline data and owner sign-off.

| Journey | SLI | Window/objective | Owner |
| --- | --- | --- | --- |
| API availability | successful / eligible requests | 30d / 99.9% provisional | API |
| API latency | eligible requests below calibrated bound | 30d / 99% provisional | API |
| workflow acceptance | accepted / eligible submissions | 30d / 99.9% provisional | workflows |
| queue latency | tasks below schedule-to-start bound | 30d / 99% provisional | Temporal |
| workflow/provider completion | successful / eligible attempts | 30d / baseline required | workflows/profiles |
| scheduled objective completion | succeeded / eligible scheduled objectives | 30d / 95% provisional, baseline required | workflows |
| Omnigent session freshness | successful starts and fresh events / eligible sessions | 30d / baseline required | runtime |
| artifact availability | successful reads+writes / eligible operations | 30d / 99.9% provisional | artifacts |
| Live Logs/Chat history | successful initial fetches / eligible fetches | 30d / 99.9% provisional | operator |
| attribution coverage | attributed / measurable usage | 7d / baseline required | profiles |

Alerts use short/long multi-window burn pairs, start as tickets, and become pages only after threshold calibration. Every alert carries owner, severity, dedupe key, impact, first dashboard, query guidance, runbook, and recovery condition. Single-run failures never page.

## Objective and attempt evidence

`GET /api/executions/metrics` includes `objectiveMetrics`, a bounded projection with `scope=observed_sample`. It is not an exhaustive 30-day SLI. The existing execution totals retain their independent query semantics. A full-window SLI requires collecting the complete authorized interval and preserving attempt identities and terminal evidence.

Root workflows declare their parent, outcome and scheduling state in durable memos. Missing historical lineage, chronology or scheduling is reported as unknown. Idle scans and cancellations do not enter the eligible-objective denominator; succeeded, failed and verification-blocked objectives do. Child workflows are counted separately. Recovery runs join an objective only through a verified saved-work lineage whose source run is present in the sample.

The latest observed outcome determines objective success. `attemptOutcomes` retains failed attempts after a recovery succeeds. `attemptProgress` accumulates remediation, evidence-retry, contract-repair, activity-retry and no-progress counters across distinct observed workflow runs. Repeated observations of the same run do not add cost twice. Saved-work availability is known only when a producer has verified a recoverable checkpoint; missing evidence is unknown. Recovery success is the fraction of observed linked recovery objectives that ended successfully. These projections must be read together: successful recovery does not erase prior failures or retry cost.

`moonmind_duplicate_effect_observations` counts reconciliations that observe multiple distinct, validated remote receipts for one owned side effect. Duplicate API rows with the same receipt ID are deduplicated first. Re-observing an unresolved duplicate may increment the counter again, so this is an incident signal, not a global count of unique duplicate effects. Runtime command deduplication remains owned by Omnigent's existing command telemetry. Telemetry export failure cannot change the authoritative operation result.

## Operator surfaces and rollout

The provisioned overview dashboard groups panels for overview, workflow/steps, Temporal/workers, providers/profiles/cost, Omnigent, artifacts/Live Logs/Chat, and security/intervention. Variables use bounded dimensions only. Drill-down links are server-owned templates to authorized Workflow Detail, trace, log, Temporal, and runbook views.

Rollout freezes the legacy inventory, dual-emits via an adapter, validates registry/rules/dashboard/runbook references in CI, calibrates in non-production, promotes reviewed alerts, and removes legacy names after retention. Before production enablement operators record expected series count, backend storage/retention, exporter overhead, and rollback to disabled telemetry and non-paging rules.
