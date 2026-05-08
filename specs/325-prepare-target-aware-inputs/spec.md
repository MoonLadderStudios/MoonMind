# Feature Specification: Prepare Target-Aware Inputs

**Feature Branch**: `325-prepare-target-aware-inputs`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
For a single-story Jira preset brief, run moonspec-specify unless an active spec.md already passes the specify gate.
For a broad technical or declarative design, run moonspec-breakdown first, then select the recommended first generated spec unless the issue brief explicitly requires processing all specs.
Preserve Jira issue MM-631 and the original preset brief in spec.md so final verification can compare against them.

Canonical Jira preset brief:

# MM-631 MoonSpec Orchestration Input

## Source

- Jira issue: MM-631
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Prepare target-aware inputs for step execution
- Priority: Medium
- Labels: `moonmind-workflow-mm-86f66178-893d-469b-ba39-7bf1a3a19bb6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-631 from MM project
Summary: Prepare target-aware inputs for step execution
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-631 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-631: Prepare target-aware inputs for step execution

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.5 Separation of text from structured inputs
- 8.1 Workflow responsibilities
- 8.2 Prepare responsibilities
- 8.3 Step execution responsibilities
- 8.4 Child workflow responsibilities
- 10 Runtime and prompt boundary
- 12 Workload-specific behavior

Coverage IDs:
- DESIGN-REQ-005
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-019
- DESIGN-REQ-021

As a runtime step, I want prepared context to include relevant objective inputs and only my step-scoped inputs so attachments are materialized, contextualized, and passed across workflow boundaries without leakage.

Acceptance Criteria
- Prepare downloads objective-scoped and step-scoped attachments and writes a canonical manifest.
- Raw files are materialized into stable workspace locations and image context artifacts are produced as secondary artifacts.
- Instruction fields remain textual.
- Each step receives relevant objective context plus only its own step-scoped context by default.
- AgentRun children receive only the prepared context relevant to the represented step.
- Runtime adapters may consume generated context or raw refs but must not invent new targeting rules.

Requirements
- Instruction text remains text, images remain structured inputs, and derived image context is a secondary artifact.
- MoonMind.Run owns progression, prepare orchestration, context generation, target-aware context delivery, ledgers, checkpoints, and full/resumed starts.
- Prepare downloads attachments, writes manifests, materializes stable workspace files, emits target-aware context artifacts, and fails on invalid preparation.
- Step runtimes and AgentRun children receive only relevant objective/current-step context and cannot redefine target binding.
- Adapters may realize normalized intent and artifact refs as text-first or multimodal payloads without inventing new target rules.

Relevant Jira links from trusted issue response:
- MM-630: Compile recursive task presets before execution; relationship: Blocks outward issue; status: Done; issue type: Story.
- MM-632: Expose distinct full retry recovery actions; relationship: Blocks inward issue; status: Backlog; issue type: Story.
"""

## User Story - Prepare Step-Scoped Inputs

**Summary**: As a runtime step, I want prepared context to include relevant objective inputs and only my own step-scoped inputs so that attachments are materialized, contextualized, and passed across workflow boundaries without leaking unrelated inputs.

**Goal**: Each task step receives a complete, target-aware prepared context that includes applicable objective-scoped attachments and only the current step's scoped attachments, while preserving text instructions as text and structured inputs as structured inputs.

**Independent Test**: Can be fully tested by submitting a task with objective-scoped attachments and multiple step-scoped attachments, then verifying that preparation produces a canonical manifest and that each step, including a delegated child step, receives only the prepared context targeted to it.

**Acceptance Scenarios**:

1. **Given** a submitted task has objective-scoped attachments and step-scoped attachments for multiple steps, **When** preparation runs, **Then** the prepared manifest records the objective target and each step target separately.
2. **Given** preparation processes files for objective and step targets, **When** raw files and derived image context are prepared, **Then** raw files have stable materialized locations and derived context is recorded as secondary context rather than merged into instruction text.
3. **Given** a step starts after preparation succeeds, **When** its runtime context is assembled, **Then** it includes relevant objective context plus only that step's scoped prepared context by default.
4. **Given** a step delegates execution to a child agent run, **When** the child receives its prepared inputs, **Then** the child receives only the prepared context relevant to the represented step and cannot broaden the target binding.
5. **Given** an adapter consumes prepared context, **When** it realizes text-first or multimodal payloads, **Then** it follows the prepared target bindings and does not create new targeting rules.
6. **Given** preparation cannot fully download, materialize, or contextualize a targeted attachment, **When** the failure is detected, **Then** the task fails explicitly with an operator-readable reason instead of running with incomplete or leaked context.

### Edge Cases

- A task has objective-scoped attachments but no step-scoped attachments; each step still receives the relevant objective context without synthetic step targets.
- A step has no scoped attachments; it receives objective context when relevant and no unrelated step context.
- Multiple steps reference attachments with similar filenames or source metadata; target identity remains based on authored binding, not names or storage paths.
- A child run is started for one step while other steps have attachments; the child receives only the represented step's prepared context plus relevant objective context.
- A runtime adapter supports raw structured inputs while another supports text-first context; both observe the same target binding semantics.

## Assumptions

- The feature applies to the canonical attachment-aware task workflow used for runtime implementation work.
- Objective-scoped context is relevant to each step unless future task policy narrows objective visibility.
- Existing artifact authorization and preview rules continue to govern access to prepared raw files and derived context.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tasks/TaskArchitecture.md`, section 3.5. Instruction text remains text, images remain structured inputs, and derived image context is a secondary artifact. Scope: in scope. Maps to FR-001, FR-002, and FR-003. Original Jira coverage ID: DESIGN-REQ-005.
- **DESIGN-REQ-002**: Source `docs/Tasks/TaskArchitecture.md`, section 8.1. The runtime workflow owns preparation orchestration, image context generation, target-aware context delivery, and preservation of bounded step evidence needed for resume eligibility. Scope: in scope. Maps to FR-004, FR-005, and FR-010. Original Jira coverage ID: DESIGN-REQ-017.
- **DESIGN-REQ-003**: Source `docs/Tasks/TaskArchitecture.md`, section 8.2. Preparation downloads objective-scoped and step-scoped attachments, writes a canonical manifest, materializes stable raw files, produces target-aware image context artifacts, and fails explicitly when preparation is incomplete or invalid. Scope: in scope. Maps to FR-002, FR-003, FR-006, and FR-007. Original Jira coverage ID: DESIGN-REQ-018.
- **DESIGN-REQ-004**: Source `docs/Tasks/TaskArchitecture.md`, section 8.3. Step execution consumes task-level objective context when relevant and only the current step's step-scoped context by default. Scope: in scope. Maps to FR-008. Original Jira coverage ID: DESIGN-REQ-019.
- **DESIGN-REQ-005**: Source `docs/Tasks/TaskArchitecture.md`, section 8.4. Child agent runs receive only the prepared context relevant to the represented step and do not redefine target binding semantics. Scope: in scope. Maps to FR-009. Original Jira coverage ID: DESIGN-REQ-021.
- **DESIGN-REQ-006**: Source `docs/Tasks/TaskArchitecture.md`, section 10. Runtime adapters may consume generated context or raw refs according to capability but must not change control-plane targeting rules. Scope: in scope. Maps to FR-011.
- **DESIGN-REQ-007**: Source `docs/Tasks/TaskArchitecture.md`, section 12. The canonical task workflow is responsible for attachment-aware task authoring, step ledger state, and prepared context passed to delegated child runs. Scope: in scope. Maps to FR-004, FR-009, and FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST preserve authored instruction fields as textual content and MUST NOT inline binary or derived attachment content into those instruction fields.
- **FR-002**: System MUST treat uploaded images and other binary task inputs as structured input references throughout preparation and runtime delivery.
- **FR-003**: System MUST represent derived image context as secondary prepared context that remains linked to the original target binding.
- **FR-004**: System MUST orchestrate preparation before step execution for tasks with objective-scoped or step-scoped attachments.
- **FR-005**: System MUST carry target-aware prepared context through runtime progression without losing the distinction between objective targets and step targets.
- **FR-006**: System MUST download or otherwise make available every valid objective-scoped and step-scoped attachment needed for execution before the affected step runs.
- **FR-007**: System MUST produce a canonical prepared-input manifest that records each prepared item, its target kind, and its step target when applicable.
- **FR-008**: System MUST provide each step with relevant objective context plus only that step's own step-scoped prepared context by default.
- **FR-009**: System MUST provide delegated child agent runs only the prepared context relevant to the represented step and MUST NOT allow child runs to broaden attachment targeting.
- **FR-010**: System MUST retain bounded prepared-context references and step evidence needed to support later recovery decisions without embedding large or binary content in run history.
- **FR-011**: Runtime adapters MUST consume prepared context according to their capabilities while preserving the control-plane target bindings exactly.
- **FR-012**: System MUST fail explicitly with an operator-readable reason when required preparation is incomplete, invalid, unauthorized, or otherwise unavailable.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-631` and the original Jira preset brief for traceability.

### Key Entities *(include if feature involves data)*

- **Input Attachment**: A structured reference to a user-provided file or imported input, including its authored target binding.
- **Prepared Input Manifest**: The canonical preparation record that lists prepared items, target bindings, materialized raw-file references, derived context references, and preparation status.
- **Prepared Context Item**: A raw input reference or derived context artifact made available to a runtime according to target binding.
- **Step Runtime Context**: The bounded set of objective and current-step prepared context delivered to a step or delegated child run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a task with one objective attachment and at least two differently targeted step attachments, 100% of steps receive only the objective context plus their own step-scoped context during verification.
- **SC-002**: Preparation produces one canonical manifest for every attachment-aware task execution before the first affected step begins.
- **SC-003**: Verification of delegated child runs confirms 100% of child runs receive no prepared context for unrelated steps.
- **SC-004**: Invalid, unavailable, or unauthorized attachment preparation paths produce an explicit terminal or retryable preparation failure before any affected step executes.
- **SC-005**: Traceability review confirms `MM-631`, the original Jira preset brief, and source design mappings remain present in the specification and final verification evidence.
