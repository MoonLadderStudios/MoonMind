# Data Model: Step Ledger Checkpoint Durability

## Prepared Input Evidence

- `manifestRef`: durable ref to the prepared input manifest for a task run.
- `preparedArtifactRefs`: compact list of prepared input refs that may be reused during safe Resume.
- `targetCounts`: bounded counts for objective and step-scoped prepared inputs.

Validation:

- refs must be non-empty strings.
- inline attachment content, generated markdown, data URLs, or binary bodies are forbidden.
- objective and step-scoped refs remain distinguishable.

## Step Ledger Row Evidence

- `logicalStepId`: stable step id from the execution plan.
- `attempt`: current source attempt for the logical step.
- `artifacts`: semantic output refs such as output summary, output primary, runtime logs, diagnostics, and provider snapshot.
- `refs`: parent-visible child workflow/run/task-run lineage.
- `stateCheckpointRef`: durable ref to recoverable workspace, branch, commit, or equivalent state for the completed step.
- `resumePreservation`: proposed bounded eligibility object:
  - `eligible`: boolean.
  - `reason`: short machine-readable reason, such as `complete`, `missing_output_refs`, `missing_state_checkpoint`, or `not_completed`.
  - `message`: optional operator-readable bounded explanation.

Validation:

- completed steps can be Resume-preserved only when they have at least one semantic output ref and required state checkpoint evidence.
- ineligible completed steps must carry a bounded reason.
- child/delegated step evidence is projected onto the parent row; child histories do not replace the parent row.

## Resume Checkpoint

- `schemaVersion`: currently `v1`.
- `source`: source workflow id and run id.
- `taskInputSnapshotRef`: authoritative source task input snapshot.
- `planRef` or `planDigest`: stable plan identity.
- `failedStep`: logical failed-step identity and attempt.
- `preservedSteps`: completed steps with semantic artifact refs and `stateCheckpointRef`.
- `preparedArtifactRefs`: prepared refs reusable during Resume.
- `resumeWorkspace`: bounded workspace, branch, commit, or equivalent state metadata.

Validation:

- plan identity or digest is required.
- workspace evidence is required.
- preserved steps require at least one semantic artifact ref and a state checkpoint ref.
- large/binary checkpoint content remains behind refs.

## State Transitions

1. Preparation succeeds -> prepared refs are recorded in parent evidence.
2. Step starts -> live row attempt and start state update.
3. Step succeeds -> semantic output refs are recorded.
4. Mutating step boundary completes -> state checkpoint ref is recorded idempotently.
5. Completed step evidence is evaluated -> `resumePreservation` is set eligible or ineligible with reason.
6. Run fails later -> resume checkpoint contains preserved eligible steps and prepared refs for recovery.
