# Feature Specification: Materialize Attachment Manifest and Workspace Files

**Feature Branch**: `197-attachment-materialization`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-370 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-370-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-370 from MM project
Summary: Materialize attachment manifest and workspace files
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-370 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-370: Materialize attachment manifest and workspace files

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 3.2 Canonical terminology
  - 4. End-to-end desired-state flow
  - 8. Prepare-time materialization contract
- Coverage IDs:
  - DESIGN-REQ-002
  - DESIGN-REQ-004
  - DESIGN-REQ-011

User Story
As a runtime executor, I need workflow prepare to deterministically download declared input attachments, write a canonical manifest, and place files in target-aware workspace paths before the relevant runtime or step executes.

Acceptance Criteria
- Prepare downloads all declared input attachments before the relevant runtime or step executes.
- Prepare writes `.moonmind/attachments_manifest.json` using the canonical manifest entry shape.
- Objective images are materialized under `.moonmind/inputs/objective/`.
- Step images are materialized under `.moonmind/inputs/steps/<stepRef>/`.
- Workspace paths are deterministic and target-aware; one target path does not depend on unrelated target ordering.
- A stable step reference is assigned when a step has no explicit id.
- Partial materialization is reported as failure, not best-effort success.

Requirements
- Include `artifactId`, `filename`, `contentType`, `sizeBytes`, `targetKind`, optional `stepRef`/`stepOrdinal`, `workspacePath`, and optional context/source paths in manifest entries.
- Sanitize filenames while preserving deterministic artifactId-prefixed paths.
- Treat execution payload and snapshot refs as the source for materialization.
- Preserve canonical target meaning from the field that contains each ref: objective-scoped attachments from `task.inputAttachments` and step-scoped attachments from `task.steps[n].inputAttachments`.
- Materialize objective-scoped attachments under `.moonmind/inputs/objective/<artifactId>-<sanitized-filename>`.
- Materialize step-scoped attachments under `.moonmind/inputs/steps/<stepRef>/<artifactId>-<sanitized-filename>`.
- Ensure attachment identity and target meaning do not depend on filename conventions or unrelated target ordering.
- Keep raw attachment bytes out of Temporal histories and task instruction text; runtime adapters consume structured refs, materialized files, and derived context.
- Fail explicitly when any declared attachment cannot be downloaded, written, or represented in the manifest.

Relevant Implementation Notes
- The canonical control-plane field name is `inputAttachments`.
- The canonical prepared manifest entry shape is `AttachmentManifestEntry` from `docs/Tasks/ImageSystem.md`.
- Workflow prepare owns deterministic local file materialization.
- Upload completion and execution snapshot persistence happen before workflow prepare receives refs.
- The execution API snapshot is the authoritative source for attachment targeting.
- Runtime adapters consume structured refs and derived context, not browser-local state.
- Target-aware workspace paths must be stable for objective and step targets independently.
- If a step has no explicit `id`, the control plane or prepare layer must assign a stable step reference for manifest and path purposes.
- Partial materialization must stop execution as a failure, preserving diagnostics for the missing or invalid attachment.

Suggested Implementation Areas
- Workflow prepare or artifact materialization activities that download input attachments.
- Manifest writer for `.moonmind/attachments_manifest.json`.
- Workspace path generation and filename sanitization helpers.
- Stable step reference assignment for steps without explicit ids.
- Execution payload or snapshot parsing for objective-scoped and step-scoped attachment refs.
- Failure handling and diagnostics for partial materialization.
- Unit and workflow/activity-boundary tests covering manifest shape, path determinism, target isolation, and failure behavior.

Validation
- Verify prepare downloads every declared objective-scoped and step-scoped input attachment before the relevant runtime or step executes.
- Verify `.moonmind/attachments_manifest.json` includes the canonical fields for every materialized attachment.
- Verify objective-scoped attachments are written under `.moonmind/inputs/objective/`.
- Verify step-scoped attachments are written under `.moonmind/inputs/steps/<stepRef>/`.
- Verify generated workspace paths are deterministic and independent of unrelated target ordering.
- Verify steps without explicit ids receive stable step references for path and manifest purposes.
- Verify a missing, invalid, or failed attachment download produces an explicit failure instead of best-effort success.
- Verify raw attachment bytes are not embedded in Temporal histories or task instruction text.

Non-Goals
- Generating target-aware vision context artifacts; that is covered by a separate story.
- Preserving attachment bindings across edit and rerun reconstruction; that is covered by a separate story.
- Enforcing upload policy, content-type policy, or artifact completion integrity beyond consuming already-declared refs.
- Inferring target bindings from filenames, artifact links, or artifact metadata.
- Adding generic non-image attachment support beyond the existing image input materialization contract.

Needs Clarification
- None

<!-- Moon Spec specs contain exactly one independently testable user story. Use /moonspec-breakdown for technical designs that contain multiple stories. -->

## User Story - Materialize Attachment Inputs During Prepare

**Summary**: As a runtime executor, I need workflow prepare to deterministically download declared input attachments, write a canonical manifest, and place files in target-aware workspace paths before the relevant runtime or step executes.

**Goal**: Every runtime or step that receives declared image attachments can rely on local files and a canonical manifest that preserve objective-scoped and step-scoped target meaning without depending on filenames, artifact metadata, or unrelated attachment ordering.

**Independent Test**: Start a task-shaped execution containing objective-scoped and step-scoped input attachment refs, including a step without an explicit id, and verify prepare downloads all declared attachments, writes `.moonmind/attachments_manifest.json`, materializes files under deterministic target-aware workspace paths, and fails explicitly if any attachment cannot be materialized.

**Acceptance Scenarios**:

1. **Given** a task execution contains completed objective-scoped input attachment refs, **When** workflow prepare runs, **Then** each declared attachment is downloaded before runtime execution, materialized under `.moonmind/inputs/objective/`, and represented in `.moonmind/attachments_manifest.json` with the canonical manifest fields.
2. **Given** a task execution contains completed step-scoped input attachment refs, **When** workflow prepare runs, **Then** each declared attachment is downloaded before the relevant step executes, materialized under `.moonmind/inputs/steps/<stepRef>/`, and represented in the manifest with `targetKind`, `stepRef`, and `stepOrdinal` when applicable.
3. **Given** a step with input attachments has no explicit id, **When** workflow prepare generates workspace paths and manifest entries, **Then** the system assigns a stable step reference so repeated prepare runs for the same payload produce the same step directory and manifest target.
4. **Given** objective and step attachments are supplied in different target groups or orderings, **When** prepare generates workspace paths, **Then** each target path is deterministic and does not depend on unrelated target ordering.
5. **Given** any declared attachment cannot be downloaded, written, sanitized, or represented in the manifest, **When** prepare runs, **Then** execution fails explicitly with materialization diagnostics rather than continuing with partial inputs.

### Edge Cases

- Objective-scoped and step-scoped attachments use the same filename.
- A filename contains unsafe path characters or an empty sanitized basename.
- Multiple steps omit explicit ids while carrying step-scoped attachments.
- Attachments are reordered within one target or unrelated targets are added.
- A download succeeds but local file writing or manifest writing fails.
- A declared attachment ref is missing a required canonical field.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/Tasks/ImageSystem.md` is treated as source requirements for runtime behavior.
- Attachment upload policy and artifact completion validation have already accepted the declared refs before prepare runs.
- The canonical control-plane field remains `inputAttachments`.
- Objective-scoped attachments are represented by `task.inputAttachments`; step-scoped attachments are represented by `task.steps[n].inputAttachments`.
- Raw attachment bytes must remain outside task instruction text and Temporal histories.

## Source Design Requirements

- **DESIGN-REQ-002** (Source: `docs/Tasks/ImageSystem.md`, section 3.2; MM-370 brief): The system MUST preserve canonical target meaning from `inputAttachments`, using objective-scoped refs from `task.inputAttachments`, step-scoped refs from `task.steps[n].inputAttachments`, and canonical prepared-manifest fields for every materialized attachment. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-007.
- **DESIGN-REQ-004** (Source: `docs/Tasks/ImageSystem.md`, section 4; MM-370 brief): Runtime prepare MUST consume structured refs from the execution snapshot, not browser-local state, and prepare MUST complete deterministic materialization before runtime or step execution. Scope: in scope. Maps to FR-001, FR-004, FR-008, FR-010.
- **DESIGN-REQ-011** (Source: `docs/Tasks/ImageSystem.md`, section 8; MM-370 brief): Workflow prepare MUST download all declared input attachments, write `.moonmind/attachments_manifest.json`, materialize raw files into stable target-aware locations, assign stable step references when needed, and treat partial materialization as failure. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-005, FR-006, FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Workflow prepare MUST download every declared objective-scoped and step-scoped input attachment before the relevant runtime or step executes.
- **FR-002**: Workflow prepare MUST write `.moonmind/attachments_manifest.json` with one manifest entry per materialized attachment.
- **FR-003**: Each manifest entry MUST include `artifactId`, `filename`, `contentType`, `sizeBytes`, `targetKind`, `workspacePath`, optional `stepRef`, optional `stepOrdinal`, and optional derived context or source paths when those artifacts exist.
- **FR-004**: Manifest target meaning MUST be derived from the structured field containing the attachment ref, not from filenames, artifact links, artifact metadata, or browser-local state.
- **FR-005**: Objective-scoped attachments MUST be materialized under `.moonmind/inputs/objective/<artifactId>-<sanitized-filename>`.
- **FR-006**: Step-scoped attachments MUST be materialized under `.moonmind/inputs/steps/<stepRef>/<artifactId>-<sanitized-filename>`.
- **FR-007**: Filename sanitization MUST prevent path traversal or unsafe workspace paths while preserving deterministic artifactId-prefixed output names.
- **FR-008**: Workspace paths MUST be deterministic for a given attachment target and MUST NOT depend on unrelated target ordering.
- **FR-009**: Steps without explicit ids that carry step-scoped attachments MUST receive stable step references for manifest and workspace path purposes.
- **FR-010**: Prepare MUST fail explicitly when any declared attachment cannot be downloaded, written, sanitized, or represented in the manifest, and MUST NOT report partial materialization as success.
- **FR-011**: Raw attachment bytes MUST NOT be embedded in Temporal histories, execution payloads, or task instruction text as part of prepare-time materialization.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-370` and the original Jira preset brief for traceability.

### Key Entities

- **Input Attachment Ref**: A structured reference to an uploaded input attachment, including artifact identity, filename, content type, and byte size.
- **Attachment Target**: The objective-scoped or step-scoped location that owns an input attachment ref.
- **Attachment Manifest Entry**: A durable prepare output describing one materialized attachment, its source ref, target binding, local workspace path, and optional derived artifact paths.
- **Stable Step Reference**: The deterministic identifier used for manifest entries and workspace directories when a step has no explicit id.
- **Materialized Attachment File**: The local workspace copy of an input attachment available to the runtime or step executor.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage verifies objective-scoped attachments are downloaded, materialized under `.moonmind/inputs/objective/`, and listed in `.moonmind/attachments_manifest.json`.
- **SC-002**: Automated coverage verifies step-scoped attachments are downloaded, materialized under `.moonmind/inputs/steps/<stepRef>/`, and listed in the manifest with step target fields.
- **SC-003**: Automated coverage verifies steps without explicit ids receive stable step references for manifest and path generation.
- **SC-004**: Automated coverage verifies workspace paths are deterministic and independent of unrelated target ordering.
- **SC-005**: Automated coverage verifies unsafe filenames are sanitized without losing deterministic artifactId-prefixed naming.
- **SC-006**: Automated coverage verifies missing, invalid, or failed attachment materialization produces an explicit failure and does not report partial success.
- **SC-007**: Final verification confirms `MM-370` and the original Jira preset brief are preserved in the active MoonSpec artifacts and delivery metadata.
