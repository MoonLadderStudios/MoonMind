# Feature Specification: Generate Target-Aware Vision Context Artifacts

**Feature Branch**: `197-generate-target-aware-vision-context`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-371 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-371-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-371 from MM project
Summary: Generate target-aware vision context artifacts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-371 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-371: Generate target-aware vision context artifacts

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 9. Vision context generation contract
- Coverage IDs:
  - DESIGN-REQ-012

User Story
As a text-first runtime user, I need deterministic image-derived context artifacts that remain traceable to source image refs and preserve objective versus step target meaning.

Acceptance Criteria
- Context generation is target-aware and may be enabled or disabled by runtime configuration.
- When disabled, manifest generation and raw file materialization still occur.
- Objective-scoped images produce `.moonmind/vision/task/image_context.md`.
- Step-scoped images produce `.moonmind/vision/steps/<stepRef>/image_context.md`.
- `.moonmind/vision/image_context_index.json` summarizes which context exists for each target.
- Generated text remains traceable to source image artifact refs and deterministic/auditable for a given source image set and model configuration.

Requirements
- Generate image-derived text context as a deterministic secondary artifact.
- Keep context generation target-aware across objective-scoped and step-scoped image attachments.
- Allow context generation to be enabled or disabled by runtime configuration.
- Continue manifest generation and raw file materialization when context generation is disabled.
- Preserve traceability from generated text back to source image artifact refs.
- Write objective-scoped image context to `.moonmind/vision/task/image_context.md`.
- Write step-scoped image context to `.moonmind/vision/steps/<stepRef>/image_context.md`.
- Write `.moonmind/vision/image_context_index.json` as an index of available context by target.
- Support OCR, captions, and safety notes only as deterministic auditable content.
- Keep derived image summaries secondary to source image refs.
- Preserve target meaning during context generation.

Relevant Implementation Notes
- Use the existing target attachment contract to distinguish objective-scoped images from step-scoped images before generating context.
- Treat generated vision context as derived data, not as a replacement for original image refs or materialized raw inputs.
- The context index should summarize which generated context exists for each objective or step target.
- Runtime configuration should decide whether to generate image-derived context; disabling generation must not block manifest generation or raw file materialization.
- Generated context must be reproducible and auditable for the same source image set and model configuration.
- Generated context may include OCR, captions, and safety notes, but all entries should preserve source image artifact ref traceability.
- Text-first runtime injection can later reference the manifest, raw materialized paths, and generated context paths without losing target meaning.

Suggested Implementation Areas
- Vision context generation service or activity for target-aware image-derived text.
- Attachment materialization or prepare layer where objective and step targets are already known.
- Runtime configuration for enabling or disabling generated vision context.
- `.moonmind/vision/task/image_context.md` and `.moonmind/vision/steps/<stepRef>/image_context.md` artifact writers.
- `.moonmind/vision/image_context_index.json` index writer.
- Tests for enabled generation, disabled generation, target separation, source traceability, and deterministic output for fixed inputs/configuration.

Validation
- Verify objective-scoped images produce only `.moonmind/vision/task/image_context.md`.
- Verify step-scoped images produce `.moonmind/vision/steps/<stepRef>/image_context.md`.
- Verify `.moonmind/vision/image_context_index.json` summarizes generated context by target.
- Verify disabling context generation still allows manifest generation and raw file materialization to complete.
- Verify generated context remains traceable to source image artifact refs.
- Verify OCR, captions, and safety notes are treated as deterministic auditable content.
- Verify derived image summaries remain secondary to source image refs and do not replace attachment references.

Non-Goals
- Replacing source image refs with generated summaries.
- Inferring target meaning from filenames, artifact links, or generated text content.
- Blocking manifest generation or raw file materialization when image context generation is disabled.
- Adding broad non-image attachment context generation beyond this story's vision context contract.
- Changing prompt injection behavior beyond producing the generated context artifacts and index needed by text-first runtimes.

Needs Clarification
- None

<!-- Moon Spec specs contain exactly one independently testable user story. Use /moonspec-breakdown for technical designs that contain multiple stories. -->

## User Story - Generate Target-Aware Vision Context Artifacts

**Summary**: As a text-first runtime user, I need deterministic image-derived context artifacts that remain traceable to source image refs and preserve objective versus step target meaning.

**Goal**: Runtime preparation produces objective-scoped and step-scoped vision context artifacts that text-first agents can consume without losing attachment target meaning or source traceability.

**Independent Test**: Prepare a task attachment context with objective-scoped images and step-scoped images, run vision context artifact generation with enabled and disabled runtime configuration, and verify the expected Markdown context paths plus JSON index are produced deterministically with source image artifact refs and target metadata preserved.

**Acceptance Scenarios**:

1. **Given** a task has objective-scoped image attachments, **When** vision context artifact generation runs with context enabled, **Then** the system produces `.moonmind/vision/task/image_context.md` for only the objective target and records that target in `.moonmind/vision/image_context_index.json`.
2. **Given** a task has step-scoped image attachments for one or more step references, **When** vision context artifact generation runs with context enabled, **Then** the system produces `.moonmind/vision/steps/<stepRef>/image_context.md` for each step target and records each step target in the index.
3. **Given** vision context generation is disabled by runtime configuration, **When** the prepare stage asks for target-aware vision context artifacts, **Then** generation does not block raw materialization or manifest work and the index records disabled context status without requiring provider output.
4. **Given** generated context contains metadata, OCR placeholders, captions, safety notes, or user hints, **When** a text-first runtime reads the artifacts, **Then** every generated entry remains traceable to the source image artifact ref and local materialized path.
5. **Given** two targets contain images with similar filenames or attachment order, **When** context artifacts are generated, **Then** target meaning is preserved from explicit objective or step bindings rather than inferred from filenames, artifact links, or generated text.

### Edge Cases

- A task has no attachments for one target but has attachments for another target.
- Vision provider credentials are unavailable while context generation is enabled.
- OCR is disabled but caption or metadata context is still rendered.
- Multiple step targets contain images with the same filename.
- A step reference contains characters that must not escape the `.moonmind/vision/steps/` tree.
- Generation is rerun for the same source image set and model configuration.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/Tasks/ImageSystem.md` section 9 is treated as source requirements for runtime behavior.
- Existing raw attachment materialization and manifest generation remain separate from generated vision context and must not be blocked by disabled vision context generation.
- Current deterministic placeholder captions/OCR are acceptable until provider-backed OCR/caption extraction is implemented.

## Source Design Requirements

- **DESIGN-REQ-012** (Source: `docs/Tasks/ImageSystem.md`, section 9; MM-371 brief): Image-derived text context MUST be generated as deterministic secondary artifacts, target-aware, runtime-configurable, traceable to source image artifact refs, written to objective and step output paths, and summarized in `.moonmind/vision/image_context_index.json`. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept objective-scoped and step-scoped image attachment groups as explicit generation targets.
- **FR-002**: System MUST write objective-scoped generated context to `.moonmind/vision/task/image_context.md` when objective-scoped image attachments are present.
- **FR-003**: System MUST write step-scoped generated context to `.moonmind/vision/steps/<stepRef>/image_context.md` for each step target that has image attachments.
- **FR-004**: System MUST write `.moonmind/vision/image_context_index.json` summarizing generated context availability, target kind, step reference when applicable, status, source attachment refs, and context path for each target.
- **FR-005**: Generated context entries MUST remain traceable to source image artifact refs and materialized local paths.
- **FR-006**: Runtime configuration MUST be able to disable vision context generation without making raw file materialization or manifest generation fail.
- **FR-007**: Disabled or provider-unavailable generation MUST produce deterministic auditable status output instead of silently omitting target context.
- **FR-008**: Generated context MAY include OCR, captions, safety notes, and user hints, but those derived summaries MUST remain secondary to source image refs.
- **FR-009**: Target meaning MUST come from explicit objective or step target bindings, not filenames, artifact links, attachment ordering across unrelated targets, or generated text content.
- **FR-010**: Repeated generation for the same source image set and model configuration MUST produce stable artifact paths and index content.

### Key Entities

- **Vision Context Target**: One explicit context generation target, either objective-scoped or step-scoped with a stable step reference.
- **Attachment Context Input**: Metadata for one materialized source image, including artifact ref, filename, content type, size, digest, local path, and optional user hint.
- **Vision Context Artifact**: Markdown derived data for one target, stored under `.moonmind/vision/task/` or `.moonmind/vision/steps/<stepRef>/`.
- **Vision Context Index**: JSON summary of target context availability, statuses, paths, and source image refs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage verifies objective-scoped image attachments produce `.moonmind/vision/task/image_context.md` and an objective index entry.
- **SC-002**: Automated coverage verifies step-scoped image attachments produce `.moonmind/vision/steps/<stepRef>/image_context.md` and step index entries.
- **SC-003**: Automated coverage verifies disabled generation records deterministic disabled statuses while leaving manifest/raw materialization responsibility unblocked.
- **SC-004**: Automated coverage verifies generated Markdown and index entries include source image artifact refs and local materialized paths.
- **SC-005**: Automated coverage verifies target separation is preserved when objective and step attachments share filenames.
