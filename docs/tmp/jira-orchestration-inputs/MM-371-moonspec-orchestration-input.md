# MM-371 MoonSpec Orchestration Input

## Source

- Jira issue: MM-371
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Generate target-aware vision context artifacts
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

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
