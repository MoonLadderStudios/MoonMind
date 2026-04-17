# Story Breakdown: Task Image Input System

- Source design: `docs/Tasks/ImageSystem.md`
- Original source reference path: `docs/Tasks/ImageSystem.md`
- Story extraction date: `2026-04-17T01:29:41Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The Task Image Input System defines an artifact-first, target-aware contract for image inputs across task creation, execution, preparation, runtime injection, preview, security, durability, and diagnostics. Users bind images to either the task objective or individual steps; MoonMind stores bytes in the Artifact Store, submits only structured refs through /api/executions, preserves target bindings in authoritative snapshots, materializes deterministic workspace files and manifests, optionally generates auditable vision context, and exposes previews/downloads through MoonMind-owned APIs. The design explicitly excludes raw image payloads in execution requests, instruction data URLs, implicit cross-step sharing, live Jira sync, generic non-image attachments by default, and provider-specific multimodal formats as control-plane contracts.

## Coverage Points

- `DESIGN-REQ-001` **Artifact-first image input purpose** (requirement, 1. Purpose): Users can attach images to task objectives or individual steps, store bytes securely as artifacts, submit lightweight refs into MoonMind.Run, preserve targeting through create/edit/rerun, generate deterministic context, materialize raw files when needed, and preview/download through MoonMind-owned APIs.
- `DESIGN-REQ-002` **Structured attachment terminology and reference shapes** (state-model, 3. Product stance and terminology): The canonical field is inputAttachments; objective and step attachment scopes have explicit meanings; TaskInputAttachmentRef and AttachmentManifestEntry define the core contract and target meaning comes from containing fields, not filenames.
- `DESIGN-REQ-003` **No raw binaries in histories or instruction text** (constraint, 3.1 Product stance): Uploaded bytes must not be embedded in Temporal histories or task instruction text; control-plane submissions carry structured refs and derived summaries remain secondary artifacts.
- `DESIGN-REQ-004` **End-to-end upload-to-runtime flow** (integration, 4. End-to-end desired-state flow): Upload completion precedes create/update/rerun acceptance; the execution API persists authoritative targeting; workflow prepare materializes files and manifests; runtime adapters consume refs and derived context.
- `DESIGN-REQ-005` **Canonical task-shaped submission path** (integration, 5. Control-plane contract): Image inputs are submitted through /api/executions using the task-shaped Temporal execution API; legacy queue-specific routes are not desired state and browser upload/download remain behind MoonMind-owned endpoints.
- `DESIGN-REQ-006` **Objective and step attachment scoping** (state-model, 5.2 Canonical task contract): task.inputAttachments are objective-scoped; task.steps[n].inputAttachments are step-scoped; the API preserves target scoping through create, edit, and rerun and normalizes refs before workflow start.
- `DESIGN-REQ-007` **Authoritative task input snapshot** (durability, 5.3 Authoritative snapshot contract): The original task input snapshot preserves text, target refs, step identity/order, runtime, publish, repository settings, and preset metadata; reconstruction uses the snapshot and fails explicitly if bindings would be discarded.
- `DESIGN-REQ-008` **First-class artifact storage and metadata** (artifact, 6. Artifact model and storage contract): Image bytes are stored in the Artifact Store, linked to executions, retain optional target diagnostic metadata, and follow artifact retention policy; payload and snapshot remain source of truth for binding.
- `DESIGN-REQ-009` **Attachment policy validation** (security, 7. Validation and policy contract): Policy is server-defined and browser-enforced, then revalidated server-side with allowed content types, signature sniffing, count and size limits, artifact completion integrity, invalid upload rejection, and fail-fast handling for unsupported future fields.
- `DESIGN-REQ-010` **Policy-disabled and unsupported-runtime behavior** (constraint, 7. Validation and policy contract): When policy is disabled, Create-page image entry points are hidden and refs are rejected; if enabled but the selected runtime cannot consume images, create fails explicitly rather than dropping attachments.
- `DESIGN-REQ-011` **Deterministic prepare-time materialization** (artifact, 8. Prepare-time materialization contract): Prepare downloads all declared attachments, writes .moonmind/attachments_manifest.json, materializes objective and step files under stable target-aware paths, assigns stable step refs when needed, and treats partial materialization as failure.
- `DESIGN-REQ-012` **Target-aware vision context generation** (artifact, 9. Vision context generation contract): Image-derived text context is deterministic, target-aware, configurable, traceable to source image refs, emits objective/step context files plus an index, and leaves manifest and raw materialization intact when disabled.
- `DESIGN-REQ-013` **Text-first runtime injection rules** (integration, 10.1 Text-first runtimes): Text-first runtimes receive an INPUT ATTACHMENTS block before WORKSPACE with relevant workspace paths, manifest entries, and context paths; step execution receives objective plus current-step context only unless cross-step access is explicitly requested.
- `DESIGN-REQ-014` **Planning and multimodal runtime contract** (integration, 10.2 Planning and task-level reasoning; 10.3 Multimodal runtimes): Planning gets objective context and a compact inventory of later step attachments; multimodal adapters may consume raw refs directly without changing the control-plane contract or source of truth.
- `DESIGN-REQ-015` **Target-aware preview, download, edit, and rerun UI** (requirement, 11. UI preview and detail contract; 13. Edit and rerun durability contract): Task detail, edit, and rerun surfaces preview/download through MoonMind APIs, organize images by target, avoid filename inference, retain metadata/actions on preview failure, and distinguish persisted refs from new local files.
- `DESIGN-REQ-016` **Execution-owned authorization boundaries** (security, 12. Authorization and security contract): Preview/download are governed by execution ownership and permissions; browsers receive short-lived URLs or proxy responses, not object-store credentials; worker access uses service credentials and execution authorization.
- `DESIGN-REQ-017` **Untrusted image and extracted-text handling** (security, 12. Authorization and security contract): Images and extracted text remain untrusted; extracted text is not executable instructions unless explicitly authored; scriptable image types, direct browser object/Jira/provider file access, and hidden compatibility retargeting transforms are forbidden.
- `DESIGN-REQ-018` **Edit and rerun attachment durability** (durability, 13. Edit and rerun durability contract): Create, edit, and rerun use the same attachment contract; unchanged refs survive, removing or adding attachments is explicit, browser text-only reconstruction cannot drop attachments, and old artifacts may remain under retention.
- `DESIGN-REQ-019` **Observable diagnostics and event evidence** (observability, 14. Observability and diagnostics contract): The system emits upload, validation, prepare, and context-generation events; diagnostics expose manifest/context paths and target-aware metadata; step-level failures identify the affected step target.
- `DESIGN-REQ-020` **Explicit non-goals** (non-goal, 15. Non-goals): The design excludes raw image bytes in create payloads, data URLs in instruction markdown, implicit step sharing, live Jira sync, generic non-image attachments by default, and provider-specific multimodal message formats as the control-plane contract.

## Ordered Story Candidates

### STORY-001: Create targeted image attachment submission

- Short name: `targeted-image-submission`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 1. Purpose, 3. Product stance and terminology, 4. End-to-end desired-state flow, 5. Control-plane contract, 15. Non-goals
- Dependencies: None
- Independent test: Submit a task-shaped payload containing one objective image ref and one step image ref, then assert the API starts/updates the run with normalized refs under the same targets and no raw binary or data URL content in the workflow input.
- Description: As a task author, I need the Create page and task-shaped execution submission to bind images to the objective or a specific step using structured inputAttachments refs so MoonMind.Run receives explicit lightweight references instead of raw image data.

Acceptance criteria:
- Create flow supports image refs on the task objective and on individual steps.
- Submitted payloads use task.inputAttachments and task.steps[n].inputAttachments as the only canonical target fields.
- Attachment identity and target meaning are not inferred from filenames.
- The workflow input carries artifact refs and compact metadata, not embedded image bytes or image data URLs.
- Legacy queue-specific attachment routes are not treated as the desired-state submission contract.

Requirements:
- Use inputAttachments as the canonical control-plane field name.
- Preserve objective-scoped and step-scoped target meaning from the containing field.
- Normalize TaskInputAttachmentRef objects before workflow start.
- Keep all browser upload and download flows behind MoonMind-owned API endpoints.
- Represent explicit image-system non-goals in validation or documentation for this contract surface.

Scope:
- Create-page objective and step image target selection
- Task-shaped /api/executions payloads using inputAttachments
- TaskInputAttachmentRef normalization before workflow start
- Rejection of legacy/raw-binary/data-URL submission patterns as desired-state behavior

Out of scope:
- Artifact byte validation and retention
- Edit/rerun reconstruction
- Runtime prompt injection

Owned source design coverage:
- `DESIGN-REQ-001`: Owns the author-facing ability to attach images to objective and step targets and submit refs into MoonMind.Run.
- `DESIGN-REQ-002`: Owns canonical inputAttachments terminology and TaskInputAttachmentRef shape for submission.
- `DESIGN-REQ-003`: Owns the no raw binaries/no instruction-text embedding rule at submission time.
- `DESIGN-REQ-004`: Owns the front half of the end-to-end flow through execution submission and workflow start.
- `DESIGN-REQ-005`: Owns the canonical /api/executions task-shaped submission path.
- `DESIGN-REQ-006`: Owns objective and step scoping in task-shaped payloads.
- `DESIGN-REQ-020`: Owns non-goals that directly affect submission shape: no raw bytes, no data URLs, no implicit step sharing, and no provider-specific control-plane format.

Assumptions:
- Existing Artifact API upload primitives can provide completed artifact refs before task submission.

### STORY-002: Enforce image artifact storage and policy

- Short name: `image-artifact-policy`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 6. Artifact model and storage contract, 7. Validation and policy contract, 12. Authorization and security contract
- Dependencies: STORY-001
- Independent test: Attempt uploads for allowed PNG/JPEG/WebP files, forbidden SVG, mismatched MIME/signature content, over-limit files, disabled policy, and a runtime-declared unsupported image case; assert only valid completed artifacts can be referenced by an execution.
- Description: As an operator, I need uploaded image bytes stored as first-class execution artifacts and governed by server-defined attachment policy so invalid or unsupported image inputs never start an execution.

Acceptance criteria:
- Image bytes are stored in the Artifact Store and linked to the execution as input attachments.
- Allowed content types default to image/png, image/jpeg, and image/webp; image/svg+xml is rejected.
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

Scope:
- Artifact Store persistence for uploaded image bytes
- Execution-owned artifact links and recommended metadata
- Policy allowlist and limits with browser enforcement plus server revalidation
- Completion-time integrity and signature checks
- Fail-fast behavior for disabled policy, unsupported fields, invalid content, and unsupported runtimes

Out of scope:
- Prompt injection format
- Task detail preview UI
- Vision summarization

Owned source design coverage:
- `DESIGN-REQ-008`: Owns first-class artifact storage, execution links, retention, metadata, and reserved namespace protection.
- `DESIGN-REQ-009`: Owns server-defined attachment policy, MIME/signature validation, counts, sizes, integrity, and fail-fast future fields.
- `DESIGN-REQ-010`: Owns disabled-policy and unsupported-runtime rejection semantics.
- `DESIGN-REQ-017`: Owns the scriptable-image prohibition at validation time.

### STORY-003: Preserve attachment bindings in snapshots and reruns

- Short name: `attachment-snapshot-durability`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 5.3 Authoritative snapshot contract, 11. UI preview and detail contract, 13. Edit and rerun durability contract
- Dependencies: STORY-001, STORY-002
- Independent test: Create a task with objective and step attachments, edit text only, rerun it, remove one attachment, and add one new attachment; assert unchanged refs and target bindings survive exactly while explicit changes are reflected.
- Description: As a user editing or rerunning a task, I need MoonMind to reconstruct attachments from the authoritative task input snapshot so unchanged bindings survive and changes are always explicit.

Acceptance criteria:
- The snapshot preserves text fields, target attachment refs, step identity/order, runtime, publish, repository settings, and applied preset metadata.
- Attachment target binding is reconstructed from the snapshot, not inferred from artifact links or filenames.
- Unchanged attachment refs survive edit and rerun unchanged.
- Removing an attachment and adding a new attachment are explicit user actions.
- A text-only draft reconstruction cannot silently drop attachments.
- The system fails explicitly if attachment bindings cannot be reconstructed.
- Historical artifacts may remain according to retention even after an edited draft stops referencing them.

Requirements:
- Persist target attachment refs in the task input snapshot.
- Use the same attachment contract for create, edit, and rerun.
- Keep step identity and ordering stable enough to bind step-scoped attachments.
- Distinguish persisted attachment refs from new local files in edit/rerun flows.

Scope:
- Authoritative task input snapshot fields
- Reconstruction of objective and step attachment bindings from snapshot data
- Edit and rerun preservation of unchanged refs
- Explicit add/remove semantics
- Failure when reconstruction would discard bindings

Out of scope:
- Initial artifact upload policy
- Prepare-time file download
- Preview byte authorization

Owned source design coverage:
- `DESIGN-REQ-007`: Owns the authoritative snapshot fields and fail-explicit reconstruction requirement.
- `DESIGN-REQ-015`: Owns the edit/rerun distinction between persisted attachments and new local files.
- `DESIGN-REQ-018`: Owns durable unchanged refs, explicit add/remove actions, no browser-induced disappearance, and historical retention semantics.

### STORY-004: Materialize attachment manifest and workspace files

- Short name: `prepare-attachment-materialization`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 3.2 Canonical terminology, 4. End-to-end desired-state flow, 8. Prepare-time materialization contract
- Dependencies: STORY-001, STORY-002
- Independent test: Run prepare against a task with multiple objective and step attachments, including a step with no explicit id, and assert manifest entries, stable paths, target fields, step refs, and failure on a simulated missing download.
- Description: As a runtime executor, I need workflow prepare to deterministically download declared input attachments, write a canonical manifest, and place files in target-aware workspace paths before the relevant runtime or step executes.

Acceptance criteria:
- Prepare downloads all declared input attachments before the relevant runtime or step executes.
- Prepare writes .moonmind/attachments_manifest.json using the canonical manifest entry shape.
- Objective images are materialized under .moonmind/inputs/objective/.
- Step images are materialized under .moonmind/inputs/steps/<stepRef>/.
- Workspace paths are deterministic and target-aware; one target path does not depend on unrelated target ordering.
- A stable step reference is assigned when a step has no explicit id.
- Partial materialization is reported as failure, not best-effort success.

Requirements:
- Include artifactId, filename, contentType, sizeBytes, targetKind, optional stepRef/stepOrdinal, workspacePath, and optional context/source paths in manifest entries.
- Sanitize filenames while preserving deterministic artifactId-prefixed paths.
- Treat execution payload and snapshot refs as the source for materialization.

Scope:
- AttachmentManifestEntry generation
- Download of all declared input attachments before relevant execution
- Canonical .moonmind/attachments_manifest.json output
- Stable objective and step workspace paths
- Stable step reference assignment when step ids are absent
- Partial materialization failure semantics

Out of scope:
- Vision context generation
- Provider-specific multimodal payload construction
- Task detail preview UI

Owned source design coverage:
- `DESIGN-REQ-002`: Owns AttachmentManifestEntry materialization shape.
- `DESIGN-REQ-004`: Owns prepare responsibilities in the end-to-end flow.
- `DESIGN-REQ-011`: Owns deterministic manifest and raw workspace materialization rules.

### STORY-005: Generate target-aware vision context artifacts

- Short name: `vision-context-artifacts`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 9. Vision context generation contract
- Dependencies: STORY-004
- Independent test: Prepare a task with objective and step images with context enabled and disabled; assert enabled runs write target-specific context files and index entries, disabled runs still produce manifest/raw files, and context records reference source artifact ids.
- Description: As a text-first runtime user, I need deterministic image-derived context artifacts that remain traceable to source image refs and preserve objective versus step target meaning.

Acceptance criteria:
- Context generation is target-aware and may be enabled or disabled by runtime configuration.
- When disabled, manifest generation and raw file materialization still occur.
- Objective-scoped images produce .moonmind/vision/task/image_context.md.
- Step-scoped images produce .moonmind/vision/steps/<stepRef>/image_context.md.
- .moonmind/vision/image_context_index.json summarizes which context exists for each target.
- Generated text remains traceable to source image artifact refs and deterministic/auditable for a given source image set and model configuration.

Requirements:
- Support OCR, captions, and safety notes only as deterministic auditable content.
- Keep derived image summaries secondary to source image refs.
- Preserve target meaning during context generation.

Scope:
- Configurable enable/disable of vision context generation
- Objective and step context files
- Context index artifact
- Traceability from generated context to source artifact refs
- Deterministic and auditable output for a given image set and model configuration

Out of scope:
- Raw file materialization when context generation is disabled
- Prompt block placement
- Provider-specific multimodal payloads

Owned source design coverage:
- `DESIGN-REQ-012`: Owns all target-aware vision context generation, disablement, traceability, and output path requirements.

### STORY-006: Inject attachment context into runtimes

- Short name: `runtime-attachment-injection`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 10. Prompt and runtime injection contract, 15. Non-goals
- Dependencies: STORY-004, STORY-005
- Independent test: Execute a planning turn and two step turns with objective plus per-step images; assert prompt/context payloads include objective context, current step context only, compact later-step inventory for planning, and no provider-specific format leakage into control-plane contracts.
- Description: As a runtime adapter, I need a clear contract for text-first, planning, and multimodal image inputs so each execution receives only the attachment context appropriate to its target.

Acceptance criteria:
- Text-first runtimes receive an INPUT ATTACHMENTS block before WORKSPACE.
- The block references relevant workspace paths, manifest entries, and generated context paths.
- Step execution receives objective-scoped context and only the current step attachment context by default.
- Non-current step context is omitted unless explicitly requested by the runtime or planner.
- Task-level planning receives objective context and a compact inventory of step-scoped attachments without flattening later-step context.
- Multimodal adapters may consume raw refs directly without changing artifact refs, target bindings, manifest source of truth, or control-plane contract.
- Provider-specific multimodal message formats remain runtime-adapter concerns, not the control-plane contract.

Requirements:
- Place attachment context before WORKSPACE for text-first runtimes.
- Use prepared manifest and generated context paths as injection inputs.
- Preserve source artifact refs and target bindings across direct multimodal payload construction.

Scope:
- INPUT ATTACHMENTS prompt block for text-first runtimes
- Step-scoped context filtering rules
- Task-level planning attachment inventory
- Multimodal adapter direct-ref option
- Control-plane source-of-truth invariants across runtime modes

Out of scope:
- Vision generation implementation
- Create-page upload UX
- Provider-specific message format details

Owned source design coverage:
- `DESIGN-REQ-013`: Owns text-first prompt block placement, contents, and current-step filtering.
- `DESIGN-REQ-014`: Owns planning inventory and multimodal adapter direct-ref contract.
- `DESIGN-REQ-020`: Owns non-goals around implicit step sharing and provider-specific message formats as the control-plane contract.

### STORY-007: Preview and download task images by target

- Short name: `targeted-image-preview`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 11. UI preview and detail contract, 13. Edit and rerun durability contract
- Dependencies: STORY-002, STORY-003
- Independent test: Open task detail/edit/rerun for a task with objective and step attachments, force one preview failure, and assert grouping, metadata, download actions, and persisted/new file distinctions remain correct.
- Description: As a task reviewer, I need task detail, edit, and rerun surfaces to preview and download image inputs by their persisted target through MoonMind-owned APIs without losing metadata when previews fail.

Acceptance criteria:
- Previews and downloads use MoonMind-owned API endpoints.
- Preview and detail surfaces organize attachments by objective and step target.
- The UI never infers target binding from filenames.
- A preview failure does not remove metadata visibility or download actions.
- Edit and rerun surfaces distinguish persisted attachments from new local files not yet uploaded.
- Unchanged persisted refs remain available unless explicitly removed.

Requirements:
- Use authoritative snapshot target bindings for UI reconstruction.
- Keep download actions available through execution-owned APIs.
- Expose enough target-aware metadata for reviewers to understand attachment scope.

Scope:
- Task detail image preview and download surfaces
- Target grouping for objective and step images
- Filename-independent target binding display
- Preview-failure fallback to metadata and download actions
- Persisted-versus-new local file distinction in edit/rerun UI

Out of scope:
- Artifact upload validation
- Worker-side materialization
- Vision OCR/caption content

Owned source design coverage:
- `DESIGN-REQ-015`: Owns preview/download UI, target grouping, filename-independent binding, preview-failure fallback, and persisted/new file distinction.
- `DESIGN-REQ-018`: Owns edit/rerun UI preservation of unchanged refs and explicit removal/addition behavior.

### STORY-008: Protect image access and untrusted content boundaries

- Short name: `image-security-boundaries`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 12. Authorization and security contract, 15. Non-goals
- Dependencies: STORY-002
- Independent test: Use two users/executions and worker access paths to assert unauthorized preview/download is denied, browser responses do not expose long-lived credentials, worker access is execution-scoped, and extracted image text is not treated as instructions by default.
- Description: As a security-conscious operator, I need image preview, download, worker access, and extracted text handling to respect execution ownership, short-lived credentials, and untrusted-content boundaries.

Acceptance criteria:
- End-user preview/download are governed by execution ownership and view permissions.
- Browsers receive only short-lived presigned download URLs or MoonMind proxy responses, never long-lived object-store credentials.
- Worker-side access uses service credentials and execution authorization, not browser credentials.
- Extracted text from images is not trusted as executable instructions unless the authored task explicitly chooses to use it.
- Images remain untrusted user input.
- Direct browser access to object storage, Jira, or provider-specific file endpoints is not allowed.
- Hidden compatibility transforms must not silently rewrite attachment refs or retarget them to another step.
- Live Jira sync remains out of scope for the image input system.

Requirements:
- Apply execution-scoped authorization to all image byte access.
- Avoid exposing durable storage or provider credentials to the browser.
- Preserve attachment refs exactly rather than rewriting them through compatibility transforms.

Scope:
- Execution ownership and view-permission checks for preview/download
- Short-lived presigned URLs or MoonMind proxy responses
- Worker-side service credentials with execution authorization
- Untrusted extracted text and image input handling
- Prohibition of direct browser object/Jira/provider file access and hidden retargeting transforms

Out of scope:
- Attachment policy size/count validation
- Diagnostic event taxonomy
- Jira live sync implementation

Owned source design coverage:
- `DESIGN-REQ-016`: Owns execution-owned preview/download authorization and browser/worker credential separation.
- `DESIGN-REQ-017`: Owns untrusted image/text treatment, direct-access prohibitions, no scriptable images, and no hidden retargeting transforms.
- `DESIGN-REQ-020`: Owns live Jira sync and provider-specific file endpoint exclusions as non-goals.

### STORY-009: Expose image diagnostics and failure evidence

- Short name: `image-diagnostics-evidence`
- Source reference: `docs/Tasks/ImageSystem.md`
- Source sections: 14. Observability and diagnostics contract
- Dependencies: STORY-002, STORY-004, STORY-005
- Independent test: Trigger successful and failing upload, validation, prepare, and context-generation paths, then assert diagnostics include the expected event classes, artifact/context paths, target metadata, and affected step target for failures.
- Description: As an operator debugging image-input failures, I need target-aware events, manifest/context path discovery, and step-specific failure evidence without scraping raw workflow history heuristics.

Acceptance criteria:
- Events are emitted for attachment upload started/completed and attachment validation failed.
- Events are emitted for prepare download started/completed/failed.
- Events are emitted for image context generation started/completed/failed.
- Task diagnostics expose the attachment manifest path and generated context paths.
- Task detail and debugging surfaces expose target-aware attachment metadata.
- Step-level failures identify the affected step target.

Requirements:
- Make diagnostics sufficient to debug image failures without relying on raw workflow history heuristics.
- Connect diagnostic evidence to the same target bindings used by task execution.

Scope:
- Recommended event classes for upload, validation, prepare, and context generation
- Discoverability of attachment manifest and generated context paths from task diagnostics
- Target-aware attachment metadata on task detail/debugging surfaces
- Step target identification on step-level failures

Out of scope:
- Raw image preview rendering
- Vision model output quality
- Provider-specific telemetry beyond the image contract

Owned source design coverage:
- `DESIGN-REQ-019`: Owns image input observability, event taxonomy, diagnostic path discovery, target-aware metadata, and step-level failure evidence.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001, STORY-004
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001, STORY-004
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-001
- `DESIGN-REQ-007` -> STORY-003
- `DESIGN-REQ-008` -> STORY-002
- `DESIGN-REQ-009` -> STORY-002
- `DESIGN-REQ-010` -> STORY-002
- `DESIGN-REQ-011` -> STORY-004
- `DESIGN-REQ-012` -> STORY-005
- `DESIGN-REQ-013` -> STORY-006
- `DESIGN-REQ-014` -> STORY-006
- `DESIGN-REQ-015` -> STORY-003, STORY-007
- `DESIGN-REQ-016` -> STORY-008
- `DESIGN-REQ-017` -> STORY-002, STORY-008
- `DESIGN-REQ-018` -> STORY-003, STORY-007
- `DESIGN-REQ-019` -> STORY-009
- `DESIGN-REQ-020` -> STORY-001, STORY-006, STORY-008

## Dependencies Between Stories

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-001, STORY-002
- `STORY-005` depends on: STORY-004
- `STORY-006` depends on: STORY-004, STORY-005
- `STORY-007` depends on: STORY-002, STORY-003
- `STORY-008` depends on: STORY-002
- `STORY-009` depends on: STORY-002, STORY-004, STORY-005

## Out-of-Scope Items and Rationale

- Raw image bytes in execution create payloads are excluded because the design is artifact-first and keeps binaries out of Temporal histories.
- Images embedded into instruction markdown as data URLs are excluded because instructions should carry text and structured refs, not binary payloads.
- Implicit attachment sharing across steps is excluded because target binding is explicit and durable.
- Live Jira sync is excluded because this image system contract only preserves Jira-ready breakdown metadata and does not define external synchronization behavior.
- Generic non-image attachment types are excluded by default because the current desired-state policy authorizes image MIME types.
- Provider-specific multimodal message formats are excluded from the control-plane contract because they belong inside runtime adapters.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
