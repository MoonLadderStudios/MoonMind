# Feature Specification: Durable Task Edit Reconstruction

**Feature Branch**: `180-durable-task-edit-reconstruction`
**Created**: 2026-04-16
**Status**: Draft
**Input**: User description: "Define durable task edit reconstruction model

Define how MoonMind task edit and rerun functionality should fully recreate prior task
submissions, including complex task shapes and durable persistence requirements.

Context:
- A completed MoonMind.Run execution can expose actions.canRerun=true while the shared /tasks/new
rerun form cannot reconstruct task instructions.
- Example workflow: mm:f7af375e-59a3-4d39-a4c8-8e6353d1b9e8 had runtime/repository/branch/skill
data but no inputArtifactRef, no inputParameters.task.instructions, and no task step instructions.
The generated plan artifact contained synthesized execution instructions, but the edit/rerun form
does not currently treat plan artifacts as original operator input.
- The current reconstruction helper in frontend/src/lib/temporalTaskEditing.ts builds
taskInstructions only from inputParameters.task.instructions or
inputParameters.task.steps[].instructions, optionally after reading inputArtifactRef from
frontend/src/entrypoints/task-create.tsx.
- The create path in api_service/api/routers/executions.py persists normalized input parameters
and may store an inputArtifactRef only when provided by the client.

Goal:
Produce a concrete implementation task/spec that makes edit and rerun capable of faithfully
reconstructing the original create-form draft for all supported MoonMind.Run task types, not just
simple inline-instruction tasks. This task is definition/design work first; do not implement code
unless the resulting plan explicitly says it is safe and small enough to do in the same run.

Required analysis:
1. Inventory every create-form input that must round-trip through edit/rerun, including
instructions, multi-step tasks, selected skills, skill inputs, applied templates and template
inputs, runtime/profile/model/effort, repository, starting branch, target branch, publish
settings, dependencies, attachments, story-output settings, proposeTasks/proposalPolicy, and
schedule-related fields that should be excluded from edit/rerun.
2. Identify which values are currently persisted in inputParameters, record fields, memo/search
attributes, input artifacts, plan artifacts, step ledger, generated plan nodes, artifact links, or
other storage.
3. Classify each persisted value as authoritative original user input, derived runtime/planner
state, presentation metadata, or generated execution output.
4. Define the canonical durable source for edit/rerun reconstruction. Prefer a versioned original
task input snapshot artifact or equivalent immutable persistence object that captures the
operator-submitted create-form draft before normalization strips or synthesizes fields.
5. Define when that snapshot/artifact is created, how it is linked to the execution, how it is
exposed by GET /api/executions/{workflowId}, and how the frontend reads it.
6. Include how to handle complex tasks: skill-only submissions, template-derived tasks, tasks with
no explicit free-text instructions but an explicit selected skill and structured skill inputs,
multi-step tasks, oversized instructions externalized to artifacts, uploaded attachments, task
dependencies, and reruns of reruns.
7. Decide whether generated plan artifacts may be used only as a degraded fallback. If so, specify
clear UI copy and constraints so derived plan instructions are never mistaken for original
operator input.
8. Define validation and failure behavior: when to hide canRerun/canUpdateInputs, when to expose a
disabled reason, when to show a reconstructable read-only warning, and when to block submission.
9. Define migration/backfill behavior for existing executions that lack the new snapshot. MoonMind
is pre-release, so avoid broad compatibility layers, but provide an operator-safe cutover story
for already-created runs.
10. Specify actual persistence changes needed, including any new artifact link_type values,
metadata keys, execution record fields, API response fields, or workflow/activity boundary
payloads. Keep large content out of workflow history and use refs for large or mutable-looking
content.
11. Specify required tests at frontend, API, artifact, and Temporal/workflow boundary levels.
Include regression coverage for the workflow above or an equivalent skill-only execution with no
inline instructions.

Expected deliverables:
- A concise proposed design under docs/tmp/remaining-work/ or a new spec under specs/ using the
repo's global spec numbering rules if this becomes a non-trivial implementation project.
- A field-by-field reconstruction matrix that names the canonical persisted source for each form
value and whether it must be editable in rerun, editable in active edit, read-only, or omitted.
- A persistence/API contract proposal for an immutable original task input snapshot artifact,
including artifact type/content type/link_type/metadata, lifecycle, retention, and exposure
through execution detail.
- A concrete implementation breakdown with tests-first tasks.
- Clear acceptance criteria that prove edit/rerun can recreate simple inline tasks, artifact-
backed instruction tasks, skill-only tasks, template-derived tasks, multi-step tasks, attachment-
bearing tasks, and reruns of reruns.

Constraints:
- Read .specify/memory/constitution.md, README.md, docs/Tasks/TaskEditingSystem.md, docs/Temporal/
WorkflowArtifactSystemDesign.md, docs/Temporal/ArtifactPresentationContract.md, docs/Tools/
SkillSystem.md, and docs/Tasks/SkillAndPlanContracts.md before proposing changes.
- Preserve the distinction between original operator input and derived planner/runtime data.
- Do not embed large task content in workflow history; use artifacts/refs.
- Do not mutate checked-in skill folders or runtime skill snapshots.
- Respect the pre-release compatibility policy: do not add hidden aliases or broad compatibility
transforms for internal contracts.
- Any workflow/activity/update/signal payload changes must include boundary tests or an explicit
cutover plan.
- Final answer should include exact files/docs changed, tests run, and any unresolved decisions."
**Implementation Intent**: Runtime implementation is required, but this run is design/specification only. No production code is changed by this spec.

## User Story - Reopen Any Supported Task Draft

### Summary

As a MoonMind operator, I need Edit and Rerun to reopen the same create-form draft I originally submitted, even when the task used skills, templates, structured inputs, attachments, or artifact-backed instructions rather than plain inline text.

### Goal

Supported `MoonMind.Run` executions persist an immutable original task input snapshot at submission time and expose that snapshot through execution detail so the shared `/tasks/new` edit and rerun modes reconstruct the operator-submitted draft from authoritative original input, not from planner output or lossy normalized execution parameters.

### Independent Test

Create one execution for each supported create-form shape, load edit or rerun mode from its execution detail, and verify the reconstructed draft exactly matches the original editable submission fields while schedule-only fields are omitted and generated planner artifacts are never treated as original input.

### Acceptance Scenarios

1. **Given** a simple inline-instruction task, **When** the operator opens Edit or Rerun, **Then** the instructions, runtime/profile/model/effort, repository, branches, publish settings, proposal settings, dependencies, selected skills, and story-output settings are restored from the original input snapshot.
2. **Given** an oversized or artifact-backed instruction task, **When** the operator opens Edit or Rerun, **Then** the form reads the original snapshot ref and restores the full operator-entered content without mutating or reusing historical artifacts for later edits.
3. **Given** a skill-only task with no free-text instructions but with a selected agent skill and structured skill inputs, **When** the operator opens Edit or Rerun, **Then** the form reconstructs the selected skill and structured inputs and does not require synthesized plan instructions to make the draft valid.
4. **Given** a template-derived task, **When** the operator opens Edit or Rerun, **Then** the applied template identity, version, feature request, template inputs, generated draft fields, and manually customized steps are restored according to the original operator draft.
5. **Given** a multi-step task with attachments and per-step skill overrides, **When** the operator opens Edit or Rerun, **Then** every step's title, instructions, attachments, skill/tool selection, structured inputs, and template binding metadata are restored.
6. **Given** an execution that lacks the new snapshot but has a generated plan artifact, **When** the operator requests Rerun, **Then** the UI may show a read-only degraded reconstruction warning but must not present derived plan instructions as original operator input or submit them without explicit operator replacement.
7. **Given** a rerun of a rerun, **When** the operator opens Rerun again, **Then** the new run's own submitted snapshot is the source of truth while lineage records the prior source execution.

### Edge Cases

- Existing executions created before the cutover may have `actions.canRerun=true` but no reconstructable original snapshot.
- Snapshot artifact creation or linking may fail after the client submits a task but before workflow start.
- Uploaded attachments may be deleted, restricted, or not readable by the current operator.
- Local-only skills or runtime skill snapshots may no longer exist in the workspace, but the original selected skill names and resolved skillset refs remain audit evidence.
- A task may have template inputs that generated step instructions but no explicit primary instruction text.
- Active edit must omit schedule controls even when the source execution came from a one-time delay or recurring schedule definition.
- The operator may change fields that require new input artifacts or replacement attachment links; historical artifacts remain immutable.

## Requirements

### Functional Requirements

- **FR-001**: The create path MUST persist an immutable, versioned original task input snapshot for every supported `MoonMind.Run` submission before normalization or planner synthesis can remove operator-submitted fields.
- **FR-002**: The original task input snapshot MUST capture all create-form fields required for edit/rerun reconstruction, including instructions, steps, selected skills, skill inputs, applied templates, template inputs, runtime/profile/model/effort, repository, branches, publish settings, dependencies, attachments, story-output settings, `proposeTasks`, and `proposalPolicy`.
- **FR-003**: The original task input snapshot MUST explicitly exclude schedule-only controls from edit/rerun reconstruction while preserving enough schedule provenance for audit display outside the editable draft.
- **FR-004**: Large instructions, large structured inputs, and attachment bodies MUST remain outside workflow history; snapshots may carry refs and bounded metadata, not raw large blobs.
- **FR-005**: `GET /api/executions/{workflowId}` MUST expose a compact reconstruction descriptor that tells clients whether an authoritative snapshot exists, which artifact ref to read, what mode constraints apply, and why edit/rerun is disabled when reconstruction is unsafe.
- **FR-006**: The shared `/tasks/new` edit/rerun form MUST prefer the original task input snapshot over `inputParameters`, record fields, memo/search attributes, input artifacts, plan artifacts, step ledgers, or generated plan nodes.
- **FR-007**: Generated plan artifacts, generated plan nodes, step ledgers, and runtime output artifacts MUST be classified as derived output and MAY be used only for explicitly labeled degraded, read-only recovery assistance when no original snapshot exists.
- **FR-008**: The backend MUST hide or disable `actions.canUpdateInputs` and `actions.canRerun` when a supported execution cannot be reconstructed into a valid editable draft, and MUST expose a bounded disabled reason.
- **FR-009**: Edit and rerun submission MUST block when the draft came only from degraded derived output until the operator supplies replacement authoritative input through the form.
- **FR-010**: Skill-only tasks MUST reconstruct without requiring `task.instructions` or step instruction text when the original snapshot contains a selected skill or runtime command plus structured inputs sufficient for the submitted task shape.
- **FR-011**: Template-derived tasks MUST reconstruct applied template identity, version, feature request, template input values, step IDs, customized steps, and capabilities from the original snapshot rather than from the current template catalog.
- **FR-012**: Multi-step and attachment-bearing tasks MUST reconstruct step order, step IDs, titles, instructions, skill/tool overrides, structured inputs, and attachment refs with readable-state validation.
- **FR-013**: Reruns MUST create a new original task input snapshot for the rerun request and link it to the new or continued execution, while preserving source-run lineage separately.
- **FR-014**: Existing executions without snapshots MUST follow a pre-release cutover policy: no broad compatibility shim, no hidden plan-to-input transform, and an operator-visible degraded/read-only path or disabled rerun with a clear reason.
- **FR-015**: Any workflow/activity/update/signal payload change required by this feature MUST carry compact refs only and include boundary tests or a documented cutover plan.
- **FR-016**: The implementation MUST include frontend, API, artifact, and Temporal/workflow boundary tests proving reconstruction across simple inline, artifact-backed, skill-only, template-derived, multi-step, attachment-bearing, and rerun-of-rerun cases.

### Key Entities

- **OriginalTaskInputSnapshot**: Immutable artifact payload containing the versioned operator-submitted create-form draft plus refs for large content and attachments.
- **ReconstructionDescriptor**: Compact execution-detail API field that points to the authoritative snapshot artifact and reports edit/rerun availability, degraded fallback state, and disabled reasons.
- **ReconstructedDraft**: Frontend create-form draft produced from the snapshot and used by Edit or Rerun.
- **DerivedExecutionEvidence**: Plan artifacts, generated plan nodes, step ledgers, runtime output artifacts, memo/search attributes, and normalized input parameters that may assist display but are not authoritative original input.
- **RerunLineage**: Relationship between source execution, rerun request, replacement input snapshot, and resulting execution/run context.

## Assumptions

- The initial implementation targets `MoonMind.Run` only, matching the current task editing scope.
- The existing artifact store and execution-artifact linking system are the durable persistence mechanism for snapshots.
- Current template catalog rows may drift over time, so snapshot reconstruction must not require fetching the current template definition to restore the original draft.
- Existing pre-cutover executions may remain non-rerunnable when no authoritative original input can be reconstructed safely.

## Success Criteria

- **SC-001**: 100% of newly created supported `MoonMind.Run` executions expose an authoritative reconstruction snapshot descriptor through execution detail.
- **SC-002**: Frontend reconstruction tests demonstrate exact draft restoration for simple inline, artifact-backed, skill-only, template-derived, multi-step, attachment-bearing, and rerun-of-rerun task shapes.
- **SC-003**: API and artifact tests prove snapshot artifacts are immutable, linked with the correct semantic link type, retained for the execution audit window, and never embedded as large content in workflow history.
- **SC-004**: Workflow boundary tests prove only compact snapshot refs cross Temporal boundaries and that `UpdateInputs`/`RequestRerun` preserve the snapshot/ref contract.
- **SC-005**: Existing executions without snapshots either hide/disable edit/rerun with a reason or show a read-only degraded warning; no test path silently treats generated plan instructions as original operator input.
- **SC-006**: Regression coverage includes the reported failure shape, or an equivalent skill-only execution with runtime/repository/branch/skill data, no `inputArtifactRef`, no inline task instructions, and only generated plan instructions.
