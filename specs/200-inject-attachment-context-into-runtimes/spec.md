# Feature Specification: Inject Attachment Context Into Runtimes

**Feature Branch**: `200-inject-attachment-context-into-runtimes`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-372 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Input Classification**: Single-story feature request.  
**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-372 from MM project
Summary: Inject attachment context into runtimes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-372 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-372: Inject attachment context into runtimes

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 10. Prompt and runtime injection contract
  - 15. Non-goals
- Coverage IDs:
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-020

User Story
As a runtime adapter, I need a clear contract for text-first, planning, and multimodal image inputs so each execution receives only the attachment context appropriate to its target.

Acceptance Criteria
- Text-first runtimes receive an `INPUT ATTACHMENTS` block before `WORKSPACE`.
- The block references relevant workspace paths, manifest entries, and generated context paths.
- Step execution receives objective-scoped context and only the current step attachment context by default.
- Non-current step context is omitted unless explicitly requested by the runtime or planner.
- Task-level planning receives objective context and a compact inventory of step-scoped attachments without flattening later-step context.
- Multimodal adapters may consume raw refs directly without changing artifact refs, target bindings, manifest source of truth, or control-plane contract.
- Provider-specific multimodal message formats remain runtime-adapter concerns, not the control-plane contract.

Requirements
- Place attachment context before `WORKSPACE` for text-first runtimes.
- Use prepared manifest and generated context paths as injection inputs.
- Preserve source artifact refs and target bindings across direct multimodal payload construction.
- Do not embed raw image bytes in execution create payloads.
- Do not embed images into instruction markdown as data URLs.
- Do not share attachments implicitly across steps.
- Do not make live Jira sync part of this story.
- Do not add generic non-image attachment types by default.
- Do not move provider-specific multimodal message formats into the control-plane contract.

Relevant Implementation Notes
- The canonical prepared manifest is `.moonmind/attachments_manifest.json`.
- Materialized objective inputs live under `.moonmind/inputs/objective/`.
- Materialized step inputs live under `.moonmind/inputs/steps/<stepRef>/`.
- Generated objective vision context, when present, lives under `.moonmind/vision/task/image_context.md`.
- Generated step vision context, when present, lives under `.moonmind/vision/steps/<stepRef>/image_context.md`.
- The generated context index, when present, lives at `.moonmind/vision/image_context_index.json`.
- Text-first runtime prompts must include enough manifest, workspace path, and generated context path information for agents to locate relevant input artifacts without exposing non-current step context.
- Planning prompt context may include a compact inventory of later step attachments so the planner can understand that future step inputs exist without receiving their full context.
- Direct multimodal provider payload construction is runtime-adapter-owned and must not mutate the source artifact refs, target bindings, prepared manifest, or control-plane payload shape.

Validation
- Verify a text-first step instruction with objective and current-step attachments includes `INPUT ATTACHMENTS` before `WORKSPACE`.
- Verify the block includes manifest path, relevant workspace paths, manifest entry data, and generated context paths when present.
- Verify a step instruction excludes non-current step workspace paths and generated context paths.
- Verify task-level planning instructions include objective context plus a compact inventory of step targets without flattening later-step context.
- Verify no raw bytes or data URLs are embedded into runtime instructions.
- Verify multimodal/direct-provider handling preserves artifact refs, target bindings, and manifest source of truth.

Non-Goals
- Embedding raw image bytes in execution create payloads.
- Embedding images into instruction markdown as data URLs.
- Implicit attachment sharing across steps.
- Live Jira sync.
- Generic non-image attachment types by default.
- Provider-specific multimodal message formats as the control-plane contract.

Needs Clarification
- None

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Inject Target-Scoped Attachment Context

**Summary**: As a runtime adapter, I need text-first, planning, and multimodal runtime paths to receive only the attachment context appropriate to the current execution target.

**Goal**: Runtime instructions and adapter-visible attachment metadata expose objective and current-step attachment context without leaking non-current step context, while preserving artifact refs, target bindings, and the prepared manifest as the source of truth.

**Independent Test**: Prepare a task workspace with objective and multiple step input attachments plus generated vision context paths, compose a runtime instruction for one step, and verify `INPUT ATTACHMENTS` appears before `WORKSPACE`, includes objective and current-step manifest/context references, excludes non-current step context, and does not embed raw bytes or data URLs.

**Acceptance Scenarios**:

1. **Given** a text-first runtime step has objective-scoped attachments and current-step attachments, **When** the runtime instruction is composed, **Then** an `INPUT ATTACHMENTS` block appears before `WORKSPACE` and references the manifest, relevant workspace paths, relevant manifest entries, and generated context paths when present.
2. **Given** the task has attachments for a later or different step, **When** the current step instruction is composed, **Then** the block omits non-current step workspace paths, generated context paths, and full manifest entry detail unless cross-step access is explicitly requested.
3. **Given** task-level planning receives an attachment-aware task, **When** planning instructions are composed, **Then** objective context is included and step-scoped attachments are represented only as a compact inventory of target metadata.
4. **Given** a runtime path can construct direct multimodal provider payloads, **When** it reads attachment metadata, **Then** source artifact refs, target bindings, prepared manifest paths, and control-plane payload shape remain unchanged.
5. **Given** attachment context includes potentially hostile extracted image text, **When** the block is injected, **Then** it is clearly marked as untrusted reference data and does not embed raw image bytes or data URLs.

### Edge Cases

- The manifest file is absent because no attachments were declared.
- A manifest entry has no generated context path.
- Objective and step attachments use the same filename.
- Multiple steps have attachments but only one step is currently executing.
- A step has a generated context index entry but no full context file.
- A manifest entry contains unexpected optional fields; injection preserves compact known metadata without failing open to raw content.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/Tasks/ImageSystem.md` sections 10 and 15 are runtime source requirements.
- Prepare-time materialization and target-aware vision context generation are handled by adjacent stories; this story consumes their manifest and context outputs.
- Text-first runtimes use composed instruction text as the injection surface.
- Direct multimodal provider payload construction can be represented by adapter-visible metadata preservation and does not require provider-specific message schemas in this story.

## Source Design Requirements

- **DESIGN-REQ-013** (Source: `docs/Tasks/ImageSystem.md`, section 10.1; MM-372 brief): Text-first runtimes MUST receive an `INPUT ATTACHMENTS` block before `WORKSPACE` that references relevant workspace paths, manifest entries, and generated context paths. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-010.
- **DESIGN-REQ-014** (Source: `docs/Tasks/ImageSystem.md`, sections 10.1-10.2; MM-372 brief): Step execution MUST receive objective-scoped context and only the current step's attachment context by default, while task-level planning receives objective context plus only a compact inventory of step-scoped attachments. Scope: in scope. Maps to FR-005, FR-006, FR-007, FR-008.
- **DESIGN-REQ-020** (Source: `docs/Tasks/ImageSystem.md`, section 15; MM-372 brief): Runtime injection MUST NOT require raw image bytes in create payloads, image data URLs in instructions, implicit cross-step sharing, live Jira sync, generic non-image support by default, or provider-specific multimodal message formats as the control-plane contract. Scope: in scope as guardrails. Maps to FR-009, FR-010, FR-011, FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Text-first runtime instructions MUST include an `INPUT ATTACHMENTS` block before the `WORKSPACE` section whenever relevant prepared attachments exist for the objective or current step.
- **FR-002**: The injected block MUST identify the prepared manifest path when `.moonmind/attachments_manifest.json` exists.
- **FR-003**: The injected block MUST include relevant manifest entry metadata for objective-scoped attachments and current-step attachments, including target kind, artifact id, filename, content type, byte size, workspace path, step ref when applicable, and generated context path when present.
- **FR-004**: The injected block MUST include relevant generated context paths when target-aware vision context artifacts or index entries exist.
- **FR-005**: Step execution MUST include objective-scoped attachment context by default.
- **FR-006**: Step execution MUST include only the current step's step-scoped attachment context by default.
- **FR-007**: Step execution MUST omit non-current step workspace paths, generated context paths, and full manifest entry details unless cross-step access is explicitly requested by a runtime or planner.
- **FR-008**: Task-level planning instructions MUST include objective-scoped attachment context and represent step-scoped attachments only as a compact inventory of target, step ref, artifact ids, filenames, and generated context availability.
- **FR-009**: Direct multimodal adapter metadata MUST preserve source artifact refs, target bindings, prepared manifest source of truth, and control-plane payload shape without introducing provider-specific message schemas into the control-plane contract.
- **FR-010**: Runtime instructions MUST treat generated image-derived text as untrusted reference data and MUST NOT present it as executable instructions.
- **FR-011**: Runtime instructions MUST NOT embed raw image bytes, base64 data URLs, or image markdown data URLs.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-372` and the original Jira preset brief for traceability.

### Key Entities

- **Attachment Injection Block**: The text-first runtime prompt section that describes relevant prepared attachment manifest entries, workspace paths, and generated context paths.
- **Current Step Attachment Context**: The subset of manifest and context entries belonging to the executing step.
- **Planning Attachment Inventory**: A compact planning-only summary of objective and step attachment targets that does not flatten later-step context into the active step.
- **Multimodal Attachment Metadata**: Adapter-visible metadata that preserves artifact refs and target bindings for direct provider payload construction without changing control-plane contracts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage verifies `INPUT ATTACHMENTS` appears before `WORKSPACE` for attachment-aware text-first step instructions.
- **SC-002**: Automated coverage verifies objective and current-step workspace paths, manifest metadata, and generated context paths are included in the current step block.
- **SC-003**: Automated coverage verifies non-current step paths and full context are omitted from current step instructions.
- **SC-004**: Automated coverage verifies planning receives a compact step attachment inventory without full later-step context.
- **SC-005**: Automated coverage verifies runtime instructions do not contain raw image bytes, base64 data URLs, or image markdown data URLs.
- **SC-006**: Final verification confirms `MM-372` and the original Jira preset brief are preserved in active MoonSpec artifacts and delivery metadata.
