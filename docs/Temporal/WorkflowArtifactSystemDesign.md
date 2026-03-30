# Workflow Artifact System Design
Status: Draft
Owners: MoonMind Platform
Last updated: 2026-03-30

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-WorkflowArtifactSystemDesign.md`](../tmp/remaining-work/Temporal-WorkflowArtifactSystemDesign.md)

## Related Docs
- `docs/Tasks/AgentSkillSystem.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`

## 1. Context

MoonMind is being redesigned around **Temporal**.

- For **Temporal-managed flows**, **Workflow Executions** are the primary units of durable orchestration.
- **Activities** perform all side effects (network calls, filesystem, GitHub/Jules, etc.).
- Temporal history and payloads must remain **small** and **safe** (avoid large blobs, avoid leaking secrets).

Public MoonMind surfaces may still describe these executions as `tasks` during migration. This document defines the artifact contract at the Temporal/runtime layer.

Therefore, workflows and activities must pass **artifact references** (pointers) rather than large payloads. This ensures large immutable inputs like plans, manifests, patches, logs, **resolved skill snapshots, skill manifests, prompt indexes, and runtime materialization bundles** are safely carried outside workflow history. This document defines a complete artifact system: storage, identity, ACLs, APIs, and retention.

---

## 2. Goals

1. **Reference-based IO:** Workflows/activities exchange `ArtifactRef` values, not large bytes.
2. **Reproducible execution context:** Artifact-backed refs should capture large immutable inputs such as plans, manifests, and resolved skill snapshots so retries and reruns do not silently drift.
3. **Immutable artifacts:** Artifacts are write-once, read-many; “updates” create new artifacts.
4. **Secure access:** Strong authorization, short-lived access grants, auditability.
5. **Operational clarity:** Retention policies, lifecycle deletion, and predictable storage costs.
6. **Execution linkage:** First-class linkage between artifacts and a Temporal **Workflow Execution** (workflow_id + run_id), including “latest output”.

---

## 3. Non-goals

- Using the artifact system as a general-purpose database for small structured state that belongs in workflow state.
- Storing secrets in cleartext.
- Guaranteeing any ordering semantics resembling a user-visible queue.

---

## 4. Definitions

### 4.1 Artifact
An **Artifact** is an immutable blob (bytes) stored outside Temporal history, plus metadata needed to:
- fetch it securely,
- verify integrity,
- manage lifecycle,
- relate it to workflow executions.

### 4.2 ArtifactRef
An **ArtifactRef** is a small JSON-serializable reference passed in workflow inputs/outputs and activity parameters/results.

### 4.3 ExecutionRef
A minimal reference to a Temporal **Workflow Execution**:
- `namespace`
- `workflow_id`
- `run_id`

(Temporal terms; MoonMind does not introduce new umbrella nouns.)

### 4.4 ResolvedSkillSet Artifact
An artifact-backed immutable manifest describing the exact agent skills selected for a run or step.

### 4.5 Materialization Artifact
An artifact generated from a `ResolvedSkillSet` to support runtime delivery, such as a prompt index, rendered bundle, or compatibility manifest.

---

## 5. Architecture Overview

### 5.1 Components

1. **Artifact Store (blob storage)**
   - Primary storage for bytes.
   - **Default backend is MinIO (S3-compatible)** for MoonMind local/dev and default Docker Compose environments.
   - Non-MinIO object stores are explicit deployment overrides.

2. **Artifact Index (metadata + query)**
   - Stores artifact metadata and relationships to workflow executions.
   - Enables listing by execution, “latest output”, retention queries, etc.

3. **Artifact API**
   - Used by:
     - UI clients (upload/download via presigned URLs)
     - Worker processes (internal read/write)
     - Lifecycle management (deletion)

4. **Artifact Activities**
   - Activities used by workflows to read/write artifacts in a deterministic way (e.g. resolved skill snapshot writes, prompt-index creation, materialization bundle writes):
     - `artifact.create`
     - `artifact.read`
     - `artifact.write_complete`
     - `artifact.list_for_execution`
     - `artifact.compute_preview` (redaction-aware)

### 5.2 High-level flows

**Upload (client → store):**
1) Client asks Artifact API to create an upload
2) API returns presigned URL(s)
3) Client uploads bytes directly to object store
4) Client (or API) finalizes upload
5) Artifact becomes readable by reference (`ArtifactRef`)

**Read (activity worker → store):**
1) Activity receives `ArtifactRef`
2) Activity fetches bytes using internal credentials (or presigned download)
3) Activity returns small results (new `ArtifactRef`s, summaries, statuses) to workflow
   - Examples of read subjects: plan artifacts, manifest artifacts, resolved skill manifests, runtime delivery bundles.

Compatibility APIs may resolve those artifacts back into task-oriented detail payloads, but the artifact linkage remains execution-centric at the Temporal layer.

### 5.3 Temporal routing contract

Artifact operations are not "general worker" calls. They are a dedicated Activity family with a dedicated queue boundary.

- All `artifact.*` Activities, including `artifact.read`, must execute on the artifacts fleet and task queue: `mm.activity.artifacts`.
  - This absolutely encompasses skill-related artifact IO (e.g., reading a resolved skill snapshot manifest, writing a prompt index, or linking materialization bundles to an execution).
- Workflows must not hardcode `mm.activity.llm` or any other non-artifact queue when scheduling `artifact.*` Activities.
- Queue selection for artifact operations should come from the Temporal activity catalog / worker topology configuration, not duplicated queue constants inside workflow logic.

This matters most in mixed stages such as `MoonMind.Run` execution:

1. A workflow may call `plan.generate` on the LLM fleet.
2. Once a plan artifact exists, the workflow must call `artifact.read(plan_ref)` on the artifacts fleet to retrieve the plan bytes.
3. After the plan payload is decoded into node invocations, the workflow may schedule `mm.skill.execute` or other capability-routed Activities on their respective fleets.

The read step stays on the artifact boundary even when the artifact content is consumed by a later LLM or sandbox step. Artifact storage access is an IO concern, not an LLM concern.

Any implementation that routes `artifact.read` to an LLM queue violates the intended Temporal design, even if the receiving worker can otherwise continue execution.

---

## 6. Artifact store choice (MinIO default)

### 6.1 Options considered

| Option | Pros | Cons | Recommended use |
|---|---|---|---|
| S3-compatible (S3 / GCS / MinIO) | Large objects, cheap, durable, native multipart, encryption options | Requires object store infra; eventual consistency concerns | **Primary** blob store |
| Database (Postgres bytea/large objects) | Fewer moving parts; transactional | Expensive; poor for large blobs; backup/replication cost; risk to OLTP performance | Only for very small objects (discouraged) |
| Local filesystem | Simple in dev | Not durable, not scalable, poor for multi-node | Dev-only |

### 6.2 Decision

**MoonMind's main and default Artifact Store is MinIO (S3-compatible).**

- Default local/dev setup: **MinIO**
- Default Docker Compose setup: **MinIO service is provisioned and wired by default**
- Production override: AWS S3 (or another S3-compatible provider) only through explicit configuration override

**Use Postgres only as the Artifact Index** (metadata + relationships), not for blob bytes.

### 6.3 Docker Compose default requirement

The repository's default Docker Compose path must treat MinIO as the standard artifact backend:

- Bring up MinIO in the default compose stack (not as an optional add-on profile)
- Configure Artifact API and workers to use MinIO endpoints/bucket by default
- Treat external S3 configuration as an explicit override, not the baseline
- Keep MinIO reachable on the internal Docker network for API/worker services by default
- Use `AUTH_PROVIDER=disabled` by default for one-click deployment, and align artifact API behavior to that app-level mode (section 9.5)
- Treat authenticated app modes as explicit overrides from the one-click default

---

## 7. Artifact identity and addressing scheme

### 7.1 Artifact ID
Use a globally unique, opaque identifier:
- Format: `art_<ULID>` (sortable by time, URL-safe)
- Example: `art_01J9Z9F7QZK2K0YQ3B1T2N0R4P`

Rationale:
- Avoid coupling ID to storage backend
- Provide stable references independent of storage key layout

### 7.2 Storage key layout (object store path)
Object store keys should support:
- multi-tenant partitioning
- operational debugging
- lifecycle policies by prefix

Recommended key pattern:
```

<namespace>/artifacts/<yyyy>/<mm>/<dd>/<artifact_id>

```

Example:
```

moonmind/artifacts/2026/03/05/art_01J9Z9F7...

````

### 7.3 Content integrity
Store and validate:
- `sha256` digest (hex)
- `size_bytes`

Workers should validate digest/size after upload completion (server-side or worker-side).

### 7.4 Immutability
Artifacts are immutable after completion:
- No “overwrite”
- Any change creates a new artifact ID

---

## 8. Metadata model (Artifact Index)

> Stored in Postgres (or equivalent metadata store). This is not a legacy queue; it is a metadata index for blobs.

### 8.1 Tables (logical schema)

#### `artifacts`
- `artifact_id` (PK)
- `created_at`
- `created_by_principal` (user/service)
- `content_type`
- `size_bytes`
- `sha256`
- `storage_backend` (enum: s3)
- `storage_key` (object store key)
- `encryption` (enum: sse-kms | sse-s3 | none | envelope)
- `status` (enum: pending_upload | complete | failed | deleted)
- `retention_class` (enum: ephemeral | standard | long | pinned)
- `expires_at` (nullable; derived)
- `redaction_level` (enum: none | preview_only | restricted)
- `metadata` (jsonb; non-indexed extra info)

#### `artifact_links`
Relates artifacts to workflow executions and gives them meaning.
- `artifact_id` (FK)
- `namespace`
- `workflow_id`
- `run_id`
- `link_type` (enum; see below)
- `label` (string; optional, human-friendly)
- `created_at`
- `created_by_activity_type` (optional)
- `created_by_worker` (optional)

`link_type` examples (keep small and stable):
- `input.instructions`
- `input.manifest`
- `input.plan`
- `input.skill_snapshot`
- `input.prompt_index`
- `output.primary`
- `output.patch`
- `output.logs`
- `output.summary`
- `runtime.skill_materialization`
- `debug.skill_resolution_trace`
- `debug.trace`

#### `artifact_pins` (optional)
Explicit pinning beyond retention class.
- `artifact_id`
- `pinned_by_principal`
- `pinned_at`
- `reason`

### 8.2 “Latest output” rule
“Latest output” is a *query*, not mutable state:
- For a given `(namespace, workflow_id, run_id, link_type)` return the artifact with max(`created_at`).
- For “latest for a workflow across runs”, define the UI rule explicitly:
  - **Option A:** latest by `created_at` across all runs
  - **Option B:** only the latest run_id (if your UI groups by workflow_id)

Note: Not all artifact link types are intended as "outputs". Types such as `input.skill_snapshot` or `runtime.skill_materialization` represent immutable execution inputs or runtime-preparation contexts, not logs or user outputs.

This is intentionally a read-model behavior, not an execution primitive.

---

## 9. ACL and authorization model

### 9.1 Requirements
- A user should only access artifacts they are authorized to access.
- Presigned URLs must be short-lived and scoped.
- All accesses should be auditable.

### 9.2 Authorization policy (recommended baseline)
Authorization is derived from:
1) the caller’s identity (principal)
2) the artifact’s linkage to a workflow execution (ExecutionRef)
3) MoonMind’s authorization rules for viewing that workflow execution

**Policy:**
- Read/write artifact metadata requires the caller can “view” the linked workflow execution, OR the caller is the owner of the artifact, OR the caller has an explicit grant.
  - Resolved skill snapshots may typically be viewable with the linked execution, but raw skill materialization bundles or debug traces may need tighter access depending on content thresholds. Prompt indexes or previews are the naturally preferred UI-facing surfaces over raw bundles.
- Direct blob access (presigned URLs) is only issued if the metadata check passes.

### 9.3 Grants and service access
- Worker processes use service identity with least privileges.
- Integrations may use narrower roles (e.g., “Jules integration worker can only read/write artifacts linked to executions it is handling”).

### 9.4 Auditing
Log at minimum:
- principal
- artifact_id
- operation (create/presign-download/presign-upload/delete)
- execution linkage (if any)
- timestamp
- IP/user-agent (for user clients)

### 9.5 App auth mode integration (required)
Artifact API auth behavior must follow the same app-level auth mode used by MoonMind.

| App auth setting | Artifact API behavior | Intended environment |
|---|---|---|
| `AUTH_PROVIDER=disabled` (**default**) | **No end-user authentication required** for user-facing artifact metadata/presign endpoints. Requests are attributed to `DEFAULT_USER_ID` (or built-in local fallback) for audit consistency. | One-click local/dev deployment |
| `AUTH_PROVIDER=local` / external IdP modes | Require authenticated user identity and enforce execution-linked authorization policy from section 9.2. | Shared dev/staging/prod |

Notes:
- This is an API-layer auth choice; it does not require anonymous/public MinIO buckets.
- Worker-internal artifact operations continue using service credentials and least-privilege roles.
- Worker-only mutation endpoints (claim/heartbeat/upload completion internals) still require worker identity/token.

### 9.6 Local no-auth profile with MinIO
For local deployment where app auth is disabled:

- Use MinIO as default artifact backend with local/internal credentials.
- Allow unauthenticated app clients at the Artifact API layer (as defined above), not by exposing public object storage.
- Keep presigned URL issuance enabled and short-lived; API still controls access shape even in no-auth local mode.
- This profile is the default for one-click deployment.

---

## 10. Size limits and chunking strategy

### 10.1 Limits
- Temporal payloads: only `ArtifactRef` and small JSON (no blobs).
- Artifact API “direct upload” (if supported): **max 10 MB** (configurable).
- Above that, require presigned multipart upload.

### 10.2 Multipart upload (S3-compatible)
- Minimum part size: 5 MB (S3 constraint); recommend **8–32 MB** for efficiency.
- API returns:
  - upload_id
  - part URLs (or “sign part” endpoint)
  - required headers and constraints

### 10.3 Streaming
- Downloads should be streamable (no forced buffering in API).
- Workers should stream artifacts to disk / pipe to tools instead of loading into memory.

---

## 11. Relationship to workflow executions

### 11.1 Linking
Every artifact *may* be linked to an ExecutionRef using `artifact_links`.
- Most execution-specific agent-skill artifacts **should** be linked to an execution (e.g., the resolved skill snapshot, prompt index, and runtime materialization bundle used by a run). 
- Cross-execution deployment-shared skill artifacts may remain unlinked or use broader sharing rules.

### 11.2 Naming and indexing conventions
Use `link_type` for stable machine meaning and `label` for UI clarity:
- `link_type=output.primary`, `label="Final output"`
- `link_type=input.manifest`, `label="Manifest v1"`

### 11.3 Versioning
Artifacts are immutable; versioning is implicit:
- Multiple artifacts can share the same `link_type` for a single execution
- “Latest” is determined by query rule (section 8.2)
- If you need explicit version numbers, store them in `metadata.version` or `label`.

---

## 12. Security

### 12.1 Encryption at rest
- Production: prefer SSE-KMS (or equivalent KMS-managed keys).
- Dev: SSE-S3 (or unencrypted if strictly local; discouraged).

### 12.2 Presigned URLs
- TTL default: **15 minutes** (configurable)
- Scope:
  - method-restricted (GET for download, PUT/POST for upload)
  - key-restricted (single artifact key)
  - content-type constrained (when possible)
  - content-length range constrained (when possible)
- Never store presigned URLs in workflow state or artifacts; they are ephemeral.

### 12.3 Redaction strategy
Artifacts may contain:
- secrets (tokens, keys)
- PII
- sensitive customer data
- skill-related datasets demanding content previews or redaction-aware layouts (e.g., prompt indexes, skill resolution traces, and runtime materialization manifests)

Design:
1) Store the **raw artifact** as produced.
2) Generate a **redacted preview artifact** for UI display when needed:
   - Activity: `artifact.compute_preview(artifact_ref, policy)` → returns `ArtifactRef` for preview
3) UI defaults to preview; raw download may require elevated permission (`redaction_level=restricted`).

### 12.4 Content scanning (optional, recommended)
- Malware scanning for uploaded files (depending on environment)
- Secret scanning for outputs destined for UI

---

## 13. Retention policy and lifecycle management

### 13.1 Retention classes (baseline)
- `ephemeral`: 1–7 days (logs, debug traces)
- `standard`: 30 days (most inputs/outputs)
- `long`: 180 days (important outputs, manifests if needed)
- `pinned`: no automatic deletion

### 13.2 Default mapping by link_type (example)
- `output.logs`, `debug.trace`, `debug.skill_resolution_trace` → ephemeral
- `input.instructions`, `input.plan`, `input.manifest`, `input.prompt_index` → standard (or long for manifests if required)
- `output.primary`, `output.patch`, `output.summary`, `input.skill_snapshot` → standard or long
- `runtime.skill_materialization` → standard or ephemeral depending on reproducibility needs
- Any artifact explicitly pinned → pinned

### 13.3 Lifecycle manager
A lifecycle manager periodically:
1) queries `artifacts` where `expires_at <= now()` and not pinned
2) deletes blob from object store
3) marks metadata `deleted`
4) deletes `artifact_links` (or keeps a tombstone for audit)

Implementation note:
- This periodic work can be initiated by a Temporal **Schedule** that starts a cleanup workflow execution.
- Deletion should be **idempotent** (safe to retry).

### 13.4 Soft-delete vs hard-delete
- Soft-delete first (mark deleted; deny new presigns)
- Hard-delete later (physical removal), possibly delayed to handle eventual consistency

---

## 14. Artifact API contract (REST)

> The API is split into **metadata operations** (authorized, auditable) and **blob transfer** (presigned URLs).

### 14.1 Types

#### `ArtifactRef` (v1)
```json
{
  "artifact_ref_v": 1,
  "artifact_id": "art_01J9Z9F7QZK2K0YQ3B1T2N0R4P",
  "sha256": "9b74c9897bac770ffc029102a200c5de...",
  "size_bytes": 123456,
  "content_type": "application/json",
  "encryption": "sse-kms"
}
````

### 14.2 Endpoints

#### Create upload (single or multipart)

`POST /artifacts`
Request:

* `content_type`
* `size_bytes` (optional but recommended)
* `sha256` (optional; recommended for integrity)
* `retention_class` (optional; default derived)
* `link` (optional):

  * `namespace`, `workflow_id`, `run_id`, `link_type` (e.g., `input.skill_snapshot`), `label`

Response:

* `artifact_ref`
* `upload`:

  * mode: `single_put` | `multipart`
  * presigned URL(s) + constraints
  * upload_id (multipart)

#### Complete multipart upload

`POST /artifacts/{artifact_id}/complete`
Request:

* parts list (part_number, etag)
  Response:
* `artifact_ref` (complete)

#### Get metadata

`GET /artifacts/{artifact_id}`
Response:

* metadata (including links)
* optionally a `download` presign (if `?include_download=true`)

#### Presign download

`POST /artifacts/{artifact_id}/presign-download`
Response:

* presigned download URL + expiration

#### Presign upload part (optional alternative)

`POST /artifacts/{artifact_id}/presign-upload-part`
Request: `part_number`
Response: presigned URL for that part

#### Link artifact to execution (if not linked at create)

`POST /artifacts/{artifact_id}/links`
Request:

* `namespace`, `workflow_id`, `run_id`, `link_type`, `label`

#### List artifacts for an execution

`GET /executions/{namespace}/{workflow_id}/{run_id}/artifacts?link_type=input.skill_snapshot`
Response:

* list of artifact metadata + refs

#### Pin/unpin

`POST /artifacts/{artifact_id}/pin`
`DELETE /artifacts/{artifact_id}/pin`

#### Delete (manual)

`DELETE /artifacts/{artifact_id}`
Notes:

* should behave like soft-delete
* must be idempotent

### 14.3 Presign constraints

Presign responses should include:

* `expires_at`
* allowed headers (content-type, content-md5 optional)
* max size allowed
* checksum requirements if supported

---

## 15. Integration requirements for workflows and activities

### 15.1 Workflow rule

Workflows must only store and pass:

* `ArtifactRef`
* compact metadata or selectors for skill-related execution context

Workflows must never:

* embed bytes for large files
* embed full agent skill bodies, manifests, prompt indexes, or runtime bundles (these must remain safely stored inside the artifact system)
* embed presigned URLs

### 15.2 Activity rule

All artifact IO happens in activities:

* read bytes (including reading skill-related artifacts)
* write bytes (including generating skill-related artifacts)
* generate previews
* link artifacts to execution

---

## 16. Contract completion

The artifact platform should expose a **stable HTTP contract** (create / presign / complete / get / list / link / pin / delete) with auth, audit, and multipart rules; a **versioned `ArtifactRef` JSON schema** (artifact_id, sha256, size_bytes, content_type; no presigned URLs in workflow state); and **retention classes** with per-`link_type` defaults (including skill snapshot and materialization classes, with coordinated preview/redaction behaviors) alongside an idempotent lifecycle manager. Delivery status and gaps are tracked in [`docs/tmp/remaining-work/Temporal-WorkflowArtifactSystemDesign.md`](../tmp/remaining-work/Temporal-WorkflowArtifactSystemDesign.md).

---

## 17. Open questions

1. Should deployment-shared skill artifacts use execution linkage only when materialized for a run, or do we also need a first-class cross-execution/shared artifact authorization model for managed skill catalogs?
2. What is the desired default retention for manifests and plans in regulated environments?
3. Do we require legal hold / WORM retention modes?
4. Should we implement server-side checksum enforcement for all uploads, or allow “best effort” for very large multipart uploads?
