# Runbook: Remediation Fleet

**Status:** Operator runbook
**Owners:** MoonMind Platform
**Related:** `docs/Observability/RemediationFleetMetrics.md`, `docs/Workflows/WorkflowRemediation.md`, `docs/Workflows/RemediationAcceptanceMatrix.md`

This runbook covers alerts emitted from the remediation fleet signal
`moonmind_remediation_events` (see `moonmind/observability/remediation_metrics.py`).
Each alert selects one bounded `signal` label.

## Signals and response

| Signal | Severity | Meaning | First response |
| --- | --- | --- | --- |
| `action` | info | A side-effecting remediation action was delivered. Tracks action rate. | No action; use for rate baselining and rollout review. |
| `repeated_failure` | warning | The same failure signature recurred across attempts. | Confirm no-progress detection engaged; consider pausing the healer. |
| `lock_conflict` | warning | A mutation lock could not be acquired for a target. | Verify one owner per target; check for a stuck lock holder. |
| `denial` | info | An action was denied or required approval. | Confirm policy/authority is behaving as intended. |
| `escalation` | warning | Remediation escalated to a human/operator. | Pick up the operator handoff in Workflow Detail. |
| `unverified_mutation` | critical | A mutation applied without a subsequent passing verification. | Treat as a safety incident: a delivered action is not a verified repair. Inspect the action result and target evidence immediately. |

## Rollout gate relationship

Autonomous (`admin_auto`) remediation stays fail-closed behind
`FEATURE_FLAGS__REMEDIATION_AUTONOMOUS_ROLLOUT_ENABLED`. These signals and their
alerts are a precondition for enabling that gate, together with a passing
operator acceptance matrix (`docs/Workflows/RemediationAcceptanceMatrix.md`).
`unverified_mutation` firing during a rollout is a stop condition.
