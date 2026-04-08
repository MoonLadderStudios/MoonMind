# Contract: Session Plane Observability Events

## Goal

Define the Phase 2 production mapping between managed-session actions and the shared `RunObservabilityEvent` timeline contract.

## Control/lifecycle mapping

| Boundary | Event stream | Event kind | Notes |
| --- | --- | --- | --- |
| fresh session launch | `session` | `session_started` | emitted from the controller after launch succeeds |
| reuse existing runtime handles | `session` | `session_resumed` | emitted from the controller when `session_status` is used to resume an existing session |
| send turn accepted/completed | `session` | `turn_started`, `turn_completed` | emitted from the controller with current turn and session snapshot metadata |
| steer active turn | `session` | `system_annotation` | normalized passive row with `metadata.action = "steer_turn"` |
| interrupt active turn | `session` | `turn_interrupted` | emitted when interruption succeeds |
| clear session | `session` | `session_cleared`, `session_reset_boundary` | clear row plus explicit epoch boundary row |
| terminate session | `session` | `session_terminated` | explicit lifecycle row instead of a generic system line |
| publish summary | `session` | `summary_published` | emitted from supervisor publication path |
| publish checkpoint | `session` | `checkpoint_published` | emitted from supervisor publication path |

## Failure rule

Session-event publication is best-effort:

- log the failure locally
- do not mutate the control result into a failure
- do not suppress durable summary/checkpoint/control/reset artifact writes that otherwise succeeded
