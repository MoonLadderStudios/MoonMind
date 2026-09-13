# Manifest Producer/Disposition Ledger — MoonLadderStudios/MoonMind#4188 (MR1)

Parent: #4187. Plan: `docs/tmp/ManifestSystemRemovalPlan.md` (baseline
`7bd159dafb44770278e8c5563d47667de6e7c491`; rechecked at `5fd5482`).
Scope: every entry that could create or reactivate native Manifest work,
including the vector-free `MoonMind.ManifestIngest` successor. Normal
workflows and generic artifact uploads remain available.

Disposition vocabulary: **removed** (surface deleted; ordinary
unsupported-route behavior), **reject-before-effects** (explicit retired
intent fails at the earliest existing unsupported-type/capability boundary
before registry mutation, artifact creation/processing, reader/network
access, compile, Temporal start, host launch, or child work), **read-only
history** (old evidence stays readable; rerun/retry/update/resume/reset
affordances removed or rejected), **retire-pause-skip** (old schedules
paused or skipped with protected evidence; never converted to
UserWorkflow).

| # | Source symbol | Role | Persisted representation | Authority / effect boundary | Disposition + implementing child | Test obligation |
|---|---|---|---|---|---|---|
| 1 | `api_service/api/routers/manifests.py` (`/api/manifests`, `/api/manifests/{name}`, run submission, worker state callback) | producer + control | Manifest registry rows, YAML artifacts | REST ingress → `manifests_service` → Temporal start | **removed** (#4192/MR3 landed; absent at `5fd5482`; final routes return ordinary 404, no deprecation stub) | `test_manifest_retirement_qualification_4193.py` no-reintroduction guard; served-API 404 check in verifier |
| 2 | `api_service/services/manifests_service.py`, `manifest_sync_service.py` | producer | registry mutation, YAML artifact creation, execution invocation | service effect boundary | **removed** (absent at `5fd5482`) | guard scan; ingress tests prove no artifact/registry side effect on rejected intent |
| 3 | `api_service/api/schemas.py` Manifest models + router registration in `api_service/main.py` | producer contract | request/response schemas | app mount | **removed** (no manifests mount; only comment ref to #4129 removal manifest) | guard (`manifests_router` marker); OpenAPI regeneration check |
| 4 | Shared `POST /api/executions` authoring — `CreateExecutionRequest.workflow_type` (`moonmind/schemas/temporal_models.py:2617`) | producer | execution row, immutable payload | schema validation (earliest shared boundary) | **reject-before-effects**: `MoonMind.ManifestIngest` raises `ValueError` with retired message | `tests/unit/api/routers/test_executions.py` retired-type cases; contract tests |
| 5 | `TemporalExecutionService.create_execution` (`moonmind/workflows/temporal/service.py:2106`) | producer | workflow start, artifact-ref reads | service admission before readable-artifact validation / Temporal start | **reject-before-effects** (`TemporalExecutionValidationError`, retired message) | `test_temporal_service.py` retired-creation cases |
| 6 | `TemporalExecutionService.update_execution` retired updates (`RETIRED_MANIFEST_UPDATE_NAMES`: `UpdateManifest`, `SetConcurrency`, `CancelNodes`, `RetryNodes`) | control | update/patch application | update boundary before source-execution load | **reject-before-effects** | `test_executions.py:15630` retired-update case |
| 7 | Rerun / retry-publication / reset / resume / signal / cancel (`executions.py:18372`, `:18767`, `service.py:3036,3538`, `executions.py:18216,18256`) | producer + control | new execution from copied historical inputs | shared launch handoff (converges on `create_execution`) | **reject-before-effects** for retired source types; historical rows stay **read-only history** | rerun-with-`ManifestIngest`-source rejection test (verifier-owned ingress journey) |
| 8 | Inline/registry submission shapes: `manifest_ref`/`manifestRef` compile vs `manifestArtifactRef` node; `manifest_artifact_ref` param | producer (old) / read (new) | stored immutable inputs, lineage | projection/decode boundary | **read-only history**: `_normalize_entry_value`/`_resolve_execution_entry` resolve `entry=manifest` + `ManifestIngest` workflow type for old rows; `manifest_status=None` lineage fallback (`executions.py:3962`); optional ref field parses but is ignored for new launches | historical-decoder tests; degraded-status regression (blank/unknown `mm_state`) |
| 9 | `moonmind/cli.py` + `moonmind/manifest/manifest_cli.py` command group | producer | CLI launch | CLI ingress | **removed** (no `manifest` group; help text pins retirement `#4192`) | installed-CLI help test: `manifest` group absent, ordinary worker/container commands intact |
| 10 | Goal-preset expansion (`_expand_goal_preset_for_workflow_submission`, `AuthoringSurface.preset_expansion`, `api_service/services/presets/`) | producer | preset slug → workflow payload | preset-expansion authoring surface | **reject-before-effects** for retired intent; presets expand to ordinary `UserWorkflow` only, never silently rewritten Manifest | preset-expansion tests (ordinary presets pass; retired intent rejected) |
| 11 | Saved drafts / copied historical inputs (`draft` snapshot, `original_task_input_snapshot_missing`, stale-draft guards `executions.py:1393,11396`) | producer | draft artifact payload | submission re-validation | **reject-before-effects** with actionable correction; stale drafts never reintroduce retired authority | draft-resubmission test with retired shape → actionable error, no launch |
| 12 | Recurring definitions — create/update (`_normalize_target`, `_workflow_bundle_for_target` in `recurring_workflows_service.py:247,521`) | producer | `RecurringWorkflowDefinition.target.workflowType` | validation before persistence/dispatch | **reject-before-effects** (`RecurringWorkflowValidationError`, retired message) | `test_recurring_workflows_service.py` ManifestIngest rejection cases |
| 13 | Recurring reconcile/trigger/backfill/catch-up/resume (`reconcile`, trigger/backfill paths, `recurring_workflows_service.py:1572`) | control | Temporal Schedule action, enqueued starts | adapter boundary | **retire-pause-skip**: ManifestIngest-targeted schedules are paused (never recreated/updated); already-created executions require explicit cancel — pause ≠ proof of stop | schedule-retirement tests; ordinary schedules still run |
| 14 | `scripts/migrate_to_temporal_schedules.py` `manifest_run` conversion (`_workflow_type_for_target`) | control (migration) | `RecurringWorkflowDefinition.target.kind`, `temporal_schedule_id` | migration ingress before `adapter.create_schedule` | **retire-pause-skip**: `manifest_run` raises `RetiredManifestTargetError`; loop logs warning and `continue` — no Temporal Schedule creation, no `temporal_schedule_id` mutation, no conversion to `MoonMind.Run` (#4188 fix) | `tests/unit/scripts/test_migrate_to_temporal_schedules_4188.py` |
| 15 | Direct Temporal schedule administration | control | Temporal Schedule objects | operator trust boundary | out of HTTP-validator scope; handled by MR2 cutover/drain gate (`moonmind/gates/manifest_ingest_drain.py`, `manifest-ingest-removal-drain-v1`) | per-deployment drain evidence (MR2/#4189), not an HTTP unit test |
| 16 | Integration callers (`execution_integrations.py` callback ingest, `describe_integration_callbacks`) | producer | integration callback payloads | callback ingress → shared launch | **reject-before-effects** via shared `create_execution` boundary; Manifest worker state callback **removed** with dedicated router | integration-callback tests with retired type → rejected, no child work |
| 17 | Frontend authoring (`frontend/src/entrypoints/manifests.tsx`, `dashboard-app.tsx` lazy imports, nav) | producer | browser authoring state, `entry=manifest` list filter | client routing | **removed** (entry absent; no `/manifests` nav/lazy import). Residuals are non-authoring: `dashboardRoutes.ts:44` icon-union member, `workflow_console.py:88` reserved segment, `schedules.tsx:1902` CSS class, `entry=manifest` as historical-read fixture | frontend route/bundle tests; `workflow-list.test.tsx` historical-read fixture stays |
| 18 | `api_service/api/routers/workflow_console.py` legacy `task_manifest_submit_route` (`/manifests/new` → `/manifests` 307) | producer | browser redirect | server route registry | **removed** (no Manifest UI route; final behavior is ordinary unsupported-route) | route-absence test |
| 19 | Persistence (`ManifestRecord`/`manifest` table; migration `376_drop_manifest_registry_4192`) | store | registry rows | Alembic upgrade | **removed after protected export** (export-before-upgrade notice; historical YAML/version/state in operator-controlled storage, never in issues/Temporal history) | fresh-install + old-DB-upgrade tests; former executions remain readable |
| 20 | Ordinary-content allowlist (user `manifest.yaml` uploads, Skill/snapshot manifests, recovery/checkpoint manifests, Vite manifests, `stepExecutionManifestRef`/`recoveryManifest`) | non-target | arbitrary user files | upload/parse boundaries | **preserved**: retirement targets retired application contracts only, not filenames/prose/artifact contents | `check_user_manifest_allowed` guard; generic-upload acceptance test |

Notes:

- Rejection reuses the existing error vocabulary (`MoonMind.ManifestIngest
  was retired (MoonLadderStudios/MoonMind#4192)…`); no second admission
  engine was added.
- Cached browser state, copied historical inputs, explicit
  `MoonMind.ManifestIngest`, and legacy Manifest target/entry forms all
  converge on rows 4–8, 11–12 and fail visibly instead of running
  different work.
- Ordinary CLI commands, runtime/profile choice, credentials, explicit
  context, and arbitrary `manifest.yaml` files are unaffected (row 20;
  coordinate #3939 normal-workflow CLI, #4112 vector CLI without
  rebuilding either).
