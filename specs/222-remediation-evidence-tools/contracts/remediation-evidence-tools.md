# Contract: Remediation Evidence Tools

## Service

`RemediationEvidenceToolService`

## Operations

- `get_context(remediation_workflow_id, principal?) -> dict`
- `read_target_artifact(remediation_workflow_id, artifact_ref, principal?) -> bytes`
- `read_target_logs(remediation_workflow_id, task_run_id, stream, cursor?, tail_lines?, principal?) -> RemediationLogReadResult`
- `follow_target_logs(remediation_workflow_id, task_run_id?, from_sequence?, principal?) -> RemediationLiveFollowResult`

## Invariants

- A persisted remediation link and linked `remediation.context` artifact are required.
- The context target workflow must match the persisted remediation link target.
- Artifact reads are limited to artifact IDs present in context evidence refs.
- Log reads and live follow are limited to taskRunIds present in context evidence or selected steps.
- Live follow requires `liveFollow.supported = true` and mode `follow` or `snapshot_then_follow`.
- This contract does not execute remediation actions.
