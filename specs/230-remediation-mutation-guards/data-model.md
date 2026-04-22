# Data Model: Remediation Mutation Guards

## RemediationMutationGuardPolicy

Policy inputs used to evaluate whether a side-effecting remediation action can proceed.

- `lock_scope`: Lock scope for mutation. Defaults to `target_execution`.
- `lock_mode`: Lock mode. Defaults to `exclusive`.
- `lock_ttl_seconds`: Positive duration used to decide stale lock recovery.
- `max_actions_per_target`: Maximum side-effecting action count for one target.
- `max_attempts_per_action_kind`: Maximum attempts for one action kind on one target.
- `cooldown_seconds`: Minimum interval before repeating an identical action.
- `allow_nested_remediation`: Whether a remediation task may target another remediation task.
- `allow_self_target`: Whether a remediation task may target itself.
- `max_self_healing_depth`: Automatic self-healing depth. Defaults to `1`.
- `target_change_policy`: One of `no_op`, `rediagnose`, or `escalate`.

Validation:
- Defaults are safe and restrictive.
- Negative counts or durations are invalid.
- Unsupported target-change policies fail closed.

## RemediationMutationLock

One bounded exclusive mutation claim.

- `lock_id`: Stable lock identity.
- `scope`: Canonical lock scope such as `target_execution`.
- `target_workflow_id`: Target execution workflow ID.
- `target_run_id`: Target execution pinned run ID.
- `holder_workflow_id`: Remediation workflow holding the lock.
- `holder_run_id`: Remediation run holding the lock when known.
- `created_at`: Creation timestamp.
- `expires_at`: Expiration timestamp.
- `mode`: Lock mode, default `exclusive`.
- `released`: Whether the holder released or lost the lock.

State transitions:
- `missing -> acquired`
- `active same holder -> acquired` with the same decision
- `active other holder -> denied`
- `expired other holder -> recovered`
- `released/lost holder -> denied` for further mutation until reacquired explicitly

## RemediationActionLedgerEntry

Canonical duplicate-suppression entry for one logical action request.

- `remediation_workflow_id`: Remediation workflow requesting the action.
- `target_workflow_id`: Target execution workflow ID.
- `idempotency_key`: Stable logical side-effect key.
- `action_kind`: Typed action kind.
- `request_shape_hash`: Stable representation of action kind, target, dry-run state, and bounded parameters.
- `decision`: Original guard decision.
- `reason`: Original reason code.
- `recorded_at`: Timestamp or deterministic clock value.

Validation:
- Missing idempotency keys fail closed for side-effecting actions.
- Reusing a key with the same request shape returns the original entry.
- Reusing a key with a materially different request shape is denied as unsafe reuse.

## RemediationActionBudget

Counters used to avoid repeated destructive action loops.

- `target_workflow_id`: Target execution workflow ID.
- `total_side_effecting_actions`: Count of accepted side-effecting actions for the target.
- `attempts_by_action_kind`: Count by action kind.
- `last_attempt_by_request_shape`: Last attempt time for cooldown decisions.

Validation:
- Counts increment only for accepted side-effecting guard decisions.
- Exhausted total or per-kind budgets return bounded non-mutating outcomes.
- Cooldown violations return bounded non-mutating outcomes with retry information when available.

## RemediationTargetFreshness

Fresh target comparison consumed before action execution.

- `pinned_run_id`: Target run captured when remediation was created.
- `current_run_id`: Current target run.
- `current_state`: Current target state.
- `current_summary`: Current target summary.
- `session_identity`: Current managed session identity when available.
- `target_run_changed`: Whether current and pinned run IDs differ.
- `materially_changed`: Whether any configured material field differs.

Validation:
- Missing current target health produces a bounded non-mutating outcome.
- Material changes map to `no_op`, `rediagnose`, or `escalate` according to policy.

## RemediationMutationGuardResult

Serialized guard decision returned before action execution.

- `schemaVersion`: `v1`.
- `decision`: `allowed`, `no_op`, `rediagnose`, `escalate`, or `denied`.
- `reason`: Stable reason code.
- `executable`: Whether action execution may proceed.
- `lock`: Redaction-safe lock decision data.
- `ledger`: Redaction-safe ledger decision data.
- `budget`: Redaction-safe budget/cooldown decision data.
- `nestedRemediation`: Redaction-safe nested-remediation decision data.
- `targetFreshness`: Redaction-safe target-freshness decision data.
- `redactedParameters`: Bounded parameters after redaction.

Validation:
- Raw secrets, presigned URLs, storage keys, and absolute local paths are redacted.
- Denied, no-op, re-diagnosis, and escalation outcomes are non-executable.
