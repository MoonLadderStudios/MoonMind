# Qdrant-Removal Cutover Runbook — #4115

Status: Proposed

Parent: MoonLadderStudios/MoonMind#4103. This issue (#4115) owns the
**replay-safe cutover, data-preservation, and rollback procedure**.
Admission + authoring retirement belongs to #4105 (landed as `c234b452e`;
inventory in `docs/tmp/QdrantRemovalInventory-4105.md`). Handler/field
deletion belongs to #4106–#4109. Capability/audit-state classification for
`moonmind_retrieval_state` belongs to #4107. Final integrated rehearsal
uses #4110, #4111, #4112, #4113; its evidence goes to #4114.

Source baseline: `9424899410a1df1b359fb1b803b9d21912e0b50a`. That Compose
declares both `qdrant-storage` and `moonmind_retrieval_state`; they are
**distinct data** and must never be treated as interchangeable. #3948
documents distinct historical ManifestIngest entry/Activity contracts;
both historical generic entries stay readable (see §4). Repository
inspection alone cannot establish an operator's active work or whether
Qdrant holds unique content — the operator preflight (§1) is authoritative.

Hermetic rehearsal: `tools/qdrant_cutover_rehearsal.py` (`--mode preflight |
rehearsal | rollback-check | retirement-check | all`). The gate never
contacts live Qdrant/Temporal/Docker, never deletes anything, and reports
operator-gated steps as `blocked` with the missing evidence named. Fixture
passes never mark deployment qualification complete.

Archive/delete triggers: archive this runbook when #4114 records the final
integrated rehearsal evidence AND the finite retirement condition (§2) is
operator-verified for the selected deployment; delete the temporary
inventory excerpts when the obsolete scaffolding they describe has drained
per §2. Never archive before both hold.

## 1. Bounded read-only preflight (operator, per deployment)

Use existing deployment/Temporal/state owners. Inventory, redacted:

1. Affected active workflows, pending/retryable Activities, recurring schedules.
2. Stored vector manifests / profile defaults, issued capabilities.
3. Relevant schema/readers, exact Compose project/service/container IDs, actual mounts.
4. Collection/payload provenance and potentially Qdrant-only documents, metadata, or text.

Produce a redacted report with one actionable verdict — `proceed`, `drain`,
or `blocked` — not a guess from service names. Hermetic default is
`blocked` (live facts unknown from a checkout); `drain` when §2 consumers
exist; `proceed` only with positive live evidence plus a verified snapshot
(§5). No live production inventory is inferred from repository code.

## 2. Drainage decision and finite retirement condition

Decision: **drainage on the old release** is the default for affected
histories. The existing versioned worker/cutover mechanism is the explicit
alternative when the deployment declares a supported versioned worker.
Never reinterpret historical payloads, never delete an Activity handler as
the drainage mechanism, never retain an indefinite compatibility backend.

Record the pending-consumer evidence (schedule IDs, pending Activity IDs,
capability IDs — redacted) with this runbook. Retirement executes only when
all three hold (finite retirement condition):

- no active schedule produces vector work;
- no pending/retryable vector Activity remains;
- snapshot/export recoverability is operator-verified (§5).

New native vector admissions are already stopped by #4105 (explicit
`rag`/`followUpRetrieval` fail closed before Temporal start/launch; schedule
producers retired). This runbook coordinates the drain; it does not re-admit.

## 3. Replay safety

Representative replay covers the changed initial-context,
launch/capability, manifest, digest/finalization, and cleanup boundaries.
Both historical generic ManifestIngest entries stay readable per #3948 and
existing old Activity/command fixtures per #3944 are preserved. Each
affected persisted handoff is exercised for restart/retry/cancel. A history
that cannot safely run on the new release gets explicit old-release drain
ownership, never a silent fallback. Immutable inputs/evidence are never
rewritten to change semantics.

## 4. Historical ManifestIngest entries (preserved)

- `manifest-ingest-generic-entry/v1`
- `manifest-ingest-generic-entry/v2`

Both must remain readable across the cutover. The rehearsal gate fails a
changed boundary that does not carry both entries.

## 5. Snapshot, logical export, recoverability (before stopping the old service)

1. Record the previous image/version (`qdrant/qdrant:v1.17.1` at baseline),
   exact mounts (`qdrant-storage:/qdrant/storage`), and recovery configuration.
2. Preserve a recoverable snapshot plus a logical export where needed.
3. Verify recoverability (restore-verification; live scratch-restore is
   operator-verified) and classify each payload as **reproducible** (derived
   from a deterministic source) or **unique** (Qdrant-only or unknown —
   preserved). Never assume reconstructibility from vectors alone.
4. No disposal decision before verification completes.

Custody: originals live in **operator-controlled authorized storage** with
redaction and a recorded retention window — never public issues or source
control. `moonmind_retrieval_state` capability/audit evidence is preserved
independently per #4107, never mixed with old vector storage.

## 6. Matched upgrade

Deploy matching application, schema/caller changes, dependency images, and
Compose together. Rehearse the supported upgrade under sanitized fixtures:
an old `.env` (with `QDRANT_*` keys), stored retired requirements, and
representative historical artifacts. Failures must explain actionably. No
new mandatory disable flag, no fake embedding credential, no substitute
search service.

## 7. Exact-container retirement (operator, idempotent)

Identify and stop/remove **only** the exact obsolete Qdrant container owned
by the selected deployment (exact Compose project, e.g. `moonmind`, plus
exact service/container IDs from §1). Then verify absence. Removing the YAML
definition does not stop a running orphan. Refuse wrong/ambiguous project
ownership. Repeated checks are safe (idempotent). Forbidden: broad orphan
cleanup, `docker compose down -v`, indiscriminate volume prune, broad
filesystem deletion. Unrelated services/volumes stay intact.

## 8. Retention window (no automatic deletion)

Retain the old Qdrant volume and exports through an explicit
recovery/retention window (record the window with §5 custody). Permanent
deletion is a **separate exact-resource operator action** after
export/restore verification — never an automatic normal-upgrade step.
Preserve PostgreSQL, MinIO, secrets, workspaces, unrelated containers, and
Omnigent state. Creating these issues authorizes no real production deletion.

## 9. Rollback (rehearsed against fixtures)

Rollback uses the **previous matching application/Compose/image revision**
with preserved data, after a schema-compatibility check, with
reconciliation recorded. Never add an optional Qdrant profile or adapter
back to the new release as a rollback feature. Old-release test
infrastructure is isolated upgrade-fixture infrastructure, never supported
new-release deployment topology.

## 10. Evidence and handoff

Publish exact rehearsal evidence (gate `--mode all` JSON, redacted
preflight report, pending-consumer record, snapshot/export verification
refs) plus remaining operator-only steps to #4103/#4114, distinguishing
automated fixture verification from protected deployment checks and real
operator actions not performed. Never claim every real installation was
upgraded. Remove obsolete new-release compatibility scaffolding once its
documented consumers drain per §2.
