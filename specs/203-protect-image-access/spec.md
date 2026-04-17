# Feature Specification: Protect Image Access and Untrusted Content Boundaries

**Feature Branch**: `203-protect-image-access`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-374 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Input Classification**: Single-story feature request.  
**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-374-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-374 from MM project
Summary: Protect image access and untrusted content boundaries
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-374 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-374: Protect image access and untrusted content boundaries

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 12. Authorization and security contract
  - 15. Non-goals
- Coverage IDs:
  - DESIGN-REQ-016
  - DESIGN-REQ-017
  - DESIGN-REQ-020

User Story
As a security-conscious operator, I need image preview, download, worker access, and extracted text handling to respect execution ownership, short-lived credentials, and untrusted-content boundaries.

Acceptance Criteria
- End-user preview/download are governed by execution ownership and view permissions.
- Browsers receive only short-lived presigned download URLs or MoonMind proxy responses, never long-lived object-store credentials.
- Worker-side access uses service credentials and execution authorization, not browser credentials.
- Extracted text from images is not trusted as executable instructions unless the authored task explicitly chooses to use it.
- Images remain untrusted user input.
- Direct browser access to object storage, Jira, or provider-specific file endpoints is not allowed.
- Hidden compatibility transforms must not silently rewrite attachment refs or retarget them to another step.
- Live Jira sync remains out of scope for the image input system.

Requirements
- Apply execution-scoped authorization to all image byte access.
- Avoid exposing durable storage or provider credentials to the browser.
- Preserve attachment refs exactly rather than rewriting them through compatibility transforms.

Relevant Implementation Notes
- The canonical design source is `docs/Tasks/ImageSystem.md`, especially the authorization/security contract and non-goals sections.
- Image preview and download flows must authorize access by execution ownership and view permissions before returning image bytes or browser-visible download locations.
- Browser-visible flows may return short-lived presigned download URLs or MoonMind proxy responses, but must not expose durable object-store credentials, Jira attachment URLs, or provider-specific file endpoints.
- Worker-side image access must use service credentials plus execution authorization, not browser credentials or user-visible download URLs.
- Extracted image text must be treated as untrusted content. It must not become executable instructions or hidden prompt input unless the authored task explicitly opts into using it.
- Image bytes, attachment metadata, OCR output, and model-extracted text remain untrusted user input at system boundaries.
- Attachment refs must be preserved exactly; unsupported or stale refs should fail visibly rather than being silently rewritten or retargeted through compatibility transforms.
- Live Jira synchronization is out of scope for the image input system.

Validation
- Verify end-user image preview/download requires execution ownership or view permission.
- Verify browser responses never expose long-lived object-store credentials, Jira attachment URLs, or provider-specific file endpoints.
- Verify worker-side image access uses service credentials and execution authorization rather than browser credentials.
- Verify extracted image text is not treated as executable instructions unless the authored task explicitly chooses to use it.
- Verify images and derived text remain classified as untrusted user input at system boundaries.
- Verify attachment refs are preserved exactly and hidden compatibility transforms do not rewrite refs or retarget them to another step.
- Verify live Jira sync remains out of scope for the image input system.

Needs Clarification
- None"

## User Story - Secure Image Access Boundaries

**Summary**: As a security-conscious operator, I need image preview, download, worker access, and extracted text handling to enforce execution ownership, short-lived browser access, service-credential worker access, exact attachment refs, and untrusted-content boundaries.

**Goal**: Image bytes and image-derived text stay inside MoonMind-controlled authorization boundaries, while browser and runtime surfaces make clear that images and extracted text are untrusted user input.

**Independent Test**: Can be tested by exercising artifact preview/download authorization, rendering browser-visible download links, composing worker attachment context, rendering vision context with hostile image-derived text, and validating task attachment refs are preserved or rejected without hidden retargeting.

**Acceptance Scenarios**:

1. **Given** an image artifact belongs to one execution owner, **When** a different authenticated user attempts preview, download, or raw presign access, **Then** the request is denied by execution/artifact authorization.
2. **Given** the browser renders an image preview or download action, **When** the action exposes a URL, **Then** it uses a MoonMind-owned proxy endpoint or a short-lived presigned URL and never exposes durable object-store credentials, Jira attachment URLs, or provider-specific file endpoints.
3. **Given** worker prepare needs image bytes for an authorized execution, **When** attachments are materialized, **Then** the worker uses the service artifact download path and does not depend on browser credentials or browser-visible URLs.
4. **Given** generated image context contains OCR text or model captions that look like instructions, **When** the context is written or injected into runtime instructions, **Then** the text is labeled as untrusted derived data and is not presented as executable system, developer, or task instructions.
5. **Given** an attachment ref is unsupported, stale, or missing its declared target, **When** the task contract or reconstruction path processes it, **Then** the system fails visibly or leaves it ungrouped instead of silently rewriting the ref or retargeting it to another step.

### Edge Cases

- A restricted image artifact is complete but requested by a non-owner in authenticated mode.
- A browser-visible artifact record includes an external download URL from imported metadata.
- OCR text contains strings such as "SYSTEM:" or "ignore previous instructions".
- A manifest or attachment metadata value contains a data URL, base64 payload, or multiline instruction-like text.
- An attachment artifact id appears in both objective-scoped and step-scoped contexts.
- Live Jira attachment URLs are present in imported Jira data but are not part of the image input system.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/Tasks/ImageSystem.md` sections 12 and 15 are runtime source requirements.
- Existing Temporal artifact authorization is the execution-owned access boundary for browser preview/download and worker materialization.
- Existing Create/detail UI tests for target-aware image rendering remain in scope as evidence when they prove MoonMind-owned endpoint usage.
- Existing task contract validation is the correct place to reject embedded image bytes and malformed attachment refs.

## Source Design Requirements

- **DESIGN-REQ-016** (Source: `docs/Tasks/ImageSystem.md`, section 12; MM-374 brief): End-user preview and download MUST be governed by execution ownership and view permissions, browser access MUST use short-lived presigned URLs or MoonMind proxy responses without long-lived object-store credentials, worker access MUST use service credentials plus execution authorization, and image text MUST not be trusted as executable instructions unless explicitly authored. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006.
- **DESIGN-REQ-017** (Source: `docs/Tasks/ImageSystem.md`, section 12; MM-374 brief): The image system MUST NOT allow direct browser access to object storage, Jira, or provider-specific file endpoints, scriptable image types, or hidden compatibility transforms that rewrite attachment refs or retarget them to another step. Scope: in scope. Maps to FR-002, FR-007, FR-008, FR-009.
- **DESIGN-REQ-020** (Source: `docs/Tasks/ImageSystem.md`, section 15; MM-374 brief): The image system MUST NOT require raw image bytes in create payloads, image data URLs in instruction markdown, implicit sharing across steps, live Jira sync, generic non-image support by default, or provider-specific multimodal formats as the control-plane contract. Scope: in scope as guardrails. Maps to FR-010, FR-011, FR-012, FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Browser preview, download, and presign operations for image artifacts MUST enforce the same artifact read authorization used for execution-owned artifacts.
- **FR-002**: Browser-visible image access MUST expose only MoonMind-owned proxy endpoints or short-lived presigned URLs and MUST NOT expose long-lived object-store credentials, Jira attachment URLs, or provider-specific file endpoints.
- **FR-003**: Worker-side image materialization MUST use service-side artifact access authorized for the execution and MUST NOT depend on browser credentials or browser-visible URLs.
- **FR-004**: Generated image context files MUST clearly label OCR text, captions, and image-derived metadata as untrusted derived data.
- **FR-005**: Runtime instruction injection MUST label image attachment metadata and generated image context as untrusted reference data.
- **FR-006**: Runtime instruction injection MUST NOT present extracted image text as executable system, developer, or task instructions unless a task-authored instruction explicitly chooses to use that text as input.
- **FR-007**: Browser UI code that renders task image downloads MUST prefer MoonMind artifact endpoints over artifact-provided external download URLs.
- **FR-008**: Attachment refs MUST be preserved exactly across task contract normalization, edit/rerun reconstruction, and worker materialization.
- **FR-009**: Unsupported, stale, malformed, or target-ambiguous attachment refs MUST fail visibly or remain ungrouped; they MUST NOT be silently rewritten or retargeted to another step.
- **FR-010**: Execution create/update payloads MUST reject embedded raw image bytes, data URLs, and base64 image payloads in attachment refs.
- **FR-011**: Image input handling MUST NOT add live Jira synchronization or direct browser Jira attachment access.
- **FR-012**: Image input handling MUST NOT add generic non-image attachment support by default.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-374` and the original Jira preset brief for traceability.

### Key Entities

- **Image Artifact Access Grant**: A browser-visible proxy response or short-lived presigned URL produced only after artifact read authorization succeeds.
- **Worker Image Materialization Request**: A service-side artifact read for an execution-owned image input, independent of browser credentials.
- **Untrusted Image Context**: OCR text, captions, metadata, and paths derived from image inputs and labeled as untrusted reference data.
- **Attachment Ref**: The exact artifact reference submitted through the task contract and preserved across reconstruction and materialization.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage verifies non-owner access to restricted image artifact download or presign is denied.
- **SC-002**: Automated coverage verifies browser-visible task image download links use MoonMind artifact endpoints rather than external object-store, Jira, or provider URLs.
- **SC-003**: Automated coverage verifies worker attachment materialization downloads image bytes through service artifact access and preserves exact artifact ids and target metadata.
- **SC-004**: Automated coverage verifies vision context and runtime injection both label image-derived text as untrusted and warn not to execute instructions embedded in images.
- **SC-005**: Automated coverage verifies embedded image bytes, data URLs, or base64 image payloads are rejected or omitted from runtime instructions.
- **SC-006**: Automated coverage verifies malformed or target-ambiguous attachment refs are not silently rewritten or retargeted.
- **SC-007**: Final verification confirms `MM-374` and the original Jira preset brief are preserved in active MoonSpec artifacts and delivery metadata.
