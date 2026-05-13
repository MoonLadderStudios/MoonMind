# Feature Specification: Runtime Prompt Boundary

**Feature Branch**: `349-runtime-prompt-boundary`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-650 as the canonical Moon Spec orchestration input.

Jira issue: MM-650
Issue type: Story
Current status: In Progress
Summary: Runtime/prompt boundary for text-first vs multimodal adapters

Canonical Jira preset brief:

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 10 Runtime and prompt boundary
Coverage IDs:
- DESIGN-REQ-026

As a runtime-adapter engineer, I want the control plane to pass normalized task intent plus artifact refs to runtimes so that text-first runtimes consume generated image context through the canonical INPUT ATTACHMENTS contract while multimodal runtimes may consume raw image refs directly through their adapters, without any adapter inventing new attachment-targeting rules outside what the Create page can express.

Acceptance Criteria
- Text-first runtimes consume image context through the canonical INPUT ATTACHMENTS contract.
- Multimodal runtimes consume raw image refs through runtime adapters without changing the control-plane task contract.
- Adapters cannot introduce new attachment target kinds or targeting rules.
- Adapter selection does not require changes to the canonical task contract.

Requirements
Implement adapter contract surface and a guardrail (test-level or schema-level) preventing adapter-introduced target kinds.

Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose recommendedImports.presetInstructions, normalizedPresetBrief, presetBrief, presetInstructions, or recommendedPresetInstructions. Non-empty custom fields were non-brief Jira metadata.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-650 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata."

## User Story - Runtime Attachment Boundary

**Summary**: As a runtime-adapter engineer, I want the control plane to pass normalized task intent plus artifact references to runtimes so that text-first runtimes receive generated image context through the canonical input attachment contract while multimodal runtimes can use raw image references without changing the control-plane task contract.

**Goal**: Runtime adapters can choose the correct provider-facing representation for image inputs while the control plane remains the single source of truth for task intent, artifact references, and attachment targeting rules.

**Independent Test**: Can be fully tested by submitting equivalent task intent with image artifact references to text-first and multimodal runtime paths, then verifying that text-first execution receives canonical input attachment context, multimodal execution receives raw image references through its adapter path, and neither path introduces attachment targets beyond the submitted task contract.

**Acceptance Scenarios**:

1. **SCN-001 - Text-first image context**: **Given** task intent with image artifact references for a text-first runtime, **When** the runtime input is prepared, **Then** the runtime receives generated image context through the canonical input attachment contract.
2. **SCN-002 - Multimodal raw refs**: **Given** the same task intent with image artifact references for a multimodal runtime, **When** the runtime input is prepared, **Then** the runtime may receive raw image references through the adapter without changing the control-plane task contract.
3. **SCN-003 - Adapter target guardrail**: **Given** a runtime adapter handles image inputs, **When** it maps task attachments into runtime input, **Then** it cannot introduce target kinds or targeting rules that were not expressible in the originating task contract.
4. **SCN-004 - Runtime selection stability**: **Given** runtime selection changes between text-first and multimodal adapters, **When** the task is prepared for execution, **Then** the canonical task contract remains unchanged and adapter-specific representation is confined to the runtime boundary.

### Edge Cases

- A task references an image artifact but no generated image context is available for a text-first runtime; the system must fail explicitly or report the missing preparation state instead of silently dropping the image input.
- A multimodal adapter receives an image reference with target metadata that is not present in the canonical task contract; the system must reject or ignore the unsupported target mapping without widening the control-plane contract.
- A task includes both objective-level and step-targeted attachments; runtime input preparation must preserve the submitted target binding for each attachment.
- Runtime selection changes after task authoring; the system must keep the same task intent and artifact references while adapting only the runtime-facing representation.

## Assumptions

- The canonical input attachment contract is the existing product-level contract for text-first runtime image context.
- Raw image references for multimodal runtimes remain artifact references rather than inline binary payloads.
- Existing task authoring surfaces define the complete set of valid attachment targets.

## Source Design Requirements

- **DESIGN-REQ-026** (`docs/Tasks/TaskArchitecture.md` section 10, lines 561-570): The control plane must pass normalized task intent plus artifact references, text-first runtimes must consume generated image context through the canonical input attachment contract, multimodal runtimes may consume raw image references through runtime adapters without changing the control-plane task contract, and runtime adapters must not invent attachment targeting rules that the Create page cannot express. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, and FR-006.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST pass normalized task intent and artifact references to runtime preparation without requiring provider-specific payload details in the control-plane task contract.
- **FR-002**: The system MUST provide text-first runtimes with generated image context through the canonical input attachment contract when image artifacts are present.
- **FR-003**: The system MUST allow multimodal runtimes to receive raw image references through runtime adapter behavior without changing the canonical task contract.
- **FR-004**: The system MUST prevent runtime adapters from introducing attachment target kinds that are not expressible by the task authoring contract.
- **FR-005**: The system MUST prevent runtime adapters from introducing attachment targeting rules that are absent from the submitted task intent and artifact references.
- **FR-006**: The system MUST preserve attachment target binding when preparing runtime input for both text-first and multimodal runtime paths.
- **FR-007**: The system MUST surface an explicit failure or diagnostic when required image context or artifact references cannot be prepared for the selected runtime.
- **FR-008**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-650` and the original Jira preset brief for traceability.

### Key Entities

- **Task Intent**: The normalized user objective and execution instructions that remain stable across runtime selection.
- **Artifact Reference**: A durable reference to an input artifact, including image artifacts, that can be prepared for runtime consumption without embedding binary content in the task text.
- **Attachment Target Binding**: The objective-level or step-level target relationship attached to an artifact reference by the task authoring contract.
- **Runtime Input Representation**: The runtime-facing form of task intent and artifact references after adapter preparation, such as generated image context for text-first runtimes or raw image references for multimodal runtimes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of covered text-first runtime preparation cases with image artifacts include generated image context through the canonical input attachment contract.
- **SC-002**: 100% of covered multimodal runtime preparation cases with image artifacts preserve the canonical task contract while allowing raw image references at the runtime boundary.
- **SC-003**: Validation evidence covers at least one attempted adapter-introduced attachment target kind or targeting rule and confirms it is rejected or prevented.
- **SC-004**: Validation evidence covers both objective-level and step-targeted image attachment bindings without target loss.
- **SC-005**: Final traceability review confirms `MM-650`, the original Jira preset brief, and `DESIGN-REQ-026` remain preserved in MoonSpec artifacts and verification evidence.
