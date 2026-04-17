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

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-368-moonspec-orchestration-input.md`

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
