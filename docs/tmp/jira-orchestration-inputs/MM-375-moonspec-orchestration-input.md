# MM-375 MoonSpec Orchestration Input

## Source

- Jira issue: MM-375
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose image diagnostics and failure evidence
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-375 from MM project
Summary: Expose image diagnostics and failure evidence
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-375 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-375: Expose image diagnostics and failure evidence

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 14. Observability and diagnostics contract
- Coverage IDs:
  - DESIGN-REQ-019

User Story
As an operator debugging image-input failures, I need target-aware events, manifest/context path discovery, and step-specific failure evidence without scraping raw workflow history heuristics.

Acceptance Criteria
- Events are emitted for attachment upload started/completed and attachment validation failed.
- Events are emitted for prepare download started/completed/failed.
- Events are emitted for image context generation started/completed/failed.
- Task diagnostics expose the attachment manifest path and generated context paths.
- Task detail and debugging surfaces expose target-aware attachment metadata.
- Step-level failures identify the affected step target.

Requirements
- Make diagnostics sufficient to debug image failures without relying on raw workflow history heuristics.
- Connect diagnostic evidence to the same target bindings used by task execution.
- Expose target-aware diagnostic events for attachment upload, attachment validation, prepare download, and image context generation lifecycle outcomes.
- Make attachment manifest path discovery and generated context path discovery available from task diagnostics.
- Surface target-aware attachment metadata on task detail and debugging surfaces.
- Ensure step-level image-input failures identify the affected step target.

Relevant Implementation Notes
- The image input system must expose enough evidence to debug failures without reading raw workflow history heuristics.
- Recommended event classes include attachment upload started, attachment upload completed, attachment validation failed, prepare download started, prepare download completed, prepare download failed, image context generation started, image context generation completed, and image context generation failed.
- Attachment manifest path and generated context paths should be discoverable from task diagnostics.
- Task detail and debugging surfaces should expose target-aware attachment metadata.
- Step-level failures must identify the affected step target.
- Diagnostic evidence should stay connected to the authoritative task input snapshot target bindings rather than inferred filenames, artifact ordering, or workflow-history heuristics.

Suggested Implementation Areas
- Image attachment upload and validation event publishing.
- Prepare-download and image-context-generation event publishing.
- Task diagnostics payloads or artifacts that expose attachment manifest and generated context paths.
- Task detail and debugging UI/API surfaces that present target-aware attachment metadata.
- Step failure reporting for image-input preparation and validation paths.
- Tests covering emitted event classes, diagnostics path discovery, target-aware metadata exposure, and step-target failure evidence.

Validation
- Verify attachment upload started/completed and attachment validation failed events are emitted with target-aware metadata.
- Verify prepare download started/completed/failed events are emitted with target-aware metadata.
- Verify image context generation started/completed/failed events are emitted with target-aware metadata.
- Verify task diagnostics expose the attachment manifest path and generated context paths.
- Verify task detail and debugging surfaces expose target-aware attachment metadata.
- Verify step-level failures identify the affected step target.
- Verify diagnostics do not rely on raw workflow history heuristics, filenames, artifact ordering, or other implicit binding recovery.

Non-Goals
- Scraping raw workflow history heuristics as the primary diagnostic interface.
- Inferring target bindings from filenames, artifact links, attachment ordering, or generated preview metadata.
- Embedding raw image bytes in execution create payloads.
- Embedding images into instruction markdown as data URLs.
- Adding live Jira sync.
- Adding generic non-image attachment support beyond the image diagnostics contract.

Needs Clarification
- None
