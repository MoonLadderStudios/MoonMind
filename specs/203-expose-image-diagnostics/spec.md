# Feature Specification: Expose Image Diagnostics and Failure Evidence

**Feature Branch**: `203-expose-image-diagnostics`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-375 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

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

<!-- Moon Spec specs contain exactly one independently testable user story. Use /moonspec-breakdown for technical designs that contain multiple stories. -->

## User Story - Image Input Diagnostics

**Summary**: As an operator debugging image-input failures, I need target-aware events, manifest/context path discovery, and step-specific failure evidence without scraping raw workflow history heuristics.

**Goal**: Operators can diagnose image-input upload, prepare download, and context-generation problems from MoonMind-owned diagnostics that preserve the affected objective or step target and point to generated manifest/context evidence.

**Independent Test**: Run image input upload, validation, prepare-download, and context-generation flows for objective-scoped and step-scoped attachments, force representative failures, and verify emitted diagnostics identify the lifecycle event, attachment target, evidence paths, and affected step target without requiring raw workflow history inspection.

**Acceptance Scenarios**:

1. **Given** an objective-scoped or step-scoped image attachment is uploaded successfully, **When** upload begins and completes, **Then** target-aware diagnostics record started and completed events with the attachment target metadata.
2. **Given** image attachment validation fails, **When** the failure is reported, **Then** diagnostics record an attachment validation failed event that identifies the affected target and attachment metadata.
3. **Given** workflow prepare downloads declared image attachments, **When** each download starts, completes, or fails, **Then** diagnostics record the corresponding prepare download event with target-aware metadata and any failure reason.
4. **Given** image context generation starts, completes, fails, or is disabled, **When** diagnostics are inspected, **Then** generated context paths and statuses are discoverable by objective or step target.
5. **Given** a step-scoped image failure occurs during prepare or context generation, **When** an operator views task diagnostics or debugging surfaces, **Then** the affected step target is identified without relying on filenames, attachment order, or raw workflow history heuristics.

### Edge Cases

- One objective-scoped attachment succeeds while one step-scoped attachment fails.
- Context generation is disabled by runtime configuration after raw attachment materialization succeeds.
- A generated context path is absent because generation failed.
- Multiple step targets contain images with the same filename.
- An attachment failure occurs before a workspace path is available.
- Diagnostics are requested for a task with no image inputs.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/Tasks/ImageSystem.md` section 14 is treated as source requirements for runtime behavior.
- Existing upload, materialization, and context-generation flows already preserve authoritative target bindings.
- Diagnostics may expose compact metadata and artifact/context paths, but they must not expose raw image bytes or provider credentials.

## Source Design Requirements

- **DESIGN-REQ-019** (Source: `docs/Tasks/ImageSystem.md`, section 14; MM-375 brief): The image input system MUST expose enough evidence to debug failures without raw workflow-history heuristics, including target-aware lifecycle events for attachment upload, validation, prepare download, and image context generation; task diagnostics MUST expose attachment manifest and generated context paths; task detail and debugging surfaces MUST expose target-aware attachment metadata; and step-level failures MUST identify the affected step target. Scope: in scope. Maps to FR-001 through FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST emit target-aware diagnostics when attachment upload starts.
- **FR-002**: The system MUST emit target-aware diagnostics when attachment upload completes.
- **FR-003**: The system MUST emit target-aware diagnostics when attachment validation fails.
- **FR-004**: The system MUST emit target-aware diagnostics when prepare download starts, completes, or fails for an image attachment.
- **FR-005**: The system MUST emit target-aware diagnostics when image context generation starts, completes, fails, or is disabled for a target.
- **FR-006**: Task diagnostics MUST expose the attachment manifest path when a manifest is produced.
- **FR-007**: Task diagnostics MUST expose generated context paths and statuses when image context generation produces, skips, disables, or fails target context.
- **FR-008**: Task detail and debugging surfaces MUST expose target-aware attachment metadata sufficient to identify objective-scoped and step-scoped image inputs.
- **FR-009**: Step-level image input failures MUST identify the affected step target when the failure is tied to a step-scoped attachment or step context.
- **FR-010**: Diagnostic target bindings MUST come from authoritative task input snapshot or manifest target metadata, not filenames, artifact links, attachment ordering, UI heuristics, or raw workflow history.
- **FR-011**: Diagnostics MUST NOT expose raw image bytes, provider credentials, auth headers, or storage-provider secret material.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-375` and the original Jira preset brief for traceability.

### Key Entities

- **Image Diagnostic Event**: A compact lifecycle record describing an image-input upload, validation, prepare-download, or context-generation event with target-aware metadata and optional evidence paths.
- **Diagnostic Evidence Path**: A MoonMind-owned path to the attachment manifest or generated context artifact that helps operators debug image-input behavior.
- **Attachment Target Metadata**: The objective-scoped or step-scoped binding, including step reference when applicable, used to identify which task target owns an image input.
- **Step Failure Evidence**: Failure metadata that ties an image-input problem to the affected step target when the problem is step-scoped.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage verifies attachment upload started and completed diagnostics include objective or step target metadata.
- **SC-002**: Automated coverage verifies attachment validation failed diagnostics include target metadata and failure details.
- **SC-003**: Automated coverage verifies prepare download started, completed, and failed diagnostics include target metadata and failure details.
- **SC-004**: Automated coverage verifies image context generation started, completed, failed, and disabled diagnostics expose target-specific context status and paths when available.
- **SC-005**: Automated coverage verifies task diagnostics expose attachment manifest and generated context paths when those evidence artifacts exist.
- **SC-006**: Automated coverage verifies step-level image failures identify the affected step target.
- **SC-007**: Automated coverage verifies diagnostics do not infer target binding from filenames, artifact ordering, UI heuristics, or raw workflow history.
- **SC-008**: Final verification confirms `MM-375`, `DESIGN-REQ-019`, and the original Jira preset brief are preserved in the active MoonSpec artifacts and delivery metadata.
