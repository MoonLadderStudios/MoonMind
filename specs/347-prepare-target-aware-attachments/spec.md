# Feature Specification: Prepare-Time Target-Aware Attachment Materialization

**Feature Branch**: `[347-prepare-target-aware-attachments]`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: "# MM-648 MoonSpec Orchestration Input

## Source

- Jira issue: MM-648
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Prepare-time target-aware attachment materialization without retargeting
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`, and preset-adjacent custom fields were empty or unrelated.

## Canonical MoonSpec Feature Request

Jira issue: MM-648 from MM project
Summary: Prepare-time target-aware attachment materialization without retargeting
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-648 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-648: Prepare-time target-aware attachment materialization without retargeting

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.2 Artifact-first binary handling
- 8.2 Prepare responsibilities
- Invariant 1
- Invariant 3
- Invariant 11
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-020
- DESIGN-REQ-029

As an execution-plane engineer, I want the prepare activity to download objective-scoped and step-scoped attachments, write a canonical attachments manifest, materialize raw files into stable workspace locations, produce target-aware image context artifacts, fail explicitly on incomplete/invalid preparation, and never silently retarget an attachment when steps are reordered, presets applied, or text edited.

Acceptance Criteria
- Objective and step attachments download to distinct, stable workspace locations.
- A canonical attachments manifest is written that names target kind and step reference for every attachment.
- Target-aware image context artifacts are produced per target.
- Prepare fails explicitly on missing/invalid attachments rather than silently dropping them.
- Step reorder, preset apply, and text edits never silently retarget an existing attachment.
- No binary attachment bytes appear in workflow history.

Requirements
Implement prepare activity behaviors, manifest writer, target-aware context generation, and contract-level guards against retargeting at the snapshot/normalizer boundary.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

## User Story - Target-Aware Attachment Preparation

**Summary**: As an execution-plane engineer, I want preparation to materialize objective-scoped and step-scoped attachments with explicit target metadata so that execution never loses, mixes, or silently retargets attachments.

**Goal**: Attachment-aware task execution prepares every authored attachment into stable workspace files and target-aware context artifacts while preserving the original objective or step binding across preparation, prompt composition, and diagnostic evidence.

**Independent Test**: Submit or exercise a prepared task payload containing objective and step attachments, then verify the prepared outputs include distinct workspace files, a canonical manifest naming each attachment target, per-target image context artifacts, explicit failures for invalid attachment refs, and no inline binary payloads in workflow-visible data.

**Acceptance Scenarios**:

1. **Given** a task with objective-scoped and step-scoped attachments, **When** preparation runs, **Then** each attachment is downloaded to a stable workspace location scoped to its original target and no target receives another target's file.
2. **Given** prepared attachment outputs, **When** diagnostics or downstream prompt composition reads the attachments manifest, **Then** every manifest entry names the attachment artifact, target kind, step reference when applicable, stable file path, and derived context refs.
3. **Given** image attachments on both the objective and individual steps, **When** image context generation completes, **Then** the system produces target-aware context artifacts per objective or step target instead of one ambiguous attachment bucket.
4. **Given** an attachment ref is missing, unauthorized, incomplete, or invalid, **When** preparation runs, **Then** preparation fails explicitly with the affected target and reason rather than dropping the attachment or continuing with partial state.
5. **Given** task steps are reordered, presets are applied, or text is edited before submission, **When** normalization and preparation process existing attachment refs, **Then** the attachment remains bound to its explicit target identity and is never silently retargeted by list position or text content.
6. **Given** prepared attachment evidence is stored in workflow-visible payloads, **When** those payloads are inspected, **Then** they contain only lightweight refs and metadata, never binary attachment bytes.

### Edge Cases

- Objective-scoped attachments and step-scoped attachments may reference files with the same original filename; prepared paths must remain distinct and deterministic.
- A step attachment without a stable step reference must fail validation instead of binding by array index.
- Duplicate artifact refs across targets must be represented as separate target bindings without overwriting target metadata.
- Non-image attachments must still be materialized and listed in the manifest even when no image context artifact is produced.
- Partial preparation failures must not leave outputs that downstream steps can mistake for complete preparation.

## Assumptions

- Existing artifact storage remains the binary source of truth; this story does not add new persistent storage.
- The existing task-shaped contract already carries objective-level and step-level attachment refs; this story strengthens preparation and boundary validation for those refs.
- Stable step references can use existing authored step IDs or normalized logical step identifiers already available in task payloads.

## Source Design Requirements

- **DESIGN-REQ-002**: Source `docs/Tasks/TaskArchitecture.md` section 3.2 and Invariant 1 require binary inputs to be stored as artifacts and referenced by lightweight refs, with no binary payloads in workflow history. Scope: in scope. Mapped requirements: FR-001, FR-008.
- **DESIGN-REQ-020**: Source `docs/Tasks/TaskArchitecture.md` section 8.2 requires prepare to download objective-scoped and step-scoped attachments, write a canonical attachments manifest, materialize raw files into stable workspace locations, produce target-aware image context artifacts, and fail explicitly when preparation is incomplete or invalid. Scope: in scope. Mapped requirements: FR-002, FR-003, FR-004, FR-005, FR-006, FR-007.
- **DESIGN-REQ-029**: Source `docs/Tasks/TaskArchitecture.md` Invariant 11 requires step reordering, preset application, and text edits to never silently retarget an existing attachment to another step. Scope: in scope. Mapped requirements: FR-004, FR-006, FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST consume task attachment inputs as artifact refs and metadata only, never inline binary bytes in workflow-visible payloads.
- **FR-002**: System MUST download objective-scoped attachments and step-scoped attachments during preparation into stable, target-distinct workspace locations.
- **FR-003**: System MUST write a canonical attachments manifest for preparation outputs.
- **FR-004**: Each manifest entry MUST identify the source artifact, original filename, target kind, target reference, stable workspace path, content type, and preparation status.
- **FR-005**: System MUST produce image context artifacts grouped by explicit target so objective context and each step context remain distinguishable.
- **FR-006**: System MUST preserve existing attachment target identity across step reorder, preset apply, and text edit normalization paths without rebinding by array index or text position.
- **FR-007**: System MUST fail preparation explicitly when an attachment is missing, unauthorized, incomplete, invalid, or cannot be materialized, and the failure MUST identify the affected target.
- **FR-008**: System MUST expose only lightweight refs to prepared files, manifests, and context artifacts across workflow/activity boundaries.
- **FR-009**: System MUST reject or fail task normalization when a step-scoped attachment cannot be associated with a stable step reference.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-648` and the canonical Jira preset brief.

### Key Entities

- **Attachment Target**: The authored destination for an attachment, either the task objective or a specific task step, identified by target kind and stable target reference.
- **Prepared Attachment**: A materialized attachment file associated with one attachment target, source artifact metadata, workspace path, status, and optional context refs.
- **Attachments Manifest**: The canonical preparation output that records every prepared attachment and target-aware context ref for downstream execution and diagnostics.
- **Target-Aware Image Context**: A derived context artifact generated for one explicit attachment target rather than a global attachment bucket.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A mixed objective-and-step attachment task produces one manifest entry for every authored attachment with correct target kind and target reference.
- **SC-002**: Unit coverage proves step reorder, preset application, and text edit normalization do not change existing attachment target bindings.
- **SC-003**: Integration coverage proves preparation fails with an explicit target-specific error for missing or invalid attachment refs.
- **SC-004**: Integration coverage proves image context outputs are grouped per target and downstream payloads carry refs rather than binary bytes.
- **SC-005**: Final verification can trace `MM-648`, DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029 through spec, plan, tasks, tests, and implementation evidence.
