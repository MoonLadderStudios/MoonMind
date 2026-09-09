# Qdrant Cutover Runbook — #4115

Parent: MoonLadderStudios/MoonMind#4103. This runbook owns the replay-safe
cutover, data preservation, and rollback procedure for the bounded upgrade
from a Qdrant-bearing deployment to the vector-free release. Design and
sanitized fixtures in this runbook are not blocked on the final integrated
verification issue; exact rehearsal evidence is published to #4103/#4114.

Status: Proposed (pre-cutover). Correctly unarchived until every documented
consumer drains and the archive/delete triggers below fire.

Sibling ownership (do not duplicate here):
- #4105 (merged): retired vector admission/authoring/plan compilation.
  Inventory: `docs/tmp/QdrantRemovalInventory-4105.md`.
- #4106–#4109: vector-only handler/field deletion in execution code.
- #4107: `moonmind_retrieval_state` capability/audit-state classification.
  Preserve that evidence independently from old vector storage.
- #4110–#4113: integrated rehearsal pieces. #4114 consumes final evidence.
- #3948/#3944: ManifestIngest public compile/execute semantics and old
  Activity/command fixtures. The two historical generic ManifestIngest
  entries (`manifest_ref` compile, `manifestArtifactRef` orchestrate) are
  preserved verbatim and stay replayable.

Source baseline: `9424899410a1df1b359fb1b803b9d21912e0b50a`. That Compose
revision declares both `qdrant-storage` and `moonmind_retrieval_state`;
they are distinct data and must never be treated as interchangeable.
Repository inspection alone cannot establish an operator's active work or
whether Qdrant holds unique content — the live Preflight below does.

Hermetic gate: `tools/qdrant_cutover_rehearsal.py` (`--mode preflight |
rehearsal | upgrade-check | preservation-check | rollback-check |
retirement-check | all`). It performs no live mutation and never contacts
a deployment host. `REHEARSAL_PASS_DEPLOYMENT_BLOCKED` is the honest
terminal state for a repo checkout: fixtures pass while owner-held and
live-deployment prerequisites stay blocked.

## 1. Preflight (bounded, read-only, redacted)

Use existing deployment/Temporal/state owners (deployment worker,
Temporal visibility, artifact/MinIO readers, capability registry). Inventory:

- affected active workflows and pending/retryable Activities,
- recurring schedules with retired vector producers,
- stored vector manifests and profile defaults,
- issued capabilities,
- relevant schema/readers,
- exact Compose project/service/container IDs and actual mounts.

Produce a redacted report (counts and refs only — no collections,
payloads, documents, metadata, text, or secrets) with one actionable
verdict:

- `proceed`: no active vector work, no unpreserved unique data, ownership exact.
- `drain`: active/pending/scheduled vector work exists. Choose drainage on
  the old release or the existing versioned worker/cutover mechanism, record
  the pending-consumer evidence and the finite retirement condition. No
  schedule may retry forever against a deleted backend.
- `blocked`: ambiguous ownership or unpreserved potentially-unique data.
  Refuse retirement until resolved.

Never guess from service names. Hermetic rehearsal:
`python tools/qdrant_cutover_rehearsal.py --mode preflight`.

## 2. Admission retirement and cutover choice (#4105 + this runbook)

New native vector admissions are already stopped at #4105 (explicit
`rag`/`followUpRetrieval` rejected before Temporal start/host launch;
schedules/drafts/patches strip retired fields; plan compilation carries no
vector descriptors). This runbook adds:

- explicit retirement of retired schedule producers (no indefinite
  compatibility backend in the new release),
- the documented drainage-vs-version-routing choice per affected history,
- pending-consumer evidence and the finite retirement condition.

Do not reinterpret historical payloads, do not merely delete an Activity
handler, and do not retain an indefinite compatibility backend. A history
that cannot safely run on the new release gets explicit old-release drain
ownership, never a silent fallback.

## 3. Replay safety

Representative old/new histories replay, or they carry tested bounded
old-release drainage. The gate checks both historical generic ManifestIngest
entries plus restart/retry/cancel containment across the real-shaped
sequence (preflight → preserve → upgrade → retire → verify) with
failure injection at each step. Immutable inputs and evidence are never
rewritten to change semantics. Old Activity/command fixtures (#3948/#3944)
are preserved.

## 4. Preservation (before stopping the old service)

1. Identify collection/payload provenance and potentially Qdrant-only
   documents, metadata, or text.
2. Record the previous image/version (`qdrant/qdrant:v1.17.1`),
   exact mounts (`qdrant-storage:/qdrant/storage`), and recovery config.
3. Preserve a recoverable snapshot plus a logical export where needed.
4. Verify recoverability; classify data as reproducible or unique before
   declaring anything disposable. Never assume payloads reconstructible
   merely because vectors are derived.
5. Keep originals in operator-controlled authorized storage with redaction
   and retention — never public issues or source control. Preserve #4107
   retrieval-state evidence independently.

Hermetic rehearsal: `--mode preservation-check --preservation-description
"...provenance...snapshot...export...verif..."`. A description claiming
derived-vectors-are-disposable or public-issue storage is refused.

## 5. Upgrade (matching revision set)

Deploy matching application, schema/caller changes, dependency images, and
Compose together. Rehearse under sanitized fixtures: an old `.env`
(`QDRANT_URL`, `QDRANT_ENABLED=true`, `VECTOR_STORE_PROVIDER=qdrant`),
stored retired requirements (rejected at admission with actionable
guidance), and representative historical artifacts (stay readable).
Explain failures actionably. There is no new mandatory disable flag, no
fake embedding credential, and no substitute search service. Old-release
test infrastructure is isolated upgrade-fixture infrastructure, not
supported new-release topology.

Hermetic rehearsal: `--mode upgrade-check` (also covered by
`tests/unit/tools/test_qdrant_cutover_rehearsal.py`).

## 6. Retirement (exact container only)

1. Identify the exact obsolete Qdrant container owned by the selected
   deployment (Compose project name, service `qdrant`, container ID).
2. Refuse ambiguous or wrong-project ownership — never broaden to orphan
   cleanup, `docker compose down -v`, volume prune, or filesystem deletion.
3. Stop/remove only that container; verify absence afterward.
4. Repeated retirement checks are safe (idempotent: absence re-verifies).

Removing the YAML definition does not stop a running orphan. Creating
these issues does not authorize any real production deletion.

Hermetic rehearsal: `--mode retirement-check --retire-action "stop/remove
exact qdrant container (identified from inventory)"`. Without an explicit
operator-supplied action the check stays blocked; destructive actions fail.

## 7. Retention

Retain the old Qdrant volume (`qdrant-storage`) and exports through an
explicit recovery/retention window. Permanent deletion is a separate
exact-resource operator action after export/restore verification — never an
automatic normal-upgrade step. Preserve PostgreSQL, MinIO, secrets,
workspaces, unrelated containers, and Omnigent state.

## 8. Rollback

Rollback uses the previous matching application/Compose/image revision with
preserved data after a schema-compatibility check, rehearsed against
fixtures. Never add an optional Qdrant profile or adapter back to the new
release as a rollback feature.

Hermetic rehearsal: `--mode rollback-check` (default scope rehearses the
matching-revision path; Qdrant-profile scopes are refused).

## 9. Evidence for #4103/#4114 (no over-claiming)

Publish the exact rehearsal evidence (gate JSON, sanitized preflight
report, preservation verification record, retirement absence verification,
rollback rehearsal log) plus the remaining operator-only steps, to
#4103/#4114. Distinguish automated fixture verification from protected
deployment checks and real operator actions not performed. Never claim
every real installation was upgraded. #4114 consumes this report without
circular dependencies: this runbook's fixtures do not require #4114.

## 10. Archive/delete triggers (temporary inventory)

This file and any temporary inventory it references live under `docs/tmp/`
or the issues. Archive or delete when ALL hold:

- the integrated rehearsal evidence is published to #4103/#4114,
- every documented consumer drained (finite retirement conditions met),
- obsolete new-release compatibility scaffolding removed,
- the retention window closed with an explicit exact-resource disposition.

Until then this runbook stays Proposed and unarchived.
