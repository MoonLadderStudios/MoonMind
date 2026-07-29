# Remediation fleet metrics and alerts

**Status:** Desired-state design
**Document Class:** System / Feature Design View
**Owners:** MoonMind Platform
**Related:** `docs/Observability/MetricsAndDashboards.md`, `docs/Workflows/WorkflowRemediation.md`, `docs/Workflows/RemediationAcceptanceMatrix.md`, `docs/Runbooks/Observability/RemediationFleet.md`

`moonmind.observability.remediation_metrics.REMEDIATION_FLEET_REGISTRY` is the
machine-readable authority for remediation fleet metric names, types, units,
bounded labels, owners, and consumers. It is deliberately separate from the
general overview registry so remediation signals and alerts can evolve without
repinning the SLO dashboard. Labels stay bounded and never carry per-run
identity (`FORBIDDEN_LABELS`).

Issue #3512, Area 7 requires operator-visible fleet metrics and alerts for six
signals before autonomous remediation can be enabled: **action rate, repeated
failure, lock conflict, denial, escalation, and unverified mutation.**

## Registry

| Metric | Instrument | Unit | Bounded labels | Owner | Consumers |
| --- | --- | --- | --- | --- | --- |
| `moonmind_remediation_events` | counter | events | `signal`, `authority_mode` | remediation | remediation-fleet, remediation-rollout-gate |

The `signal` label is one of: `action`, `repeated_failure`, `lock_conflict`,
`denial`, `escalation`, `unverified_mutation`. The `authority_mode` label is one
of: `observe_only`, `approval_gated`, `admin_auto`, `unknown`. Unknown signals
degrade to `escalation` so anomalies stay visible; unknown authority modes
degrade to `unknown`.

## Alerts

`remediation_alert_rules()` returns one alert per signal, each selecting a single
`signal` label and referencing `docs/Runbooks/Observability/RemediationFleet.md`:

| Alert signal | Severity | Summary |
| --- | --- | --- |
| `action` | info | Remediation mutating action rate |
| `repeated_failure` | warning | Same remediation failure recurring |
| `lock_conflict` | warning | Remediation mutation lock contention |
| `denial` | info | Remediation action denied or approval-gated |
| `escalation` | warning | Remediation escalated to an operator |
| `unverified_mutation` | critical | Remediation mutation applied without passing verification |

`unverified_mutation` is `critical`: a successfully *delivered* action is not a
verified repair, and an applied mutation without a passing verification is a
safety incident.

## Rollout gate

Autonomous (`admin_auto`) remediation mutation is fail-closed behind the
deployment flag `FEATURE_FLAGS__REMEDIATION_AUTONOMOUS_ROLLOUT_ENABLED`
(`moonmind.workflows.temporal.remediation_rollout.autonomous_remediation_rollout_enabled`).
Even when a link is authored with `admin_auto`, autonomous mutation is refused
until the flag is enabled. These metrics/alerts and a passing operator
acceptance matrix are preconditions for enabling that flag.
