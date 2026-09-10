# Manifest Registry Preservation Runbook — #4191

Parent: MoonLadderStudios/MoonMind#4187. Plan MR4 in the pinned removal plan.
Design depends on #4188's writer inventory and #4189 milestone A's
historical-read/cutover contract. Integrate with #4190. Destructive
application is separately gated on stopped writers/callbacks, verified
preservation and the deployment owner's authorization, not merely a merged PR.

Status: Proposed (pre-destructive). Correctly unarchived until every
documented writer drains and the archive/delete triggers below fire.

Sibling ownership (do not duplicate here):
- #4187 (parent): Manifest system removal plan and milestone sequencing.
- #4188: writer inventory — the authoritative caller list for every
  `manifest`-table reader/writer and worker state callback. This runbook
  consumes that inventory; it never invents writer coverage from field names.
- #4189: milestone A historical-read/cutover contract — the generic
  `MoonMind.ManifestIngest` read path for both entry contracts
  (`manifest_ref` compile, `manifestArtifactRef` orchestrate). This runbook
  preserves evidence for that contract; it never reimplements its serializers.
- #4190: integration gate — consumes sanitized schema/upgrade/restore
  evidence from this runbook. #4189 and the integration gate own acceptance
  of the preserved history.
- #4189 and the integration gate own the final go/no-go; this runbook only
  rehearses the preservation side hermetically.

Source baseline: plan baseline `7bd159dafb44770278e8c5563d47667de6e7c491`.
At that baseline `api_service/db/models.py` defines `ManifestRecord` with
table name `manifest`; `api_service/services/manifests_service.py` and
`api_service/services/manifest_sync_service.py` read/write registry state,
hashes and run references; `api_service/api/routers/manifests.py` exposes a
worker state callback (`POST /api/manifests/{name}/state`); shared Temporal
execution models contain Manifest-related type/ref/metadata consumers.
Inspect current schema/migration history before selecting exact columns or
PostgreSQL types to remove. Current head at authoring time:
`376_merge_375_heads` (merging `375_artifact_principal_text` and
`375_session_authority_4121`); initial clean revision `0b8e4befb8e5` creates
the `manifest` table. No forward revision drops it yet.

Hermetic gate: `tools/manifest_registry_preservation_rehearsal.py`
(`--mode preflight | rehearsal | upgrade-check | preservation-check |
rollback-check | retirement-check | all`). It performs no live mutation and
never contacts a deployment host, live database, Temporal, MinIO, or Docker.
`REHEARSAL_PASS_DEPLOYMENT_BLOCKED` is the honest terminal state for a repo
checkout: fixtures pass while owner-held and live-deployment prerequisites
stay blocked.

## 1. Preflight (bounded, read-only, redacted)

Use existing database/deployment/artifact mechanisms (migration locking and
version checks, deployment worker, Temporal visibility, artifact/MinIO
readers, capability registry). Inventory:

- affected registry rows and their dedicated state (see disposition table),
- active writers and pending callbacks (see #4188 inventory),
- recurring schedules or workers with registry producers,
- stored definitions and their version/hash provenance,
- issued capabilities and relevant schema/readers,
- exact migration head/version and actual mounts.

Produce a redacted report (counts and refs only — no YAML content, state
payloads, documents, metadata, text, or secrets) with one actionable
verdict:

- `proceed`: no active writers, no unpreserved unique registry data,
  preservation verified, ownership exact, #4188/#4189 ready.
- `drain`: active/pending/scheduled registry work exists. Choose drainage on
  the old release or the existing versioned worker/cutover mechanism, record
  the pending-consumer evidence and the finite retirement condition. No
  schedule may retry forever against a deleted registry.
- `blocked`: ambiguous ownership, unpreserved potentially-unique data,
  unverified preservation, incompatible old application versions, concurrent
  migrators, or missing #4188/#4189/#4190 evidence. Refuse destructive
  application until resolved.

Never guess from field names. A field name alone is not proof that data is
disposable. Hermetic rehearsal:
`python tools/manifest_registry_preservation_rehearsal.py --mode preflight`.

## 2. Persistence disposition (caller-backed, with owners)

Each row names its owner. `delete` means drop after verified preservation;
`retain` means keep as generic historical evidence; `temporary` means bounded
recovery data with an exact consumer and finite cleanup condition. There is
no permanent second registry or alias.

| Persistence | Disposition | Owner |
|---|---|---|
| `manifest.content` (exact YAML bytes) | delete after verified protected export + restore | manifest-registry (#4191) |
| `manifest.content_hash` (immutable digest) | delete after export; never rewrite to match new schemas | manifest-registry (#4191) |
| `manifest.version` | delete after export | manifest-registry (#4191) |
| `manifest.state_json`, `manifest.state_updated_at` (incremental/state payloads) | delete after export | manifest-registry (#4191) |
| `manifest.last_indexed_at` | delete after export | manifest-registry (#4191) |
| `manifest.last_run_job_id`, `last_run_source`, `last_run_status`, `last_run_workflow_id`, `last_run_temporal_run_id`, `last_run_manifest_ref`, `last_run_started_at`, `last_run_finished_at` (last-run links) | delete after export; last-run links re-anchored to execution history via preserved run IDs | manifest-registry (#4191) |
| `manifest` constraints/indexes (`UNIQUE(name)`, `ix_manifest_id`) | delete with table | manifest-registry (#4191) |
| `ManifestRecord` ORM model | delete with forward migration after drain contract | manifest-registry (#4191) |
| writers: `ManifestsService.upsert_manifest`, `ManifestsService.submit_manifest_run` run-metadata updater, `ManifestsService.update_manifest_state`, `ManifestSyncService.sync_manifest`, router `PUT /api/manifests/{name}`, router `POST /api/manifests/{name}/state` worker state callback | retire (stop writers/callbacks before destructive step) | #4188 inventory, executed by #4191 |
| saved definitions (registry YAML bytes are the saved-work copy) | preserve via bounded export envelope; deleting a registry entry must not cascade into the sole saved-work copy | manifest-registry (#4191) |
| shared execution columns: `TemporalExecutionCanonicalRecord.manifest_ref`, `TemporalExecutionRecord.manifest_ref`, `input_ref`, `plan_ref`, owner IDs, exact run IDs, lineage, input/plan/result refs, timestamps | retain as generic historical evidence for #4189 read path | temporal-execution (#4189) |
| `TemporalWorkflowType.MANIFEST_INGEST` (`MoonMind.ManifestIngest` type string) and both input contract shapes (`manifest_ref` compile, `manifestArtifactRef` orchestrate) | retain; removing an active Python enum member/response variant must not cause ORM decoding or execution listing to fail | temporal-execution (#4189) |
| unrelated `*_manifest_ref` columns (`capture_manifest_ref`, `image_manifest_ref`, `step_execution_manifest_ref`) | retain; not registry state | respective subsystems |
| historical serializers (execution list/detail, lineage, artifacts) | retain generic read path; do not coerce old records to UserWorkflow | #4189 |
| artifact links and retention/GC authority | retain independent authority; wrong-owner, restricted, missing, expired and corrupted evidence must remain truthful and authorized | artifact/retention owner |
| temporary recovery data (if any retained schema is needed) | temporary only with exact old consumer + finite cleanup condition named here before use | #4191 with #4188 consumer |

Shared-enum/type rules: preserve original `MoonMind.ManifestIngest` type
strings, both input contract shapes, owner IDs, exact run IDs, lineage,
input/plan/result refs and timestamps needed for #4189's generic read path.
Do not coerce old records to UserWorkflow, delete whole executions, or
rewrite immutable hashes to match new schemas.

## 3. Preservation (before stopping the old writers for the final copy)

1. Stop writers (or prove a final reconciliation if copying began earlier).
   Use a consistent snapshot after stopping writers; a proven final
   reconciliation is required if the copy began earlier.
2. Record the previous image/version, exact mounts, and recovery config.
3. Preserve necessary exact YAML bytes, version/hash, incremental/state
   payloads, timestamps and last-run links before registry deletion.
4. Include row counts and content/digest checks without printing sensitive
   values (counts and hex digests only; historical YAML/state can contain
   sensitive material even when current validation rejects secrets).
5. Verify a usable restore, not just successful file creation (restore into
   an isolated namespace/database and re-verify counts/digests/links).

Hermetic rehearsal:
`python tools/manifest_registry_preservation_rehearsal.py --mode preservation-check`.
The gate writes only to a hermetic state dir, sets operator-only file modes
where the platform allows, and never uploads to public GitHub, normal
broadly visible artifacts, or Temporal history.

## 4. Protected exports (operator-controlled)

Keep exports protected and operator-controlled. Preserve original access
restrictions, retention and recoverability. Never add a new export database
or secret system. Concretely:

- exports live in operator-controlled storage with restrictive modes
  (0600 equivalent where the platform supports it; best-effort chmod
  elsewhere is documented, not silent);
- no public GitHub upload, no normally visible artifact upload, no Temporal
  history upload (memo/search attributes stay counts/refs only);
- no new export database; no new secret system;
- redacted logs and sanitized evidence only (see section 8);
- original retention and recoverability preserved; the export is not a
  second registry and has no serving path.

## 5. Migration and compatibility

Remove `ManifestRecord` and the `manifest` table with a forward Alembic
migration after the preservation/drain contract is satisfied. Keep applied
migrations and their ancestry. Old revisions are self-contained: no applied
revision imports soon-deleted runtime modules (verified by the gate), so no
history rewrite is needed and already-applied business outcomes are never
rewritten.

Coordinate new/old application compatibility and concurrent migrators
through existing migration locking/version mechanisms. Refuse destructive
application while old writers or incompatible code remain active. If
temporary retained schema is needed, identify its exact old consumer and
finite cleanup condition in the disposition table above. Do not add a
permanent second registry or alias.

Fresh installation and upgrade from a populated old database must both
work. Fresh migration to head works without importing removed Manifest
runtime modules (gate checks the head chain). Existing-database upgrade
removes dedicated registry ownership and intended obsolete fields while
preserving authorized generic historical reads for both entry contracts
(gated on #4189).

Real supported PostgreSQL migration coverage is distinguished from
SQLite/helper tests, and required CI runs the relevant regressions. The
hermetic gate and its unit tests are SQLite/helper-scoped rehearsal only;
they never claim production PostgreSQL coverage.

## 6. Artifact retention (independent authority)

Preserve artifact links and existing retention/GC authority independently of
the deleted registry. Wrong-owner, restricted, missing, expired and
corrupted evidence must remain truthful and authorized. Deleting a registry
entry must not cascade into shared execution evidence or the sole
saved-work copy. Retention windows and GC ownership do not move with the
registry deletion.

## 7. Irreversible boundary and Rollback

The forward migration dropping `manifest` is the irreversible boundary.

- Before it, rollback can use the matching compatible release (exact
  schema/release identities pinned in the gate: head
  `376_merge_375_heads`, initial `0b8e4befb8e5`, manifest table definition).
- After it, schema downgrade alone cannot recreate lost content: require
  verified protected restoration or forward repair.
- Rehearse the supported procedure without overwriting unrelated newer
  executions, credentials or shared database changes (isolated
  namespace/database restore; row-level reconciliation, never broad backup
  restore over newer work).
- Unsupported rollback stops actionably instead of booting a partially
  restored application.

Hermetic rehearsal:
`python tools/manifest_registry_preservation_rehearsal.py --mode rollback-check`.
Supported rollback/forward repair is rehearsed with exact schema/release
identities and preserves unrelated newer work; unsupported paths fail
clearly. No blanket table/enum deletion, applied-migration erasure, broad
backup restore over newer work, MinIO purge, volume prune or automatic
production data deletion.

## 8. Sanitized evidence for #4189 and the integration gate

Supply sanitized schema/upgrade/restore evidence to #4189 and the
integration gate (#4190). Reuse existing Alembic and database tests; no
production migration/export/delete is performed merely because this issue
exists. Evidence is counts, refs, digests, revision IDs, and redacted
verdicts only — never YAML bytes, state payloads, or secrets. The gate's
`--json-out` summary is the handoff artifact shape.

## 9. Retirement (exact-resource operator action only)

Retirement is a separate exact-resource operator action after verified
preservation and owner authorization — never part of normal upgrade, never
automatic. Forbidden retirement patterns (the gate refuses them):

- blanket table/enum deletion without per-column disposition;
- applied-migration erasure or history rewrite;
- broad backup restore over newer work;
- MinIO purge, volume prune, `down -v`, `docker system prune`;
- automatic production data deletion merely because this issue exists;
- deleting whole executions to remove registry history;
- coercing old `MoonMind.ManifestIngest` records to UserWorkflow;
- rewriting immutable hashes to match new schemas;
- adding a permanent second registry or alias.

Hermetic rehearsal:
`python tools/manifest_registry_preservation_rehearsal.py --mode retirement-check`.

## 10. Retention

Explicit recovery retention window lives with the operator-controlled
export; eventual deletion of the protected export is a separate
exact-resource operator action after export/restore verification.
Unrelated users, profiles, workflow history, workspaces and saved-work data
remain intact. Retention/GC authority for execution evidence does not move.
