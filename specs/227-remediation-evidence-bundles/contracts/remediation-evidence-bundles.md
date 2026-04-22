# Contract: Remediation Evidence Bundles

## Service Boundary

`RemediationEvidenceToolService` exposes the trusted remediation evidence boundary:

- `get_context(remediation_workflow_id, principal?) -> dict`
- `read_target_artifact(remediation_workflow_id, artifact_ref, principal?) -> bytes`
- `read_target_logs(remediation_workflow_id, task_run_id, stream, cursor?, tail_lines?, principal?) -> RemediationLogReadResult`
- `follow_target_logs(remediation_workflow_id, task_run_id?, from_sequence?, principal?) -> RemediationLiveFollowResult`
- `prepare_action_request(remediation_workflow_id, action_kind, principal?) -> RemediationActionRequestPreparation`

## Preconditions

- A `TemporalExecutionRemediationLink` exists for the remediation workflow.
- A linked `remediation.context` artifact exists before evidence reads.
- Artifact/log/live-follow reads are limited to context-declared refs and taskRunIds.
- `prepare_action_request` validates the same linked context before reading current target health.

## Failure Behavior

- Missing links, missing context artifacts, invalid context artifacts, target mismatches, undeclared refs, unsupported streams, unsupported live follow, and missing target records fail fast with `RemediationEvidenceToolError`.
- Missing optional evidence is represented as bounded degradation in the context, not as an unbounded wait.

## Non-Goals

- No raw storage access.
- No raw shell, SQL, Docker, or credential access.
- No side-effecting action execution registry in this story.
