# Workflow Artifact System Design

**Document Class:** Canonical declarative
Status: Draft 
Owners: MoonMind Platform 
Last updated: 2026-10-06

## Related docs

- `docs/Steps/SkillSystem.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/TemporalArchitecture.md`

---

## 1. Context

Temporal owns durable orchestration; Activities perform side effects. Compact
artifact references keep large immutable plans, manifests, patches, logs, Skill
snapshots, prompts, runtime bundles, provider results and diagnostics outside
workflow history. Legacy task-named public fields do not change this runtime
contract, which covers storage, identity, linkage, authorization, APIs, retention
and Activity boundaries.

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

8. **Model-agnostic portability**
 Artifact metadata and refs must not encode model or provider identity as provenance. Model-specific diagnostics may live inside artifact bytes when required, but the artifact contract remains portable across model switches.

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
 - default backend is Silo (S3-compatible) for local/dev and default Compose environments

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

1. Activity receives `ArtifactRef`.
2. The reader requires `ArtifactAccessContext` from verified Activity identity or an existing admission owner, then applies metadata, raw-byte and quarantine policy.
3. Activity returns compact results to workflow code.

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

## 6. Artifact store choice (S3-compatible default)

## 6.1 Options considered

| Option | Pros | Cons | Recommended use |
|---|---|---|---|
| S3-compatible storage (S3 / Silo) | scalable, durable, multipart-friendly, cheap for blobs | requires object storage infra | **primary blob store** |
| Database blobs | fewer moving parts | poor for large objects, expensive, hurts OLTP characteristics | discouraged |
| Local filesystem | simple for dev | poor durability, poor scale, bad multi-node story | dev-only fallback |

## 6.2 Decision

**MoonMind’s default artifact store is S3-compatible object storage.** Postgres
owns the Artifact Index; the existing S3 adapter owns blob access. Explicit
external endpoints, buckets and credentials remain operator settings.

| Candidate | Fit for the existing adapter |
|---|---|
| [Silo](https://github.com/pgsty/silo/releases) | Maintained MinIO fork with independent server fixes; retains credential configuration, multipart, checksums and presigning. Selected for the smallest operational change. |
| [SeaweedFS](https://github.com/seaweedfs/seaweedfs/wiki/Quick-Start-with-weed-mini) | Maintained single-process S3 option; introduces filer/master configuration and different credential administration. |
| [Garage](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/) | Supports core/multipart operations; different authorization and no signature-v2 support require more adapter/configuration changes. |

Compatibility is qualified through actual bucket, PUT/GET/HEAD/range, checksum,
multipart completion/abort and presigned HTTP operations, rather than inferred
from the S3 label.

## 6.3 Docker Compose default

Production, integration and reliability stacks use `pgsty/silo:latest`, with
`TEMPORAL_ARTIFACT_S3_IMAGE` honoring an explicit image selection. The `minio`
service name, internal/public endpoints, ports and `MINIO_*` credential settings
remain stable. Internal clients use `moonmind-temporal-artifacts-s3`; public
presigned URLs use `TEMPORAL_ARTIFACT_S3_PUBLIC_ENDPOINT`.

Silo uses **`artifact-data`**, never the predecessor’s `minio-data` volume.
Existing installations use the deployment controller’s bounded S3 transfer,
verification and rollback path before application recreation; see
[Compose updates](../Steps/DockerComposeUpdateSystem.md#106-recreate-services).
The source remains recoverable. Artifact IDs, keys, database references,
immutable inputs, pins and independent use claims are unchanged by copying.

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

Immutable checkpoint payloads use a content-addressed blob key:

```text
<namespace>/blobs/<checkpoint-scope>/sha256/<prefix>/<sha256>
```

Each checkpoint boundary still receives its own logical `artifact_id`, owner,
links, retention class, expiry, and pin state. Logical artifact rows may share
the same storage key only when the trusted publisher computed identical bytes
for the same checkpoint scope. Lifecycle deletion locks all rows for the shared
key and removes the blob only after the final logical reference is hard-deleted.

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
* equal checkpoint content may reuse an immutable storage blob without reusing
  or weakening the logical artifact record

---

## 8. Metadata model (Artifact Index)

Stored in Postgres or equivalent metadata storage.

Artifact ownership and pin principals preserve the complete opaque identity as
text, including an owner-type prefix such as `service:`. Storage must accept the
full principal produced from a valid caller identity; it must not truncate or
rewrite that identity. This also preserves authorization for retained artifacts
and in-flight container-job histories.

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

Recommended bounded `artifact_links` metadata for step-aware presentation:

* `step_id`
* `attempt`
* optional `scope` such as `step`

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
* for a given step execution, group by `(namespace, workflow_id, run_id, metadata.step_id, metadata.attempt, link_type)`
* for a workflow across runs, define UI behavior explicitly:

 * latest by `created_at`, or
 * latest run only

Client rule:

* The dashboard must not derive latest step evidence locally by sorting execution-wide artifacts in the browser
* server-side grouping determines the canonical latest artifact for a step execution and link type

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

Typed operator or execution admission governs metadata and bytes. Creator labels
are provenance, not permission. Byte reads additionally enforce restricted-raw,
quarantine and integrity policy. Retention and dependency records create no grants.

## 9.3 Service and worker access

Every read requires `ArtifactAccessContext`, including metadata, previews, downloads,
Skill delivery and restore. Saved-work and Container Job admission persist approved
raw rights before start. Historical recovery uses immutable API start/memo inputs or
that exact run's completed creation, never payload identities. Canonical journals
admit their recorded ordinary inputs for compaction. Quarantine denies raw bytes.

Execution inputs gain that linkage at trusted boundaries, never from agent-supplied owner strings:

* Admission binds the exact persisted plan to its producer-verified dependency closure before granting input access. Historical decoding validates original snapshots and canonical Skill-manifest digests, never current definitions. API grants commit before start; schedules admit before planning; children and step successors admit before returning bindings or reading inputs. Activities use actual namespace/workflow/run identity. Unavailable optional evidence receives no grants; required inputs must remain usable. Ordered artifact locks serialize admission against deletion.
* Session supervision and resolver preparation reuse existing execution grants. Resolver preparation verifies its exact Activity run's Temporal parent and namespace before reading the source plan, Agent Profile and inherited repository snapshots; new artifacts keep the caller's provenance. Session continuations bind the sealed intent and plan to the canonical session; commands check ownership, revision and fence before reading inputs.
* `TemporalOmnigentArtifactGateway.read_admitted_input` checks the plan and reads only its admitted inputs with Step Execution authority, without cross-run cached scope. Host launch, native chat and Skill delivery use this boundary. Pre-plan `read_requested_skill_snapshot` links only the request's selected manifest; `read_repository_access_snapshot` remains limited to repository snapshots.
* Workspace, checkpoint, attachment, Skill and remediation readers use the workflow-built Step Execution identity. Parent handoffs verify the actual Activity parent and namespace against Temporal. Publication links selected child outputs and receipts to that verified parent. Temporal UUIDs remain separate from semantic Step Execution run IDs; retained branch turns must match their recorded launch and parent. Missing or conflicting history grants nothing.
* Writers keep least privilege: managed runtime workers write stdout/stderr/diagnostics for runs they handle, and integration workers write provider snapshots linked to their execution.

Visibility never replaces closure, Step or parent checks. Parent-lookup failures use
publication's bounded retry policy before writes; creator identities are not substitutes
for admitted authority.

## 9.4 Auditing

Log at minimum:

* principal
* artifact ID
* operation
* execution linkage
* timestamp
* IP/user-agent for user-facing API calls where applicable

## 9.5 App auth mode integration

Artifact API auth behavior follows the one app-level selector `AUTH_PROVIDER`
(`accounts` | `oidc` | `header` | explicitly restricted local `disabled`).
Retired selectors (`keycloak`, `default`, `google`, `local`) fail at startup with
migration guidance. Authoritative mode, identity, session, and error semantics
live in [AuthenticationContracts.md](../Security/AuthenticationContracts.md);
this section only binds them to artifact routes.

| `AUTH_PROVIDER` | Artifact API behavior | Intended environment |
| --- | --- | --- |
| `accounts` | Require the MoonMind session for the resolved `User.id`, then execution-linked authorization | fresh installs and shared deployments |
| `oidc` | Require the MoonMind session minted from the verified `(issuer, subject)` pair, then execution-linked authorization | shared dev/staging/prod behind an external IdP |
| `header` | Require the MoonMind session minted from the proxy-asserted identity (ingress strips and replaces identity headers; direct bypass blocked), then execution-linked authorization | authenticated ingress only |
| `disabled` | No end-user credential required at user-facing metadata/presign endpoints, but only behind loopback-only bind or documented trusted ingress (`MOONMIND_TRUSTED_INGRESS=1`); historical artifact owner-check bypass remains, with raw/quarantine protection | one-click local/dev |

User versus machine credentials at artifact routes:

| Caller | Accepted credential | Notes |
| --- | --- | --- |
| Browser / API user | MoonMind session cookie (`__Host-mm_session` on HTTPS; `mm_session_dev` only for explicitly permitted loopback HTTP), short-lived and purpose-bound | Cookie mutations require CSRF protection and origin validation; conflicting cookie/bearer identities are `401 auth_conflict` |
| Machine (artifact/worker paths) | Scoped service identity with least privilege (§9.3); container-job bearer on its authorized routes | Succeeds only on authorized routes and resources; the removed legacy worker-token path is `410 worker_token_deprecated` |

Error behavior: missing credential at a strict boundary is `401 auth_required`;
invalid, expired, wrong-key/issuer/purpose, or reserved-identity credential is
`401 auth_invalid`; authenticated but inactive or unauthorized is `403`;
identity-store unavailability is `503` and fails closed. SSE and artifact
downloads work without placing broad tokens in URLs; blob access is issued only
after metadata-level authorization succeeds.

This is an API-layer auth choice. It does **not** require public object-storage buckets.
Native chat and upstream runtime access stay behind the qualified same-origin
binding facade, not an unrestricted upstream proxy.

## 9.6 Single-user operation

Artifacts are instance resources for the admitted operator: verified operator admission inspects instance artifacts without a human-owner lookup. Legacy UUIDs remain creator/quota provenance. Execution reads require verified linkage in every authentication mode; service-only reads require their own resource ownership.
Restricted raw bytes, quarantine, redaction, and explicit-grant gates are
enforced independently of ordinary artifact visibility. See
[Single-User Application Design](../SingleUserApplicationDesign.md).

The guarded upgrade can convert an artifact-only retained dataset after proving
one human operator (including explicitly attested same-person UUID aliases).
It records a protected, server-owned recovery disposition on artifacts with a
proven human creator. Only an operator context carrying the existing server-owned recovery proof may use this disposition for restricted raw recovery. HTTP admission binds this proof separately from creator and consumer-claim provenance; payload strings cannot reconstruct it. Explicit input delegation may preserve a selected permitted raw read for one workflow; it never transfers operator recovery authority. Direct
artifact and durable session-resource reads share this proof and read policy.
Profile bundle and workflow evidence readers carry it only after their own
admission and resource-scope checks. Authenticated modes retain their own UUID
identity: trusted-header reads use the existing ingress and live identity-mapping
checks, without acquiring disabled-mode recovery authority. An absent proxy
assertion preserves optional session/machine admission; a presented invalid
assertion fails closed. Caller metadata or headers cannot grant recovery.
Quarantined bytes remain preview-only, and machine-created artifacts receive no human
recovery grant. Original creator provenance, quota scopes, links, pins, and
in-flight claim owners remain unchanged; the operator cannot release a legacy
claim merely by receiving recovery access. Historical pin/deletion actors and
expired claims remain provenance; their actor labels do not require live accounts.

The grant and current-revision completion record commit atomically. Authority
writers are excluded while the guarded inventory is checked and transformed;
interruption rolls both back. Historical ledger completion preserves established
operator admission but grants no new artifact recovery authority. Missing or
malformed creators refuse conversion, including null creators with only
deployment-ownership attestation. Instance execution records retain their
original owners and admitted work. Legacy workflow parents still refuse before
artifact mutation until their owning conversion is available. Historical links without a
live execution remain intact and do not invalidate independently proven creator
attribution. Schema installation itself grants no recovery access. Once recovery grants
exist, schema downgrade refuses rather than silently losing them; repair forward.
Default restarts probe for outstanding human or ambiguous artifact creators
before replaying certified completion. Later artifacts, including OIDC writes
before an account-free switch, require current guarded attribution and atomic
conversion. The probe never grants access. Established machine owners and the
exact disabled-mode creator who already has owner access need no new recovery
grant. With no pending recovery, startup avoids loading retained histories into
Python; the bounded-result SQL probe may still scan the artifact table.
When disabled mode uses its built-in zero UUID and no retained User has that
ID, exact zero-owned artifacts and active claims remain deployment scope during
conversion, unchanged and without new recovery grants. A retained zero User still
counts as a person. Nonzero configured defaults require ordinary attribution
evidence when mixed with newly pending historical recovery; configuration alone
does not prove a historical identity or alias.
Explicit preflight and apply inspect current source evidence, and unresolved
aliases or unsupported live parents still require their existing dispositions.

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

* `ephemeral`: seven days of operational/debug data
* `standard`: default inputs/outputs
* `long`: important outputs or manifests
* `pinned`: no automatic deletion

## 14.2 Default mapping examples

Representative mappings:

* `output.logs`, `debug.trace`, `debug.skill_resolution_trace` → ephemeral
* `runtime.stdout`, `runtime.stderr`, `runtime.merged_logs`, `runtime.diagnostics`, `runtime.omnigent.sse.raw`, `runtime.omnigent.sse.normalized` → ephemeral
* `input.instructions`, `input.plan`, `input.manifest`, `input.prompt_index` → standard or long
* `output.primary`, `output.patch`, `output.summary`, `input.skill_snapshot` → standard or long
* `output.provider_snapshot` → standard or ephemeral depending on operational needs
* `runtime.skill_materialization` → standard or ephemeral depending on reproducibility goals
* explicitly pinned artifacts → pinned

## 14.3 Lifecycle manager

The hourly `MoonMind.ManagedRuntimeWorkspaceCleanup` operational workflow invokes
`artifact.lifecycle_sweep` after its workspace pass. The artifact Activity remains
the authority for expiry, pin protection, deletion, storage mutation, and
durable metadata. A lifecycle failure marks that maintenance run
degraded and is retried by the next schedule rather than overwriting successful
workspace cleanup evidence.

Each sweep pages through expiry, pending uploads, deletion intents and tombstones
for up to 40 minutes. Deletions commit per candidate to release locks; metadata
purges commit bounded pages. Heartbeats continue during operations. Unresolved
work remains observable for the next hourly pass.

Retention is assigned on creation/linking. Migrations shorten expiry only when
every link matches the changed class, measured from the latest link, never before
creation. Pins and live claims protect every artifact. Non-terminal bridge sessions
also protect recovery journals; ordinary retention resumes when sessions end.

Trusted plan, Skill and saved-work producers seal a flat dependency set with the
owning transaction: logical IDs, original digests, requiredness and roles. Server-owned
completion and request digests distinguish verified-empty, historical-unsealed and
corrupt closures. Repeating the same direct request reuses its original closure;
later child seals cannot redefine it. Admission, retention and recovery consume this
proof rather than independently discovering nested references.

Required dependencies remain protected for their root's intrinsic lifetime: expiry,
pins, live claims, current definitions or active execution. Incoming edges alone do
not extend lifetime, and historical plans do not retain everything forever. Missing
optional evidence reports partial availability; unavailable required inputs stop
admission/recovery. Integrity checks do not deliver restricted raw bytes or grant
permission. Quarantine still prevents executable admission.

Schedules save an explicit input root, including while disabled. Definition and
closure commit before Temporal dispatch; the same row lock fences delayed dispatch,
and startup reconciliation repairs uncertain projections by saved ID. Errors mean
saved/pending refresh, not recreate. Backfill uses original verified snapshots,
without changing the definition's optimistic version or resolving today's Skills.
Current Skill pointers retain their revisions; obsolete unowned content remains
reclaimable through the existing lifecycle and ordered artifact/alias locks.

Before expiry selection, bounded lifecycle pages backfill retained saved-work roots
from their original manifests and producer-run links. Failed seals roll back and
remain observable. Until sealing succeeds, only already-linked artifacts from that
exact namespace/workflow/original run receive temporary protection while the root
is live. This guard creates no grants and ends when the root is sealed or retired.

`ArtifactTransactionOwnership.CALLER` flushes without committing admission, retry
or schedule transactions. `STANDALONE` commits independently. Start/reset follows
durable grants. Caller reads release observation-only savepoints before artifact
locks. Streams, signed downloads and physical deletion require standalone ownership.
Committed TTL claims survive caller rollback. Cleanup preserves primary errors
and cancellation; transaction-control failures propagate.

Caller writes persist reserved `_moonmind/pending-writes/` markers containing target
identity and timestamp, never payload or authority. S3 credentials need prefix LIST
and marker read/write/delete access. Cancellation drains writes before releasing
database key fences. After 24 hours, lifecycle reconciles under the same fence:
committed/shared references preserve bytes, settled orphans are deleted, uncertain
cleanup retains tracking. Bounded scans pass invalid markers without trusting their
targets. Trusted caller payloads use bounded single PUTs; public upload limits and
standalone multipart behavior remain unchanged.

Known database connection failures return `artifact_authority_unavailable`;
storage timeouts return `artifact_storage_unavailable` (HTTP 503, `Retry-After: 5`).
Storage credential rejection is `artifact_storage_forbidden` (502); confirmed
object absence is `artifact_bytes_missing` (409). Reader denial, quarantine,
expiry and integrity checks retain their own semantics. Failures never change
artifact availability or grants. Activities retry unavailability within existing
budgets; permanent artifact errors stop. Lazy downloads open before response
headers; later transport failures terminate the stream and retain their cause.
Unreadable publication decisions stop reconciliation rather than authorize
replacement publication.

Omnigent commits each complete replacement journal pair before reclaiming its
superseded numbered prefixes. The replacement retains the accumulated events;
the existing artifact service owns tombstones, physical deletion and persisted
deletion intents. Canonical journal refs, pinned prefixes and prefixes held by a
live consumer are preserved. A physical-delete failure leaves the committed
replacement intact and an intent for the hourly lifecycle sweep to reconcile.
Bridge event-index references advance atomically with the complete journal pair;
reclamation preserves exact payload references owned by append-only control-plane
observations. Pins and use claims protect the original snapshots without freezing
the event index on an older prefix.

Each journal flush durably adds a raw/normalized pair and links, superseding the
previous pair. Even idle streams flush every event; bounded purge reclaims the
resulting tombstones.

Deletion must be idempotent.

## 14.4 Soft-delete, hard-delete and purge

* soft-delete first, denying new presigns
* hard-delete later: the tombstone commits with a deletion intent before the
  object delete, and the intent clears only after the delete is confirmed
* purge the tombstone row and its links once it is older than
  `TEMPORAL_ARTIFACT_LIFECYCLE_PURGE_AFTER_SECONDS` (default seven days)

A tombstone alone never authorizes purge: the row needs no deletion intent, and
either another live row owns its shared bytes or the store reports the key
absent. Present or unknown bytes return to deletion-intent reconciliation; a
store error is not progress, so an outage hands over at most a page per pass.
Pin rows, live use claims, non-terminal journal refs and remediation context
refs keep the tombstone; a `PINNED` class left by deletion does not. Pin
and link requests lock the row first; candidates are locked with
`SKIP LOCKED` and rechecked, so a concurrent protection wins or finds the
artifact gone. Rows and links go in one transaction, so a failed commit leaves
the tombstone.

Seven days matches the ephemeral class and outlasts Activity retries,
interrupted-step recovery and the hourly sweep, so recent refs see a truthful
deleted state. Afterwards, reading an ID whose embedded creation time is past the
horizon fails as `artifact_expired` with that time; a newer missing ID stays
`artifact_not_found`, since purge cannot have removed it.

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
