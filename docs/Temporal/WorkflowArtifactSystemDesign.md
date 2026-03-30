# Workflow Artifact System Design

Status: Draft  
Owners: MoonMind Platform  
Last updated: 2026-03-30

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-WorkflowArtifactSystemDesign.md`](../tmp/remaining-work/Temporal-WorkflowArtifactSystemDesign.md)

## Related docs

- `docs/Tasks/AgentSkillSystem.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/TemporalArchitecture.md`

---

## 1. Context

MoonMind is built around **Temporal** as the durable orchestration substrate.

- **Workflow Executions** are the primary durable orchestration primitive.
- **Activities** perform all side effects.
- Temporal history and payloads must remain **small**, **bounded**, and **safe**.

Public MoonMind surfaces may still describe these executions as `tasks` during migration. This document defines the artifact contract at the Temporal/runtime layer.

Therefore, workflows and activities must pass **artifact references** rather than large payloads. This ensures large immutable inputs and outputs such as:

- plans
- manifests
- diffs and patches
- logs
- resolved skill snapshots
- prompt indexes
- runtime materialization bundles
- provider result snapshots
- managed runtime diagnostics

are safely stored outside workflow history.

This document defines the artifact system: storage, identity, linkage, authorization, API posture, retention, and activity-boundary usage.

---

## 2. Goals

1. **Reference-based IO**  
   Workflows and activities exchange `ArtifactRef` values, not large byte blobs.

2. **Reproducible execution context**  
   Artifact-backed refs capture immutable inputs so retries and reruns do not silently drift.

3. **Immutable artifacts**  
   Artifacts are write-once, read-many. Any “update” creates a new artifact.

4. **Secure access**  
   Strong authorization, short-lived grants, and auditable access.

5. **Operational clarity**  
   Retention classes, lifecycle deletion, and predictable storage behavior.

6. **Execution linkage**  
   First-class linkage between artifacts and Temporal executions.

7. **Canonical runtime result discipline**  
   True agent-runtime activities return compact canonical contracts, while large outputs and diagnostics live in artifacts referenced by those contracts.

---

## 3. Non-goals

- using the artifact system as a general-purpose database for small workflow state
- storing secrets in cleartext
- embedding provider-native large payloads into workflow history
- treating artifact storage as a user-visible queue or ordering primitive

---

## 4. Definitions

## 4.1 Artifact

An **Artifact** is an immutable blob stored outside Temporal history, plus metadata needed to:

- fetch it securely
- verify integrity
- manage lifecycle
- relate it to workflow executions

## 4.2 ArtifactRef

An **ArtifactRef** is a small JSON-serializable reference passed in workflow inputs, outputs, activity parameters, and activity results.

## 4.3 ExecutionRef

A minimal reference to a Temporal workflow execution:

- `namespace`
- `workflow_id`
- `run_id`

## 4.4 ResolvedSkillSet artifact

An artifact-backed immutable manifest describing the exact agent skills selected for a run or step.

## 4.5 Materialization artifact

An artifact generated from a `ResolvedSkillSet` to support runtime delivery, such as:

- prompt index
- rendered bundle
- compatibility manifest
- runtime-facing materialization snapshot

## 4.6 Diagnostics artifact

An artifact containing large operational details that must not be placed in workflow history or memo, for example:

- terminal logs
- provider snapshots
- managed runtime stderr/stdout bundles
- execution traces
- failure summaries too large for memo

---

## 5. Architecture overview

## 5.1 Components

1. **Artifact Store (blob storage)**
   - primary storage for bytes
   - default backend is MinIO (S3-compatible) for local/dev and default Compose environments

2. **Artifact Index**
   - stores artifact metadata and execution relationships
   - enables listing by execution, latest-output queries, retention queries, and access checks

3. **Artifact API**
   - used by UI clients for upload/download flows
   - used by workers for internal read/write flows
   - used by lifecycle management and cleanup

4. **Artifact Activities**
   - deterministic workflow-facing activity family for artifact IO, including:
     - `artifact.create`
     - `artifact.read`
     - `artifact.write_complete`
     - `artifact.list_for_execution`
     - `artifact.compute_preview`
     - `artifact.link`
     - `artifact.pin`
     - `artifact.unpin`
     - `artifact.lifecycle_sweep`

## 5.2 High-level flows

### Upload (client → store)

1. Client asks the Artifact API to create an upload
2. API returns presigned URL(s) or multipart instructions
3. Client uploads bytes directly to object storage
4. Client or API finalizes the upload
5. Artifact becomes readable by `ArtifactRef`

### Read (activity worker → store)

1. Activity receives `ArtifactRef`
2. Activity fetches bytes using internal credentials
3. Activity returns compact results to workflow code

Examples of read subjects:

- plan artifacts
- manifest artifacts
- resolved skill manifests
- runtime delivery bundles
- provider result snapshots
- managed runtime diagnostics bundles

### Result production (activity/runtime → artifact system)

When a true agent-runtime activity finishes:

- the large payloads stay in artifact storage
- the activity returns a compact canonical contract such as `AgentRunResult`
- that result contains refs like:
  - `output_refs[]`
  - `diagnostics_ref`

This is a core architecture rule: **large execution outputs belong in artifacts, not runtime payloads or workflow history**.

## 5.3 Temporal routing contract

Artifact operations are a dedicated activity family with a dedicated routing boundary.

All `artifact.*` Activities must execute on:

- fleet: `artifacts`
- queue: `mm.activity.artifacts`

This includes skill-related artifact IO such as:

- reading a resolved skill snapshot
- writing a prompt index
- linking runtime materialization bundles
- storing provider result snapshots
- storing managed runtime logs and diagnostics

Workflows must not hardcode a non-artifact queue when scheduling `artifact.*` Activities.

Queue selection for artifact operations should come from the activity catalog, not duplicated queue constants inside workflow logic.

---

## 6. Artifact store choice (MinIO default)

## 6.1 Options considered

| Option | Pros | Cons | Recommended use |
|---|---|---|---|
| S3-compatible storage (S3 / GCS-compatible / MinIO) | scalable, durable, multipart-friendly, cheap for blobs | requires object storage infra | **primary blob store** |
| Database blobs | fewer moving parts | poor for large objects, expensive, hurts OLTP characteristics | discouraged |
| Local filesystem | simple for dev | poor durability, poor scale, bad multi-node story | dev-only fallback |

## 6.2 Decision

**MoonMind’s default artifact store is MinIO (S3-compatible).**

- local/dev default: **MinIO**
- default Docker Compose backend: **MinIO**
- production override: AWS S3 or another compatible provider only by explicit configuration

Postgres is used as the **Artifact Index**, not the primary blob store.

## 6.3 Docker Compose default requirement

The default Compose path must treat MinIO as the standard artifact backend:

- MinIO is provisioned by default
- API and workers use MinIO endpoints/buckets by default
- external S3 is an explicit override
- MinIO stays on the internal Docker network by default

---

## 7. Artifact identity and addressing

## 7.1 Artifact ID

Use a globally unique, opaque identifier:

- format: `art_<ULID>`

Example:

- `art_01J9Z9F7QZK2K0YQ3B1T2N0R4P`

Rationale:

- independent of storage backend
- sortable
- stable
- safe to reference in workflow payloads

## 7.2 Storage key layout

Recommended object-store key pattern:

```text
<namespace>/artifacts/<yyyy>/<mm>/<dd>/<artifact_id>
```

Example:

```text
moonmind/artifacts/2026/03/30/art_01J9Z9F7QZK2K0YQ3B1T2N0R4P
```

## 7.3 Integrity

Store and validate:

* `sha256`
* `size_bytes`

`write_complete` or equivalent finalization should verify integrity.

## 7.4 Immutability

Artifacts are immutable after completion:

* no overwrite
* no in-place mutation
* any content change creates a new artifact ID

---

## 8. Metadata model (Artifact Index)

Stored in Postgres or equivalent metadata storage.

## 8.1 Logical tables

### `artifacts`

* `artifact_id` (PK)
* `created_at`
* `created_by_principal`
* `content_type`
* `size_bytes`
* `sha256`
* `storage_backend`
* `storage_key`
* `encryption`
* `status` (`pending_upload | complete | failed | deleted`)
* `retention_class`
* `expires_at`
* `redaction_level`
* `metadata` (jsonb)

### `artifact_links`

Relates artifacts to workflow executions and gives them meaning.

* `artifact_id`
* `namespace`
* `workflow_id`
* `run_id`
* `link_type`
* `label`
* `created_at`
* `created_by_activity_type`
* `created_by_worker`

Representative stable `link_type` values:

* `input.instructions`
* `input.manifest`
* `input.plan`
* `input.skill_snapshot`
* `input.prompt_index`
* `output.primary`
* `output.patch`
* `output.logs`
* `output.summary`
* `output.provider_snapshot`
* `output.agent_result`
* `runtime.skill_materialization`
* `runtime.stdout`
* `runtime.stderr`
* `runtime.merged_logs`
* `runtime.diagnostics`
* `debug.skill_resolution_trace`
* `debug.trace`

### `artifact_pins`

Optional explicit pinning beyond retention class.

* `artifact_id`
* `pinned_by_principal`
* `pinned_at`
* `reason`

## 8.2 “Latest output” rule

“Latest output” is a query behavior, not mutable execution state.

Representative rules:

* for a given `(namespace, workflow_id, run_id, link_type)`, return max by `created_at`
* for a workflow across runs, define UI behavior explicitly:

  * latest by `created_at`, or
  * latest run only

Not all link types are intended as “outputs.”
For example:

* `input.skill_snapshot` is an immutable input
* `runtime.skill_materialization` is a runtime-preparation artifact
* `runtime.diagnostics` is operational detail

---

## 9. Authorization and ACL model

## 9.1 Requirements

* callers must only access artifacts they are authorized to access
* presigned URLs must be short-lived and scoped
* access should be auditable

## 9.2 Authorization baseline

Authorization is derived from:

1. caller identity
2. artifact linkage to an execution
3. MoonMind authorization rules for viewing that execution
4. optional explicit grants or service-role rules

Baseline policy:

* read/write metadata requires that the caller can view the linked execution, or owns the artifact, or has an explicit grant
* blob access is issued only after metadata-level authorization succeeds

## 9.3 Service and worker access

Workers use service identity with least privilege.

Representative examples:

* artifact workers may read/write artifact storage broadly within policy
* integration workers may write provider snapshots and read linked execution artifacts
* managed runtime workers may write stdout/stderr/diagnostics artifacts for runs they are handling

## 9.4 Auditing

Log at minimum:

* principal
* artifact ID
* operation
* execution linkage
* timestamp
* IP/user-agent for user-facing API calls where applicable

## 9.5 App auth mode integration

Artifact API auth behavior must follow the app-level auth mode.

| App auth setting         | Artifact API behavior                                                | Intended environment    |
| ------------------------ | -------------------------------------------------------------------- | ----------------------- |
| `AUTH_PROVIDER=disabled` | no end-user auth required for user-facing metadata/presign endpoints | one-click local/dev     |
| authenticated modes      | require authenticated identity and execution-linked authorization    | shared dev/staging/prod |

This is an API-layer auth choice. It does **not** require public object-storage buckets.

---

## 10. Size limits and transfer strategy

## 10.1 Limits

* Temporal payloads: only `ArtifactRef` and compact JSON
* direct upload API path: modest configurable size limit
* larger uploads: presigned multipart upload

## 10.2 Multipart upload

For S3-compatible backends:

* minimum part size follows backend constraints
* API should return multipart upload instructions, IDs, and required headers

## 10.3 Streaming

* downloads should be streamable
* workers should stream large artifacts rather than loading everything into memory
* managed runtime log processing should support streamed or incremental artifact handling where practical

---

## 11. Relationship to workflow executions

## 11.1 Linking rule

Artifacts may be linked to an `ExecutionRef`.

Most execution-specific artifacts **should** be linked, especially:

* input instructions
* plans
* manifests
* skill snapshots
* prompt indexes
* provider result snapshots
* runtime logs
* diagnostics bundles
* final outputs

Cross-execution shared artifacts may remain unlinked or follow broader authorization rules.

## 11.2 Naming and indexing conventions

Use:

* `link_type` for stable machine meaning
* `label` for UI-friendly presentation

Examples:

* `link_type=output.primary`, `label="Final output"`
* `link_type=input.manifest`, `label="Manifest"`
* `link_type=runtime.diagnostics`, `label="Managed runtime diagnostics"`

## 11.3 Versioning

Artifacts are immutable; versioning is implicit.

* multiple artifacts can share the same `link_type`
* “latest” is a query concern
* explicit version numbers can live in metadata if needed

---

## 12. Canonical runtime result integration

This section locks the relationship between artifacts and true agent-runtime contracts.

## 12.1 Rule

True agent-runtime activities must return compact canonical contracts. Large outputs stay in artifacts.

Representative contracts:

* `AgentRunHandle`
* `AgentRunStatus`
* `AgentRunResult`

## 12.2 Result artifact expectations

`AgentRunResult` should normally carry artifact refs for large outputs, for example:

* `output_refs[]`
* `diagnostics_ref`

This means:

* provider raw payload dumps belong in artifacts
* managed runtime logs belong in artifacts
* large summaries or diffs belong in artifacts
* workflow history should only see small summaries and refs

## 12.3 Examples

### External provider result

An external provider activity may:

* write final provider snapshot as `output.provider_snapshot`
* write extracted human-facing output as `output.primary`
* write diagnostics as `runtime.diagnostics`

and then return a compact `AgentRunResult` referencing those artifacts.

### Managed runtime result

A managed runtime flow may:

* write stdout to `runtime.stdout`
* write stderr to `runtime.stderr`
* write merged logs to `runtime.merged_logs`
* write diagnostics to `runtime.diagnostics`
* write final user-facing output to `output.primary`

and then return a compact `AgentRunResult`.

---

## 13. Security

## 13.1 Encryption at rest

* production: prefer KMS-managed encryption
* dev: simpler storage encryption posture is acceptable if strictly local

## 13.2 Presigned URLs

Default posture:

* short TTL
* method-restricted
* key-restricted
* constrained by content-type/size where possible

Never store presigned URLs in workflow state or artifacts.

## 13.3 Redaction strategy

Artifacts may contain:

* secrets
* PII
* customer-sensitive data
* provider operational payloads
* skill-related content requiring preview/redaction

Strategy:

1. store the raw artifact
2. optionally generate a redacted preview artifact
3. default UI to preview when policy requires it
4. restrict raw access when sensitivity demands it

Representative activity:

* `artifact.compute_preview(artifact_ref, policy)`

## 13.4 Content scanning

Optional but recommended where appropriate:

* malware scanning
* secret scanning
* policy scanning before broad user-facing preview exposure

---

## 14. Retention policy and lifecycle management

## 14.1 Baseline retention classes

* `ephemeral`: short-lived operational/debug data
* `standard`: default inputs/outputs
* `long`: important outputs or manifests
* `pinned`: no automatic deletion

## 14.2 Default mapping examples

Representative mappings:

* `output.logs`, `debug.trace`, `debug.skill_resolution_trace` → ephemeral
* `runtime.stdout`, `runtime.stderr`, `runtime.merged_logs`, `runtime.diagnostics` → ephemeral or standard depending on policy
* `input.instructions`, `input.plan`, `input.manifest`, `input.prompt_index` → standard or long
* `output.primary`, `output.patch`, `output.summary`, `input.skill_snapshot` → standard or long
* `output.provider_snapshot` → standard or ephemeral depending on operational needs
* `runtime.skill_materialization` → standard or ephemeral depending on reproducibility goals
* explicitly pinned artifacts → pinned

## 14.3 Lifecycle manager

A lifecycle manager periodically:

1. finds expired, unpinned artifacts
2. deletes the blob or marks it for deletion
3. updates metadata state
4. preserves tombstones where audit/history policy requires them

This periodic work may be triggered by a Temporal Schedule that starts a cleanup workflow.

Deletion must be idempotent.

## 14.4 Soft-delete vs hard-delete

Preferred pattern:

* soft-delete first
* deny new presigns after soft-delete
* hard-delete later

---

## 15. Artifact API contract (REST)

The API is split into:

* metadata operations
* blob transfer operations via presigned URLs

## 15.1 `ArtifactRef` (v1)

```json
{
  "artifact_ref_v": 1,
  "artifact_id": "art_01J9Z9F7QZK2K0YQ3B1T2N0R4P",
  "sha256": "9b74c9897bac770ffc029102a200c5de...",
  "size_bytes": 123456,
  "content_type": "application/json",
  "encryption": "sse-kms"
}
```

## 15.2 Representative endpoints

### Create upload

`POST /artifacts`

Request may include:

* `content_type`
* `size_bytes`
* `sha256`
* `retention_class`
* optional execution link info:

  * `namespace`
  * `workflow_id`
  * `run_id`
  * `link_type`
  * `label`

Response:

* `artifact_ref`
* upload instructions:

  * single or multipart mode
  * presigned URL(s)
  * upload ID if multipart

### Complete multipart upload

`POST /artifacts/{artifact_id}/complete`

Response:

* completed `artifact_ref`

### Get metadata

`GET /artifacts/{artifact_id}`

### Presign download

`POST /artifacts/{artifact_id}/presign-download`

### Link artifact to execution

`POST /artifacts/{artifact_id}/links`

### List artifacts for an execution

`GET /executions/{namespace}/{workflow_id}/{run_id}/artifacts?...`

### Pin / unpin

* `POST /artifacts/{artifact_id}/pin`
* `DELETE /artifacts/{artifact_id}/pin`

### Delete

`DELETE /artifacts/{artifact_id}`

Should behave like soft-delete and be idempotent.

## 15.3 Presign constraints

Presign responses should include:

* expiry
* allowed headers
* size/checksum constraints where supported

---

## 16. Integration requirements for workflows and activities

## 16.1 Workflow rule

Workflows must only store and pass:

* `ArtifactRef`
* compact metadata
* stable selectors
* compact canonical runtime contracts

Workflows must never:

* embed large bytes
* embed full skill bodies
* embed raw provider snapshots
* embed long logs
* embed presigned URLs

## 16.2 Activity rule

All artifact IO happens in activities:

* read bytes
* write bytes
* generate previews
* link artifacts
* manage retention-related mutation
* produce result and diagnostics artifacts for true agent runs

## 16.3 Provider/runtime rule

Provider-facing and managed-runtime-facing activities should treat artifacts as the durable storage layer for large operational outputs.

They should not expose provider-shaped or runtime-shaped large payloads directly to workflows when a ref-based contract is more appropriate.

---

## 17. Contract completion

The artifact platform should expose:

* a stable HTTP contract for create/presign/complete/get/list/link/pin/delete
* a stable versioned `ArtifactRef` schema
* retention classes with link-type-based defaults
* preview/redaction support
* idempotent lifecycle cleanup
* execution linkage
* clear integration with canonical runtime result contracts

Large execution outputs, diagnostics, and runtime/provider payloads should be artifact-backed by default rather than carried through workflow payloads.

---

## 18. Open questions

1. Should deployment-shared skill artifacts remain mostly unlinked until run materialization time, or do we need a broader shared-artifact authorization model?
2. What should the default retention be for plans and manifests in stricter environments?
3. Do we need legal-hold or WORM retention modes?
4. Should checksum enforcement be strict for every upload mode?
5. How aggressive should the default retention be for provider snapshots and managed-runtime diagnostics?
