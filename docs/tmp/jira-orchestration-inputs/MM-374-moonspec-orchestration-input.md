# MM-374 MoonSpec Orchestration Input

## Source

- Jira issue: MM-374
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Protect image access and untrusted content boundaries
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

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

Suggested Implementation Areas
- Execution-scoped image preview and download authorization checks.
- MoonMind-owned image download or proxy route behavior.
- Worker-side image byte access authorization and credential boundaries.
- Prompt/context construction paths that handle image-extracted text.
- Attachment ref validation paths that currently normalize, rewrite, or infer targets.
- Tests for unauthorized preview/download attempts, browser credential isolation, worker access boundaries, untrusted extracted text handling, and fail-fast attachment refs.

Validation
- Verify end-user image preview/download requires execution ownership or view permission.
- Verify browser responses never expose long-lived object-store credentials, Jira attachment URLs, or provider-specific file endpoints.
- Verify worker-side image access uses service credentials and execution authorization rather than browser credentials.
- Verify extracted image text is not treated as executable instructions unless the authored task explicitly chooses to use it.
- Verify images and derived text remain classified as untrusted user input at system boundaries.
- Verify attachment refs are preserved exactly and hidden compatibility transforms do not rewrite refs or retarget them to another step.
- Verify live Jira sync remains out of scope for the image input system.

Non-Goals
- Direct browser access to object storage, Jira attachment URLs, provider-specific file endpoints, or durable provider credentials.
- Hidden compatibility transforms that rewrite attachment refs, infer missing targets, or retarget attachments to another step.
- Treating image-extracted text as trusted system, developer, or executable user instructions by default.
- Live Jira synchronization for the image input system.

Needs Clarification
- None
