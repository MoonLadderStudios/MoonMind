# Data Model: Durable Task Edit Reconstruction

## OriginalTaskInputSnapshot

Immutable artifact payload that captures the operator-submitted create-form draft before backend normalization.

Fields:

- `snapshotVersion`: integer, initially `1`.
- `source`: object with `kind` (`create`, `edit`, `rerun`), optional `sourceWorkflowId`, optional `sourceRunId`, and optional `submittedAt`.
- `draft`: object matching the versioned create-form draft contract.
- `largeContentRefs`: bounded map from draft paths to artifact refs for oversized content.
- `attachmentRefs`: list of uploaded artifact refs with draft path, filename, content type, size, and read state metadata.
- `lineage`: object with rerun source and prior snapshot ref when the snapshot is produced from edit or rerun.
- `excluded`: object documenting omitted schedule controls and why they are not editable.

Validation rules:

- `snapshotVersion` is required.
- `draft.taskShape` is required and must be one of `inline_instructions`, `skill_only`, `template_derived`, `multi_step`, or `artifact_backed`.
- Large content is represented by refs, not embedded when above inline limits.
- Attachment bodies are never embedded.
- Schedule controls are excluded from the editable draft.
- Skill selection and structured inputs may satisfy task objective requirements when instructions are absent.

## TaskInputSnapshotDescriptor

Compact execution-detail field returned by `GET /api/executions/{workflowId}`.

Fields:

- `available`: boolean.
- `artifactRef`: nullable string.
- `snapshotVersion`: nullable integer.
- `sourceKind`: `create`, `edit`, `rerun`, or `unknown`.
- `reconstructionMode`: `authoritative`, `degraded_read_only`, or `unavailable`.
- `disabledReasons`: map keyed by `canUpdateInputs`, `canRerun`, and `draft`.
- `fallbackEvidenceRefs`: optional bounded list of plan/input/output refs used only for read-only recovery assistance.

Validation rules:

- `reconstructionMode=authoritative` requires a readable snapshot artifact.
- `degraded_read_only` must not enable submit until operator supplies replacement input.
- `unavailable` must include at least one disabled reason.

## ReconstructedDraft

Frontend state derived from `OriginalTaskInputSnapshot`.

Fields:

- `runtime`, `providerProfile`, `model`, `effort`.
- `repository`, `startingBranch`, `targetBranch`.
- `publishMode` and publish options supported by the current form.
- `taskInstructions` and instruction refs where applicable.
- `primarySkill`, `primarySkillArgs`, runtime command selection where applicable.
- `steps` with stable order, IDs, titles, instructions, tool/skill overrides, inputs, attachments, and template binding metadata.
- `appliedTemplates` with slug, version, scope, feature request, inputs, step IDs, applied time, and capabilities.
- `selectedDependencies`.
- `storyOutput`.
- `proposeTasks` and `proposalPolicy`.
- `reconstructionWarnings`.

State transitions:

- `authoritative snapshot -> editable draft`.
- `degraded evidence -> read-only recovery preview`.
- `read-only recovery preview + operator replacement -> editable draft`.
- `editable draft submit -> new snapshot artifact + update/rerun payload ref`.

## Artifact Linkage

New semantic linkage:

- `link_type`: `input.original_snapshot`
- Label: `Original task input snapshot`
- Retention: `long` by default; may be pinned with execution audit retention.
- Content type: `application/vnd.moonmind.task-input-snapshot+json;version=1`
- Artifact type metadata: `task.input.original_snapshot`

Metadata keys:

- `artifact_class`: `input.original_snapshot`
- `snapshot_version`: `1`
- `workflow_type`: `MoonMind.Run`
- `source_kind`: `create`, `edit`, or `rerun`
- `source_workflow_id`: present for rerun/edit lineage when applicable
- `draft_shape`: primary task shape
- `schema_name`: `OriginalTaskInputSnapshot`
- `created_by`: bounded principal identifier

## Existing Storage Classification

Authoritative original user input:

- New original snapshot artifact.
- Existing input artifact only when explicitly marked as an original input snapshot or referenced by the snapshot as large content.
- Uploaded attachment artifacts linked from the snapshot.

Derived runtime/planner state:

- `inputParameters` after normalization.
- Plan artifacts and generated plan nodes.
- Step ledger entries.
- Runtime diagnostics, output artifacts, and generated summaries.
- Resolved model when different from requested model.

Presentation metadata:

- Execution title and summary in memo.
- Search attributes such as owner, repo, state, and updated time.
- `actions` capability booleans and disabled reasons.

Generated execution output:

- Plan outputs, runtime stdout/stderr, diagnostics, provider result snapshots, proposed follow-up tasks, PR/publish artifacts, and story-output result artifacts.
