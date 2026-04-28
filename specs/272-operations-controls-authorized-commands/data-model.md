# Data Model: Operations Controls Exposed as Authorized Commands

## OperationControl

Settings-visible operational control.

Fields:
- `key`: stable operation identifier, for example `workers.pause`.
- `title`: operator-facing label.
- `current_state`: current subsystem state such as running, draining, quiesced, pending, failed, or unavailable.
- `impact`: concise expected effect shown before submission.
- `requires_confirmation`: whether the command class requires explicit confirmation.
- `authorized`: whether the current actor may invoke the command.
- `last_action`: latest sanitized `OperationAuditEvent` for this control, when available.
- `pending_transition`: optional command currently in progress.
- `failure_reason`: sanitized latest failure reason, when available.
- `resume_action`: optional safe rollback/resume affordance.

Validation:
- Every exposed control must map to a backend-owned command or status source.
- Controls may be hidden or disabled in the UI, but backend authorization remains authoritative.

## OperationCommand

Auditable request to change operational state.

Fields:
- `action`: `pause` or `resume` for the MM-542 worker-pause slice.
- `mode`: `drain` or `quiesce` for pause commands.
- `target`: `workers`.
- `reason`: operator-provided reason.
- `confirmation`: explicit confirmation string for disruptive command classes.
- `force_resume`: whether resume is allowed before drain completion; exposed on the public JSON contract as `forceResume`.
- `requested_by_user_id`: authenticated actor identifier.
- `requested_at`: server timestamp.
- `idempotency_key`: stable command key derived from action, target, mode, actor, and reason unless the command is an explicit force/rollback class.

Validation:
- `pause` requires `mode`, `reason`, and confirmation.
- `resume` requires `reason`.
- Forced resume requires confirmation.
- Unknown actions, targets, and modes fail fast.

## OperationResult

Sanitized result returned to Settings.

Fields:
- `status`: `pending`, `succeeded`, `failed`, `unauthorized`, `conflicted`, or `unavailable`.
- `signal_status`: optional subsystem signal summary.
- `snapshot`: current worker-pause snapshot.
- `failure_reason`: sanitized error text when command fails.
- `rollback_or_resume_path`: optional next safe action.

State transitions:
- `running` -> pause with `drain` -> `draining`
- `running` -> pause with `quiesce` -> `quiesced`
- `draining` or `quiesced` -> resume -> `running`
- any command -> subsystem failure -> previous state plus failed operation result

## OperationAuditEvent

Durable non-secret audit record backed by existing `SettingsAuditEvent`.

Fields:
- `event_type`: `operation_invoked`.
- `key`: operation key such as `operations.workers`.
- `scope`: `system`.
- `actor_user_id`: authenticated actor identifier.
- `new_value_json`: command/result metadata without credentials.
- `reason`: operator-provided reason.
- `created_at`: server timestamp.

Validation:
- Audit payloads must not include raw credentials, tokens, auth headers, environment dumps, or decrypted secret values.
- Latest audit response must expose only sanitized metadata needed by Settings.
