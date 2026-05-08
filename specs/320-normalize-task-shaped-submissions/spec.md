# Feature Specification: Normalize Task-Shaped Submissions

**Feature Branch**: `320-normalize-task-shaped-submissions`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
For a single-story Jira preset brief, run moonspec-specify unless an active spec.md already passes the specify gate.
For a broad technical or declarative design, run moonspec-breakdown first, then select the recommended first generated spec unless the issue brief explicitly requires processing all specs.
Preserve Jira issue MM-627 and the original preset brief in spec.md so final verification can compare against them.

Canonical Jira preset brief:

MM-627: Normalize task-shaped submissions with explicit attachment targets

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.1 Task-first control plane
- 3.3 Explicit target binding
- 5.1 Authoring and validation
- 5.3 Task contract normalization
- 6 Canonical task-shaped contract
- 11 Invariants
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-003
- DESIGN-REQ-006
- DESIGN-REQ-008
- DESIGN-REQ-011
- DESIGN-REQ-025
As a Mission Control user, I want submitted tasks to preserve objective text, steps, repository/runtime/publish choices, dependencies, Jira provenance, and objective- or step-scoped attachments so execution receives exactly the task I authored.
Acceptance Criteria
- Create/edit/rerun submission accepts valid task-shaped payloads with objective-scoped and step-scoped attachment refs.
- The normalized payload preserves attachment arrays, step IDs/order, runtime intent, publish mode, task.git.branch, dependencies, and Jira provenance where supported.
- New payloads do not emit targetBranch; task.git.branch carries the single authored branch semantics.
- Reordering steps, changing text, or applying authored data cannot silently retarget attachments.
- Invalid repository, runtime, publish, dependency, attachment policy, or target-binding inputs fail explicitly.
Requirements
- Users author tasks while the control plane translates intent into execution contracts and the execution plane owns lifecycle progression.
- Each input attachment binds to the task objective or a declared step, and binding survives every flow.
- The Create page/control plane validates repository, runtime, publish mode, dependencies, attachments, and branch/publish placement.
- The task-shaped payload preserves task fields, step identity/order, runtime/publish intent, branch, dependencies, attachments, Jira provenance, and preset metadata.
- task.git.branch is the only authored branch field; publish mode determines PR base versus branch update semantics.
- Reordering, presets, text changes, aliases, or migration layers must not silently retarget attachments or alter objective-versus-step meaning.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-627 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Current Jira status at orchestration input fetch time: In Progress

Canonical source note: synthesized from trusted jira.get_issue response fields because the response did not expose recommendedImports.presetInstructions, normalizedPresetBrief, presetBrief, presetInstructions, recommendedPresetInstructions, or acceptanceCriteriaText.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Preserve Authored Task Submission Shape

**Summary**: As a Mission Control user, I want submitted tasks to preserve objective text, steps, repository/runtime/publish choices, dependencies, Jira provenance, and objective- or step-scoped attachments so execution receives exactly the task I authored.

**Goal**: A user can create, edit, or rerun a task and trust that MoonMind preserves the authored task shape, explicit attachment targets, branch intent, dependencies, runtime/publish choices, Jira provenance, and preset metadata through submission normalization.

**Independent Test**: Submit create, edit, and rerun task drafts that include objective text, ordered steps, objective-scoped attachments, step-scoped attachments, repository/runtime/publish choices, dependencies, Jira provenance, branch intent, and preset metadata; then verify accepted submissions preserve every authored binding and invalid or ambiguous submissions fail before execution receives altered task data.

**Acceptance Scenarios**:

1. **Given** a valid task draft with objective text, ordered steps, objective-scoped attachments, step-scoped attachments, runtime intent, publish mode, branch intent, dependencies, Jira provenance, and preset metadata, **When** the user submits it for creation, **Then** the normalized task preserves those authored values and every attachment remains bound to its declared target.
2. **Given** an existing task is edited without changing attachment targets, **When** the user changes step text, objective text, ordering, runtime intent, publish mode, branch intent, dependencies, Jira provenance, or preset metadata, **Then** submission normalization preserves the explicit objective-versus-step attachment bindings instead of inferring new targets.
3. **Given** a task is rerun from an authoritative task input snapshot, **When** the user submits a full rerun or edited full retry, **Then** the normalized task preserves the original attachment targets unless the user explicitly changes valid authored task fields.
4. **Given** a user submits new task data with branch intent, **When** the submission is normalized, **Then** the task carries the authored branch through the canonical task branch field and does not emit a separate legacy target branch field.
5. **Given** a submission contains an invalid repository, runtime, publish mode, dependency, attachment policy value, missing attachment target, unknown attachment target, or ambiguous target binding, **When** the user submits it, **Then** the system fails explicitly and execution does not receive silently modified task data.

### Edge Cases

- A step reorder must keep each step-scoped attachment with its original declared step target.
- Text changes must not move objective-scoped attachments into a step or step-scoped attachments into the objective.
- Preset reapplication, preset aliases, and manual overrides must not retarget existing attachments unless the user authors a valid target change.
- A submission that mixes valid and invalid attachment targets must fail as a whole rather than dropping the invalid attachment.
- Binary input content must remain represented as structured attachment references, not embedded in task instructions.
- Jira provenance is preserved only where the task contracts support Jira provenance; unsupported provenance input must fail explicitly or be excluded with visible validation.

## Assumptions

- This story covers submission normalization for create, edit, and rerun entry points; deeper execution lifecycle behavior remains owned by the execution plane.
- The single authored branch semantics use the canonical task branch field, while publish mode determines whether the run updates a branch or opens a pull request.
- Existing server-defined attachment policy remains authoritative for allowed attachment types, sizes, and target eligibility.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 3.1, lines 57-65): Users author tasks rather than workflow internals, and the control plane must translate that task intent into execution contracts while the execution plane owns lifecycle progression. Scope: in scope. Maps to FR-001, FR-005.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` section 3.3, lines 75-83): Every input attachment must belong to either a task objective target or a step target, and that target binding must survive create, edit, rerun, prepare, prompt composition, and detail rendering. Scope: in scope. Maps to FR-002, FR-003, FR-009.
- **DESIGN-REQ-006** (Source: `docs/Tasks/TaskArchitecture.md` section 5.1, lines 157-162): The authoring control plane must validate repository, runtime, publish mode, dependencies, and attachment policy while collecting text, preset state, Jira imports, and input attachments into one coherent draft. Scope: in scope. Maps to FR-004, FR-010.
- **DESIGN-REQ-008** (Source: `docs/Tasks/TaskArchitecture.md` section 5.3, lines 171-178): Task contract normalization must preserve task and step attachments, step identity and order, runtime and publish intent, authored preset binding metadata, flattened step provenance, final submitted order, and Jira provenance where supported. Scope: in scope. Maps to FR-001, FR-005, FR-006, FR-007.
- **DESIGN-REQ-011** (Source: `docs/Tasks/TaskArchitecture.md` section 6, lines 228-294): The canonical task-shaped contract includes objective instructions, objective attachments, steps with step attachments and source metadata, authored presets, runtime intent, publish mode, task branch, applied step templates, and dependencies. Scope: in scope. Maps to FR-001, FR-005, FR-006, FR-008.
- **DESIGN-REQ-025** (Source: `docs/Tasks/TaskArchitecture.md` section 11, lines 574-612): Task system invariants require binary inputs to stay out of inline histories, explicit attachment targets, no silent attachment loss, text fields to remain text, snapshot-based durability, compile-time preset composition, preset provenance durability, server-defined policy, target-aware runtime consumption, no hidden retargeting, and compatibility without semantic drift. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-006, FR-009, FR-010, FR-011, FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept valid create, edit, and rerun submissions as task-shaped user intent rather than requiring users to author workflow internals.
- **FR-002**: Each input attachment in a submission MUST be bound explicitly to either the task objective or a declared step.
- **FR-003**: System MUST preserve objective-scoped and step-scoped attachment arrays through create, edit, rerun, prepare, prompt composition, and detail rendering.
- **FR-004**: System MUST validate repository, runtime, publish mode, dependencies, attachment policy, and attachment target bindings before execution receives the task.
- **FR-005**: Normalized task output MUST preserve objective text, step text, step identity, step order, runtime intent, publish mode, dependencies, Jira provenance where supported, and preset metadata from the authored submission.
- **FR-006**: New normalized task output MUST carry the authored branch through the canonical task branch field and MUST NOT emit a separate target branch field.
- **FR-007**: Preset-derived and manual step provenance MUST remain durable through normalization, including final submitted order and detached or overridden preset state.
- **FR-008**: Normalized task output MUST preserve authored preset binding metadata and applied template provenance when those values are present in the authored submission.
- **FR-009**: Reordering steps, changing text, applying presets, using aliases, or processing migration-era input MUST NOT silently retarget any existing attachment or alter objective-versus-step meaning.
- **FR-010**: Invalid repository, runtime, publish, dependency, attachment policy, missing target, unknown target, or ambiguous target-binding inputs MUST fail explicitly before execution receives a normalized task.
- **FR-011**: Binary input content MUST remain represented by structured attachment references and MUST NOT be embedded in task instruction text.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-627` and the original Jira preset brief.

### Key Entities

- **Task Submission**: The authored task data submitted from create, edit, or rerun flows, including objective text, steps, runtime/publish choices, branch intent, dependencies, Jira provenance, preset metadata, and attachments.
- **Attachment Target**: The declared destination for an attachment, either the task objective or a specific task step.
- **Task Step**: An ordered task instruction unit with identity, text, optional attachment refs, and optional source/provenance metadata.
- **Authored Preset Binding**: Metadata that records the selected preset, version, alias, include path, input mapping, and scope used to compose the final task.
- **Normalized Task Output**: The task-shaped contract handed to execution after validation and normalization.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid create, edit, and rerun submissions with objective-scoped and step-scoped attachments preserve all authored attachment arrays and target bindings after normalization.
- **SC-002**: 100% of valid submissions preserve step identity, step order, runtime intent, publish mode, task branch, dependencies, Jira provenance where supported, and preset metadata after normalization.
- **SC-003**: 100% of new valid submissions carry branch intent only through the canonical task branch field and emit zero separate target branch fields.
- **SC-004**: 100% of invalid repository, runtime, publish, dependency, attachment policy, or target-binding submissions fail explicitly before execution receives normalized task data.
- **SC-005**: Reordering steps, changing text, applying presets, using aliases, or processing migration-era input causes zero silent attachment retargeting events in covered validation scenarios.
- **SC-006**: Traceability review confirms `MM-627`, the original Jira preset brief, and DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, and DESIGN-REQ-025 remain preserved across MoonSpec artifacts and final verification evidence.
