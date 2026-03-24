# Task Image Input System (Temporal Execution)

**Implementation tracking:** [`docs/tmp/remaining-work/Tasks-ImageSystem.md`](../tmp/remaining-work/Tasks-ImageSystem.md)

## Summary

This design defines how **image attachments** are processed within MoonMind tasks using the new Temporal-based execution and Artifact storage architecture.

The goals of this subsystem:
1. Allow users to **upload images** from the Mission Control UI.
2. Store the uploaded bytes securely in the **Artifact Store** (MinIO).
3. Pass lightweight, secure `ArtifactRef` pointers into the **Temporal Workflow** (`MoonMind.Run`).
4. Execute a deterministic **Vision Processing Activity** to extract useful text context (captions, OCR) for downstream LLM prompts.
5. Provide a deterministic path for sandbox activities to access raw image bytes when needed.

---

## Architecture Overview

In an artifact-centric, Temporal-orchestrated model, we never embed large binaries or multi-part uploads directly into job creation JSON or Temporal execution histories.

### High-Level Flow

```
Dashboard UI
  │
  ├─ 1) POST /artifacts (multipart or direct)
  │     └─ returns ArtifactRef(s)
  │
  ├─ 2) Upload bytes directly to Artifact Store (MinIO via presigned URL)
  │
  └─ 3) POST /api/queue/jobs (Payload includes initialParameters.inputArtifactRefs)
        │
        ▼
MoonMind.Run Workflow (Temporal)
  │
  ├─ 1) Initializes state with input ArtifactRefs.
  │
  ├─ 2) Schedules `vision.generate_context` Activity (mm.activity.llm queue):
  │     - Activity fetches image bytes from Artifact Store.
  │     - Activity calls Vision LLM (e.g., Gemini 1.5 Pro/Flash).
  │     - Activity writes `image_context.md` text back to Artifact Store.
  │     - Activity returns new ArtifactRef to the Workflow.
  │
  ├─ 3) Schedules `plan.generate` (if applicable) or `mm.skill.execute`:
  │     - The generated `image_context.md` Ref is passed to the LLM for reasoning.
  │
  └─ 4) Evaluates Sandboxed Activity (e.g., `sandbox.run_command`):
        - If the sandbox script needs the raw images, an `artifact.download_to_workspace`
          activity is scheduled to materialize the bytes into `repo/.moonmind/inputs/`.
```

---

## Data Model & Artifact Index

Instead of implicitly storing images as `AgentJobArtifact` records prefixed with `inputs/`, images are formalized through the unified **Artifact Index**.

* **Content-Type**: Must be `image/png`, `image/jpeg`, or `image/webp`.
* **Linkage**: The `artifact_links` table connects the image to the workflow execution.
  - `link_type`: `input.image`
  - `label`: Original filename (e.g., `screenshot.png`)
* **Retention Class**: Inherits the `standard` policy (e.g., 30 days) alongside other job inputs/outputs.
* **Integrity**: Enforced via `sha256` checksums validated upon completion of the upload to the Artifact Store.

---

## Authorization & Security

Security applies evenly across the Artifact API as established by the `WorkflowArtifactSystemDesign.md`.

* **End-User Access**: 
  - Regulated entirely by the Workflow Execution viewing permissions.
  - Generates short-lived (e.g., 15 minute) presigned URLs for UI preview and download.
  - No permanent direct access to the object store.
* **Worker Access**:
  - Activities operate using service credentials and least-privilege roles to fetch blobs from the Artifact Store.
  - They do not rely on passing active worker-claim tokens like the legacy architecture.
* **Content Safety**:
  - `image/svg+xml` files remain strictly forbidden to prevent script injection.
  - Minimum and maximum byte boundaries for chunks and total size are enforced by the Artifact API bounds.

---

## Workload Specific Behavior

### 1. Planning and Coding Skills (Text Only)

For capabilities acting as standard LLM prompts (such as `pr-resolver` or simple `codex exec` interactions):
* The raw bytes never enter the `sandbox` container logic.
* The Workflow invokes `vision.generate_context` in the `mm.activity.llm` fleet.
* The result is a text artifact (e.g., `image_context.md`) summarising the image content.
* `plan.generate` and text-centric tools append this image context strictly inside the system prompt:

```text
INPUT ATTACHMENTS:
[Provided text summary generated from the provided artifacts...]
```

### 2. Multi-modal Run-times

If MoonMind introduces a model execution path (like a direct `gemini` multimodal chat capability inside the sandbox or as a dedicated skill activity):
* The Temporal workflow passes the `ArtifactRef` into the multimodal context array.
* The specific activity fetching the Artifact blobs constructs a valid multimodal Provider Payload (base64 or signed platform URL) seamlessly without intermediary caching disk writes.

### 3. Sandbox Materialisation (Optional)

If a user specifically instructs: *"Crop this image using ImageMagick"*
* The skill must dictate a requirement for raw visual assets over text captions.
* The Temporal execution schedules the `artifact.download_to_workspace` activity to physically map the image `ArtifactRef`s into `.moonmind/inputs/<artifact_id>-<label>` inside the active Sandbox workspace before the shell command executes.
* Standard `.git/info/exclude` rules prevent accidental commits of these ephemeral visual assets.

---

## API Contract Summary

Legacy routes using `POST /api/queue/jobs/with-attachments` and trailing `/worker` suffixes are deprecated.

Instead, the client integrates directly with the standard REST endpoints:
1. `POST /artifacts`
2. upload bytes using pre-signed URL.
3. `POST /artifacts/{artifact_id}/complete`
4. Use standard `POST /api/queue/jobs`

UI rendering consumes:
* `GET /executions/{namespace}/{workflow_id}/{run_id}/artifacts?link_type=input.image`
* Followed by `POST /artifacts/{artifact_id}/presign-download` for UI thumbnails.

---

## Vision pipeline (target)

Images ingest through **`POST /artifacts`** with valid image MIME types and Temporal **`ArtifactRef`s** in workflow variables. **`vision.generate_context`** (or equivalent) produces text artifacts wired into **`mm.skill.execute`** preparation. Legacy **`with-attachments`** queue ingest is retired once Temporal paths cover all cases. Progress: [`docs/tmp/remaining-work/Tasks-ImageSystem.md`](../tmp/remaining-work/Tasks-ImageSystem.md).
