# Phase 0 Research – Tasks Image Attachments

## Decision 1: Store attachments as `AgentJobArtifact` rows under the reserved `inputs/` namespace
**Decision**: Keep using the existing `agent_job_artifacts` table with prefix-enforced names (`inputs/<attachmentUuid>/<sanitized_filename>`) and file blobs under `var/artifacts/agent_jobs/<jobId>/inputs/...`.
**Rationale**: `AgentQueueArtifactStorage` already handles deduplicated disk layout, digest computation, and retention jobs. Reusing it means attachment lifecycle, ACL checks, and cleanup leverage hardened code that is already wired into queue telemetry/events.
**Alternatives considered**: (a) Adding a dedicated attachments table/filesystem root—rejected because it duplicates retention and auditing work; (b) stream uploads straight to S3/object storage—rejected for Phase 1 because it would introduce a second storage dependency plus migration tooling.

## Decision 2: Expose dedicated list/download endpoints scoped to attachments
**Decision**: Surface `GET /api/queue/jobs/{jobId}/attachments` plus `/download` variants for users and mirror worker-scoped endpoints (`.../worker`) that require an active claim and worker token auth.
**Rationale**: Attachment ACLs differ from generic artifacts (only job owner or claiming worker may read; uploads forbidden). Dedicated endpoints let AgentQueueService reuse `_list_input_artifacts` and `_assert_job_worker_ownership` without risking accidental exposure via the broader `/artifacts` APIs.
**Alternatives considered**: (a) Reuse `/artifacts` with client-side filtering—rejected because it would leak metadata for other artifact namespaces; (b) Add signed URLs—rejected for MVP because streaming directly from FastAPI keeps the code symmetric with existing artifact downloads.

## Decision 3: Download + manifest inside worker prepare stage before any runtime executes
**Decision**: Extend `QueueApiClient` with `list_attachments_worker`/`download_attachment_worker`, call them inside `_run_prepare_stage`, store binaries under `repo/.moonmind/inputs/<uuid>-<filename>`, emit `.moonmind/attachments_manifest.json`, update `artifacts/task_context.json`, and raise if an attachment digest mismatch occurs.
**Rationale**: Downloading during prepare guarantees that by the time the runtime command builds prompts or clones repositories, every attachment is locally accessible and recorded for auditing. Tying manifest generation to the same stage ensures publish artifacts and worker logs prove what the runtime saw.
**Alternatives considered**: (a) Download lazily per step—rejected because steps run inside runtime-specific shells where fetching via API is harder to audit; (b) Keep attachments outside the repo tree—rejected because the runtime instructions need stable relative paths and `.moonmind` is already excluded from git status.

## Decision 4: Introduce `moonmind/vision` module with Gemini-first provider defaults
**Decision**: Create a reusable `moonmind/vision` package that wraps caption/OCR providers behind settings (`MOONMIND_VISION_*`), defaults to Gemini `models/gemini-2.5-flash`, supports a hard enable/disable flag, and returns a deterministic Markdown template for `.moonmind/vision/image_context.md`.
**Rationale**: Workers for Codex/Gemini/Claude all need identical text context, so a shared module prevents prompt drift and centralizes provider switches (Gemini → OpenAI/Anthropic) without editing every worker. The Markdown output doubles as an artifact for operators.
**Alternatives considered**: (a) Inline the provider logic inside Codex worker—rejected because Gemini/Gemini CLI runtimes and future workers would diverge; (b) Skip OCR/captions until Phase 2—rejected because DOC-REQ-007 demands a toggleable path even if OCR is optional.

## Decision 5: Prompt injection block precedes workspace instructions across runtimes
**Decision**: Update `_compose_step_instruction_for_runtime` (and non-Codex prompt builders) to prepend an `INPUT ATTACHMENTS` section summarizing manifest path, `.moonmind/inputs` layout, and the rendered `image_context.md` (or a notice if disabled).
**Rationale**: Placing the block before workspace instructions satisfies DOC-REQ-006 and guarantees every runtime—whether CLI prompts or API-based adapters—receives the same deterministic input before touching workspace steps.
**Alternatives considered**: (a) Append attachments at the end—rejected because long instructions risk truncation by runtimes; (b) Provide only a link to the manifest—rejected because operators need the textual context captured in logs and `task_context.json` for post-mortems.
