# Milestone A Closed Record — MoonLadderStudios/MoonMind#4189 (MR2)

Parent: #4187. Plan: `docs/tmp/ManifestSystemRemovalPlan.md` §MR2 (baseline
`7bd159dafb44770278e8c5563d47667de6e7c491`). This is temporary execution
scaffolding under `docs/tmp/`; delete or archive it when the A/B children,
milestone C, and the retention window below are resolved.

Status: **closed**. The deletion child (MR3) and the migration child (MR4)
may consume this record without waiting for milestone B's rehearsal evidence
or milestone C's operational closure. Downstream dependence is on A/B as
applicable, never on this issue's final operational closure.

## 1. Both historical contracts

At plan baseline, `workflows/temporal/workflows/manifest_ingest.py`
distinguished two persisted input meanings, and the new release must never
reinterpret one as the other:

- `manifest_ref` compile/summary contract
  (`{"manifest_ref": manifest, **({"action": action})}` with `action` in
  `(None, "run")`), and
- `manifestArtifactRef` node-execution contract
  (`{"manifestArtifactRef": manifest_ref, ...}` with `failurePolicy`
  `best_effort`/`fail_fast`).

Read-compat pins: `api_service/api/routers/executions.py`
`_normalize_entry_value`/`_resolve_execution_entry` resolve both old shapes
to `entry="manifest"`; `manifest_status=None` lineage fallback preserves
`manifest_ref`/`plan_ref` for authorized readers. Regression:
`tests/integration/workflows/temporal/test_manifest_registration_boundary.py`
(both contracts decode) and
`tests/unit/api/routers/test_manifest_historical_reads_4189.py` plus
`test_manifest_historical_reads_projection_4189.py` (ORM-decoded rows
through the full detail/list serializers, degraded lifecycle values, lost
summary stays unavailable).

## 2. Control/effect owners and the read/admission split

Historical reads are independent of current launchable types within the
existing owners; no owner was added and no second migration framework was
created:

- Registration/projection: `moonmind/workflows/temporal/workflow_registry.py`
  (`product_workflow_types() == ("MoonMind.UserWorkflow",)`; `ManifestIngest`
  absent; unknown types stay closed via `require_product_projection`).
- Admission: `CreateExecutionRequest` schema (earliest shared boundary),
  `TemporalExecutionService.create_execution` (rejects `MANIFEST_INGEST`
  before artifact/reader/Temporal effects), `send_update` (rejects
  `RETIRED_MANIFEST_UPDATE_NAMES`: `UpdateManifest`, `SetConcurrency`,
  `CancelNodes`, `RetryNodes` before source load). Rerun, typed/failed-step
  recovery, drafts, presets, recurring targets, and integration callbacks
  all converge on these boundaries (producer ledger rows 4–8, 10–12, 16).
- Read path: executions list/detail serializers under the current
  owner/raw-access policy (raw lineage refs for admins only); generic
  bounded/inert artifact presentation; no compiler, retired parser, reader,
  or live-host dependency. Unavailable evidence stays unavailable.
- Producer inventory ownership: `docs/tmp/ManifestProducerLedger-4188.md`
  (#4188/MR1 rows 1–20).
- Drain ownership: `moonmind/gates/manifest_ingest_drain.py`
  (`manifest-ingest-removal-drain-v1`; fail-closed: unobservable is
  outstanding, never zero).
- Registry preservation ownership:
  `moonmind/gates/manifest_registry_migration_4191.py` +
  `tools/manifest_registry_export_4191.py` +
  `376_drop_manifest_registry_4192` (see
  `docs/tmp/ManifestRegistryDisposition-4191.md`).

Retirement-critical obligations transferred from #3948 (constraints on
REQ-01/02/07, not prerequisites): both input meanings above; exact
owner/artifact authorization; child and primary-result evidence; no
repeated completed effects when summary writing fails; correct
cancellation/retry disposition of already-admitted work. The new release
does not claim compilation was ingestion and does not reinterpret
historical results.

## 3. Bounded cutover choice (no replacement framework)

Preferred cutover, per deployment (three independently operated
deployments, each with its own observations — never inferred from another
device): stop producers first (ledger rows 1–4, 9, 12–15); let identified
work finish on the existing matching release or cancel it deliberately
with owned finish-versus-cancel records (exact old image/worker/queue,
expected terminal evidence, child/effect/cleanup disposition); recheck
after producer shutdown so races do not invalidate an earlier empty
inventory; then deploy the release without Manifest support. A generic
system pause may exclude Manifest work, and parent closure is not proof
children/external work stopped. If live counts are zero, skip temporary
drain machinery and ship the cohesive removal. If version routing is
genuinely needed, it uses already-supported mechanisms with exact
isolated consumers and a finite removal condition — never an
undifferentiated shared queue, a permanent compatibility worker, or
successful no-op Activity results.

Direct Temporal administration (starts/resets outside HTTP) is outside
HTTP-validator scope and needs an explicit operator cutover policy owned
by each deployment's operators; HTTP retirement alone cannot disable
privileged Temporal access. Per-deployment preflight (bounded, read-only,
via existing deployment/Temporal/state owners) and milestone C
authorization remain operator-gated pending work, not code-acceptance
failures.

## 4. Integrated rehearsal + rollback/restore procedure (with migration child)

Execution owner: the migration child with deployment authority; **not
executed in this change**. Procedure, in order:

1. Stage old definitions and records on the pinned old release; confirm no
   active producers (drain gate re-probe).
2. Protected export with `tools/manifest_registry_export_4191.py` into an
   operator-owned directory (0700/0600); verify row counts plus
   content/digest restore before any destructive schema change. Never
   commit exports, never publish raw YAML/histories/credentials/dumps.
3. Deploy the removal; exercise historical list/detail/artifact reads and
   normal-workflow smoke; run representative replay/drain fixtures on the
   pinned old release separately from new-release historical reads.
4. Compatible rollback (before destructive schema changes) uses the matching
   compatible release. After the irreversible boundary
   (`376_drop_manifest_registry_4192`), schema downgrade alone stops
   actionably — recovery requires verified protected restoration or
   forward repair without overwriting unrelated newer work.
5. Retain the pinned old release and protected evidence for the agreed
   recovery window without retaining executable support in the new product.

## 5. Retention and temp-control removal

Retain this record, the producer ledger, the registry disposition, and the
residual notes under `docs/tmp/` until the A/B children, milestone C
dispositions, and the agreed recovery window are all resolved. Remove
temporary controls/fixtures/scaffolding only after their documented final
consumers and retention obligations end; durable read/security rules stay
in their canonical owners.
