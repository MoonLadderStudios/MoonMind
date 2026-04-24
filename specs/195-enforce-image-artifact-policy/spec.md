# Feature Specification: Enforce Image Artifact Storage and Policy

**Feature Branch**: `195-enforce-image-artifact-policy`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-368 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-368 from MM project
Summary: Enforce image artifact storage and policy
Issue type: Story
Current Jira status at trusted fetch time: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-368 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-368: Enforce image artifact storage and policy

Source Reference:
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 6. Artifact model and storage contract
  - 7. Validation and policy contract
  - 12. Authorization and security contract
- Coverage IDs:
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-017

User Story:
As an operator, I need uploaded image bytes stored as first-class execution artifacts and governed by server-defined attachment policy so invalid or unsupported image inputs never start an execution.

Acceptance Criteria:
- Image bytes are stored in the Artifact Store and linked to the execution as input attachments.
- Allowed content types default to `image/png`, `image/jpeg`, and `image/webp`; `image/svg+xml` is rejected.
- Browser checks are repeated server-side before artifact completion or execution start.
- Max count, per-file size, total size, and integrity constraints are enforced.
- Worker-side uploads cannot overwrite or impersonate reserved input attachment namespaces.
- Disabled policy hides Create-page entry points and rejects submitted image refs.
- Unsupported future fields and incompatible runtimes fail explicitly rather than being ignored or dropped.

Requirements:
- Persist uploaded image bytes as artifacts rather than execution payload data.
- Attach execution-owned artifact links for submitted images.
- Treat artifact metadata as observability, not as binding source of truth.
- Revalidate content type, signature, counts, sizes, and completion integrity server-side.
- Reject scriptable image content types and other untrusted image risks.
- Keep the task snapshot as the authoritative source for attachment target binding.
- Prevent worker-side uploads from overwriting or impersonating reserved input attachment namespaces.
- Enforce server-defined attachment policy even when browser-side checks have already run.
- Fail explicitly when image attachments are disabled, unsupported by the selected runtime, incomplete, invalid, or include unsupported future fields.

Relevant Implementation Notes:
- Canonical image input field: `inputAttachments`.
- Objective-scoped attachments are submitted through `task.inputAttachments`.
- Step-scoped attachments are submitted through `task.steps[n].inputAttachments`.
- Image bytes must not be embedded in Temporal histories or task instruction text.
- The control plane submits structured attachment references, not raw binaries.
- The execution API persists the authoritative snapshot of attachment targeting.
- Image artifacts should be linked to the execution with execution-owned artifact links.
- Artifact metadata may include target diagnostics such as `source`, `attachmentKind`, `targetKind`, `stepRef`, `stepOrdinal`, and `originalFilename`, but metadata is not the binding source of truth.
- Integrity must be enforced at artifact completion time before execution start.
- Policy defaults should include `enabled=true`, max count, per-file size, total size, and allowed content types of `image/png`, `image/jpeg`, and `image/webp`.
- The Create page may label the feature as images, but the implementation should preserve the generic `inputAttachments` contract.
- Security boundaries are artifact-first and execution-owned: no direct browser access to object storage, no direct browser access to Jira or provider file endpoints, no scriptable image types, and no silent compatibility transforms that rewrite attachment refs or retarget them to another step.

Suggested Implementation Areas:
- Artifact upload creation, completion, and validation paths.
- Execution submission validation and task snapshot persistence.
- Create-page image entry point visibility and browser-side policy checks.
- Server-side attachment policy enforcement before artifact completion or execution start.
- Worker artifact upload namespace protections.
- Tests covering artifact storage, policy rejection, execution linkage, disabled policy behavior, and unsupported runtime or future-field failure.

Validation:
- Verify uploaded image bytes are persisted as artifacts and linked to the execution as input attachments.
- Verify `image/png`, `image/jpeg`, and `image/webp` are accepted by default and `image/svg+xml` is rejected.
- Verify server-side validation repeats browser checks for content type, signature, max count, per-file size, total size, and completion integrity.
- Verify invalid, incomplete, over-limit, or scriptable image uploads are rejected before execution start.
- Verify disabled attachment policy hides Create-page entry points and rejects submitted image refs.
- Verify worker-side uploads cannot overwrite or impersonate reserved input attachment namespaces.
- Verify unsupported future fields and incompatible runtimes fail explicitly instead of being ignored or dropped.

Non-Goals:
- Embedding raw image bytes in Temporal histories, workflow payloads, or task instruction text.
- Treating artifact metadata as the authoritative attachment binding source.
- Allowing `image/svg+xml` or other scriptable image content types.
- Adding hidden compatibility transforms that silently rewrite attachment refs or retarget them to another step.
- Redesigning the broader artifact store, retention model, or runtime adapter architecture beyond the storage and policy enforcement needed for this story.

Needs Clarification:
- None

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Enforce Image Artifact Storage and Policy

**Summary**: As an operator, I need uploaded image bytes stored as first-class execution artifacts and governed by server-defined attachment policy so invalid or unsupported image inputs never start an execution.

**Goal**: Image task inputs are accepted only when the server can prove that the uploaded artifacts satisfy the configured image policy, are complete, are linked as execution-owned input attachments, and cannot be impersonated by worker artifact uploads.

**Independent Test**: Submit task-shaped execution requests with valid and invalid image attachment refs and verify that valid images remain artifact-backed input attachments while disabled, incomplete, oversized, unsupported, scriptable, future-field, incompatible-runtime, and reserved-namespace attempts fail before execution starts.

**Acceptance Scenarios**:

1. **Given** attachment policy is enabled and completed image artifacts use allowed content types, **When** a task-shaped execution is submitted with objective-scoped and step-scoped `inputAttachments`, **Then** the execution is accepted with artifact-backed input attachment refs preserved in the task snapshot.
2. **Given** an attachment ref points to an incomplete artifact, an unsupported content type, a scriptable image type, an over-limit size/count/total, or a mismatched integrity signal, **When** execution submission or artifact completion is attempted, **Then** the request is rejected before execution starts.
3. **Given** attachment policy is disabled, **When** the Create page loads or a task-shaped execution request submits image refs, **Then** image entry points are not offered and submitted refs are rejected.
4. **Given** a worker-side artifact upload targets a reserved input attachment namespace, **When** the upload is requested, **Then** the upload is rejected and cannot overwrite or impersonate input attachments.
5. **Given** an attachment ref includes unsupported future fields or the selected runtime cannot consume image attachments, **When** execution submission is attempted, **Then** the system fails explicitly instead of silently ignoring or dropping attachment refs.

### Edge Cases

- The browser reports an allowed image type, but the server-side content type or signature validation does not confirm a supported image.
- The selected files are individually valid but exceed configured max count or total-size policy.
- An artifact upload was created but not completed before task submission.
- A user attempts to submit `image/svg+xml` or another scriptable content type.
- A worker upload uses a path or namespace reserved for input attachments.
- A task edit or rerun reconstructs a draft containing existing attachment refs while attachment policy is now disabled.
- A request contains fields not yet supported by the attachment contract.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/Tasks/ImageSystem.md` is treated as source requirements for runtime behavior.
- The canonical control-plane field name remains `inputAttachments`.
- Objective-scoped attachments are represented by `task.inputAttachments`; step-scoped attachments are represented by `task.steps[n].inputAttachments`.
- Existing artifact APIs remain the storage surface for uploaded image bytes.

## Source Design Requirements

- **DESIGN-REQ-008** (Source: `docs/Tasks/ImageSystem.md`, section 6; MM-368 brief): Image bytes MUST be stored in the Artifact Store and linked to the execution as input attachments. Scope: in scope. Maps to FR-001, FR-002, FR-008.
- **DESIGN-REQ-009** (Source: `docs/Tasks/ImageSystem.md`, section 6; MM-368 brief): Allowed content types MUST default to `image/png`, `image/jpeg`, and `image/webp`; `image/svg+xml` MUST be rejected. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-010** (Source: `docs/Tasks/ImageSystem.md`, section 7; MM-368 brief): Browser checks MUST be repeated server-side before artifact completion or execution start, including content type, signature, count, size, total size, and integrity constraints. Scope: in scope. Maps to FR-004, FR-005, FR-006.
- **DESIGN-REQ-017** (Source: `docs/Tasks/ImageSystem.md`, section 12; MM-368 brief): Authorization and security boundaries MUST prevent direct browser storage access, worker-side input namespace impersonation, scriptable image types, and hidden compatibility transforms that silently rewrite attachment refs. Scope: in scope. Maps to FR-007, FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST persist uploaded image bytes as artifacts rather than embedding image bytes in execution payloads, workflow histories, or task instruction text.
- **FR-002**: System MUST preserve objective-scoped and step-scoped `inputAttachments` as execution-owned artifact links in the authoritative task snapshot.
- **FR-003**: System MUST default image attachment policy to allow `image/png`, `image/jpeg`, and `image/webp`.
- **FR-004**: System MUST reject `image/svg+xml`, scriptable image types, and any image content type outside the configured allowlist.
- **FR-005**: System MUST revalidate browser-side attachment checks server-side before artifact completion or execution start.
- **FR-006**: System MUST enforce configured max count, per-file size, total size, upload completion, and integrity constraints before execution starts.
- **FR-007**: System MUST reject worker-side artifact uploads that attempt to overwrite or impersonate reserved input attachment namespaces.
- **FR-008**: System MUST reject submitted image refs when attachment policy is disabled, and Create-page image entry points MUST be unavailable when policy is disabled.
- **FR-009**: System MUST fail explicitly for unsupported future attachment fields and incompatible runtimes instead of silently ignoring, dropping, rewriting, or retargeting attachment refs.
- **FR-010**: System MUST treat artifact metadata as observability only; authoritative target binding MUST come from the task snapshot.

### Key Entities

- **Input Attachment Ref**: A lightweight task payload reference to an artifact-backed image, including artifact identity, filename, content type, and byte size.
- **Attachment Policy**: Server-defined rules controlling enablement, allowed image content types, max count, per-file size, and total size.
- **Execution-Owned Artifact Link**: The relationship between a submitted task execution and an input attachment artifact.
- **Task Snapshot**: The authoritative persisted task input state that preserves objective and step attachment target binding.
- **Reserved Input Namespace**: Artifact storage locations reserved for user-submitted input attachments and unavailable to worker-side artifact uploads.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Valid objective-scoped and step-scoped image attachments are accepted only as artifact-backed refs and remain present in the task snapshot in automated coverage.
- **SC-002**: Unsupported, scriptable, incomplete, over-count, oversized, over-total, or integrity-invalid image attachments are rejected before execution start in automated coverage.
- **SC-003**: Disabled attachment policy prevents Create-page image entry points and rejects submitted image refs in automated coverage.
- **SC-004**: Worker-side uploads to reserved input namespaces are rejected in automated coverage.
- **SC-005**: Unsupported future fields and incompatible-runtime inputs produce explicit validation failures without silently dropping attachment refs in automated coverage.
