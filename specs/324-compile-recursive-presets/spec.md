# Feature Specification: Compile Recursive Task Presets

**Feature Branch**: `324-compile-recursive-presets`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-630 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-630 MoonSpec Orchestration Input

## Source

- Jira issue: MM-630
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Compile recursive task presets before execution
- Labels: `moonmind-workflow-mm-86f66178-893d-469b-ba39-7bf1a3a19bb6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-630 from MM project
Summary: Compile recursive task presets before execution
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-630 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-630: Compile recursive task presets before execution

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 5.4 Preset compilation
- 5.3 Task contract normalization
- 6 Canonical task-shaped contract
- 8 Execution-plane responsibilities
- 11 Invariants

Coverage IDs:
- DESIGN-REQ-009
- DESIGN-REQ-010

As a task author using presets, I want recursive preset includes resolved into final ordered steps before execution while MoonMind keeps provenance for audit and safe reconstruction.

Acceptance Criteria:
- Recursive preset include trees are validated before execution contract finalization.
- Manual and preset-derived steps are flattened into deterministic final submitted order.
- `authoredPresets` and `steps[].source` preserve IDs/slugs, versions, aliases, include paths, mappings, original step IDs, and detachment state.
- Workers receive resolved steps and do not expand presets or read the live preset catalog.
- Already submitted work can be reconstructed after live preset catalog changes.

Requirements:
- Recursive presets are authoring objects resolved before execution; workers consume flattened steps without live preset catalog lookup.
- Snapshots and payloads preserve authored preset bindings, include-tree summaries, source provenance, detachment state, and final order.
"""

**Implementation Intent**: Runtime. The Jira preset brief selects product behavior for task submission and execution boundaries; the referenced architecture document is treated as runtime source requirements.

## User Story - Recursive Preset Compilation

**Summary**: Task authors can submit tasks that include nested presets and receive one final, ordered, executable step list with durable provenance.

**Goal**: Enable authors to compose work from presets without making execution depend on live preset definitions after submission.

**Independent Test**: Submit a task draft containing manual steps plus a recursive preset include tree, then verify before execution begins that the task has a deterministic flattened step order, complete preset provenance, and a worker-facing payload that remains executable after the live preset catalog changes.

**Acceptance Scenarios**:

1. **Given** a task draft with a preset that includes another preset, **When** the task is submitted, **Then** the system validates the full include tree before finalizing the execution contract.
2. **Given** manual steps interleaved with preset-derived steps, **When** compilation succeeds, **Then** the submitted task contains a deterministic final step order that preserves the author's intended ordering.
3. **Given** preset-derived or detached steps, **When** the submitted task snapshot is recorded, **Then** the snapshot preserves preset identifiers or slugs, versions, aliases, include paths, input mappings, original step identifiers, detachment state, and per-step source provenance where those values exist.
4. **Given** a compiled task, **When** a worker starts execution, **Then** the worker receives resolved executable steps and does not need to expand presets or consult the live preset catalog.
5. **Given** a task was already submitted and then preset catalog definitions changed, **When** the task is viewed, rerun, audited, or reconstructed, **Then** the system uses the submitted snapshot and provenance rather than changed live preset definitions.

**Edge Cases**:

- A recursive include tree contains a cycle or unsupported include shape.
- A nested preset references a missing, disabled, or unauthorized preset.
- A preset-derived step has been detached or edited before submission.
- Two included presets provide conflicting aliases or mappings.
- A task contains no presets and should continue to submit as a manual flat task.
- A live preset is modified or deleted after a task has already been submitted.

## Assumptions

- A task draft may already contain manual steps, preset selections, and nested preset include metadata before submission.
- Existing task edit, rerun, and audit surfaces can read from an authoritative submitted task snapshot when the snapshot preserves enough provenance.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tasks/TaskArchitecture.md` lines 171-178. Task contract normalization must preserve authored preset binding metadata, flattened provenance, manual and preset-derived order, fully resolved execution payloads, and allowed Jira provenance. Scope: in scope. Mapped to FR-001, FR-003, FR-004, FR-006.
- **DESIGN-REQ-002**: Source `docs/Tasks/TaskArchitecture.md` lines 180-191. Preset compilation must be a control-plane phase before execution finalization, resolve recursive composition, validate include trees, flatten steps, preserve provenance, and produce payloads that do not require live preset lookup. Scope: in scope. Mapped to FR-001 through FR-005.
- **DESIGN-REQ-003**: Source `docs/Tasks/TaskArchitecture.md` lines 240-257 and 275-293. The canonical task contract defines source and authored preset fields capable of carrying preset identifiers, slugs, versions, aliases, include paths, mappings, and original step identifiers. Scope: in scope. Mapped to FR-003, FR-004.
- **DESIGN-REQ-004**: Source `docs/Tasks/TaskArchitecture.md` lines 324-337. Authored presets and step source are task contract fields for reconstruction, audit, diagnostics, and safe rerun semantics, and worker-facing payloads must already be resolved. Scope: in scope. Mapped to FR-004, FR-006.
- **DESIGN-REQ-005**: Source `docs/Tasks/TaskArchitecture.md` lines 452-461. The execution plane consumes normalized resolved steps and must not expand presets or depend on live preset catalog correctness for already submitted work. Scope: in scope. Mapped to FR-005, FR-006.
- **DESIGN-REQ-006**: Source `docs/Tasks/TaskArchitecture.md` lines 593-597. Compile-time preset composition and preset provenance durability are task system invariants. Scope: in scope. Mapped to FR-001 through FR-006.
- **DESIGN-REQ-007**: Source `docs/Tasks/TaskArchitecture.md` lines 578-592 and 599-600. Attachment payload, attachment target, text, snapshot, and server policy invariants are preserved by this story but not expanded. Scope: out of scope; MM-630 focuses on preset compilation and provenance rather than attachment policy behavior. Mapped to FR-007.
- **DESIGN-REQ-008**: Source `docs/Tasks/TaskArchitecture.md` lines 463-490. Workflow, prepare, and step execution responsibilities around attachments and target-aware context remain outside the preset compilation slice. Scope: out of scope; workers still consume resolved task context, but attachment preparation behavior is not changed by this story. Mapped to FR-007.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST compile recursive preset include trees before execution contract finalization.
- **FR-002**: The system MUST reject or block finalization of preset include trees that are cyclic, missing required preset references, unauthorized, disabled, or otherwise invalid.
- **FR-003**: The system MUST flatten manual and preset-derived steps into one deterministic submitted order before workers can execute the task.
- **FR-004**: The submitted task snapshot and payload MUST preserve reliable authored preset binding and step source provenance, including identifiers or slugs, versions, aliases, include paths, input mappings, original step identifiers, and detachment state when those values exist.
- **FR-005**: Worker-facing execution payloads MUST contain resolved executable steps and MUST NOT require workers to expand presets or read the live preset catalog.
- **FR-006**: Already submitted work MUST be reconstructable from its submitted snapshot and provenance after live preset catalog definitions change.
- **FR-007**: The feature MUST preserve existing task behavior for attachment handling, runtime selection, publish intent, Jira provenance, edit, rerun, and resume semantics except where those behaviors need the compiled preset provenance selected by MM-630.
- **FR-008**: The system MUST preserve Jira issue key `MM-630` and the canonical Jira preset brief in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Task Draft**: The authoring-state task before submission, containing manual steps, preset selections, optional nested includes, runtime selections, publish intent, and Jira provenance.
- **Preset Include Tree**: The nested structure of selected presets and included presets that must be validated and resolved before execution.
- **Compiled Task Snapshot**: The authoritative submitted representation containing final ordered steps plus enough provenance to audit, reconstruct, edit, rerun, or resume safely.
- **Step Source Provenance**: Per-step metadata describing whether a step is manual, preset-derived, included, or detached, including reliable origin identifiers and include path data when available.
- **Worker-Facing Payload**: The resolved task contract consumed by execution workers after preset compilation.

## Success Criteria

- **SC-001**: 100% of submitted tasks with recursive preset includes are validated before execution contract finalization.
- **SC-002**: Re-submitting the same valid draft inputs produces the same final step order in 100% of repeated submissions.
- **SC-003**: 100% of compiled preset-derived or detached steps with reliable origin data preserve source provenance in the submitted snapshot.
- **SC-004**: Workers can execute a compiled preset-derived task when live preset lookup is unavailable after submission.
- **SC-005**: A submitted task can be reconstructed after live preset catalog changes without changing its original final step order or provenance.
- **SC-006**: Manual-only task submission behavior remains unchanged for tasks with zero presets.
