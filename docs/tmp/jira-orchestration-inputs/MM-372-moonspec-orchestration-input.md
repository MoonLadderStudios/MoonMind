# MoonSpec Orchestration Input: MM-372

Jira issue: MM-372 from MM project
Summary: Inject attachment context into runtimes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-372 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

## MM-372: Inject attachment context into runtimes

### Source Reference

- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 10. Prompt and runtime injection contract
  - 15. Non-goals
- Coverage IDs:
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-020

### User Story

As a runtime adapter, I need a clear contract for text-first, planning, and multimodal image inputs so each execution receives only the attachment context appropriate to its target.

### Acceptance Criteria

- Text-first runtimes receive an `INPUT ATTACHMENTS` block before `WORKSPACE`.
- The block references relevant workspace paths, manifest entries, and generated context paths.
- Step execution receives objective-scoped context and only the current step attachment context by default.
- Non-current step context is omitted unless explicitly requested by the runtime or planner.
- Task-level planning receives objective context and a compact inventory of step-scoped attachments without flattening later-step context.
- Multimodal adapters may consume raw refs directly without changing artifact refs, target bindings, manifest source of truth, or control-plane contract.
- Provider-specific multimodal message formats remain runtime-adapter concerns, not the control-plane contract.

### Requirements

- Place attachment context before `WORKSPACE` for text-first runtimes.
- Use prepared manifest and generated context paths as injection inputs.
- Preserve source artifact refs and target bindings across direct multimodal payload construction.
- Do not embed raw image bytes in execution create payloads.
- Do not embed images into instruction markdown as data URLs.
- Do not share attachments implicitly across steps.
- Do not make live Jira sync part of this story.
- Do not add generic non-image attachment types by default.
- Do not move provider-specific multimodal message formats into the control-plane contract.

### Relevant Implementation Notes

- The canonical prepared manifest is `.moonmind/attachments_manifest.json`.
- Materialized objective inputs live under `.moonmind/inputs/objective/`.
- Materialized step inputs live under `.moonmind/inputs/steps/<stepRef>/`.
- Generated objective vision context, when present, lives under `.moonmind/vision/task/image_context.md`.
- Generated step vision context, when present, lives under `.moonmind/vision/steps/<stepRef>/image_context.md`.
- The generated context index, when present, lives at `.moonmind/vision/image_context_index.json`.
- Text-first runtime prompts must include enough manifest, workspace path, and generated context path information for agents to locate relevant input artifacts without exposing non-current step context.
- Planning prompt context may include a compact inventory of later step attachments so the planner can understand that future step inputs exist without receiving their full context.
- Direct multimodal provider payload construction is runtime-adapter-owned and must not mutate the source artifact refs, target bindings, prepared manifest, or control-plane payload shape.

### Validation

- Verify a text-first step instruction with objective and current-step attachments includes `INPUT ATTACHMENTS` before `WORKSPACE`.
- Verify the block includes manifest path, relevant workspace paths, manifest entry data, and generated context paths when present.
- Verify a step instruction excludes non-current step workspace paths and generated context paths.
- Verify task-level planning instructions include objective context plus a compact inventory of step targets without flattening later-step context.
- Verify no raw bytes or data URLs are embedded into runtime instructions.
- Verify multimodal/direct-provider handling preserves artifact refs, target bindings, and manifest source of truth.

### Non-Goals

- Embedding raw image bytes in execution create payloads.
- Embedding images into instruction markdown as data URLs.
- Implicit attachment sharing across steps.
- Live Jira sync.
- Generic non-image attachment types by default.
- Provider-specific multimodal message formats as the control-plane contract.

### Needs Clarification

- None.
