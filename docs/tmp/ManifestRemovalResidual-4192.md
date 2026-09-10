# Manifest Removal Residual-Reference Disposition (MR5, #4192)

Parent: MoonLadderStudios/MoonMind#4187. This note dispositions every
remaining first-party `manifest` match after the MR5 candidate, per issue
requirement 9. Method: case-insensitive first-party grep over `moonmind/`,
`api_service/`, `tools/`, `scripts/`, `frontend/src`, `docs/` (excluding
`docs/tmp/` migration notes themselves and third-party lockfile content),
plus the generated OpenAPI bundle. Matches are classified below as
**unrelated retained system**, **immutable/historical evidence**,
**negative test**, or **bounded migration note**. Nothing here justifies a
live import dependency: no shipped dependency, configuration, build input,
generated contract, example, CLI help, or active document requires or
advertises the native Manifest/RAG ingestion product.

Head: `3884dc77f` plus the MR5 remediation pass (this change).
Status rule: no native Manifest/RAG ingestion product; existing normal
workflows/Skills and authorized artifact handoffs retained.

## 1. Fixed in this pass

- Live supported-catalog removal: `MoonMind.ManifestIngest` dropped from
  `SUPPORTED_WORKFLOW_TYPES` (`moonmind/schemas/temporal_models.py`) and
  `_SUPPORTED_RECURRING_WORKFLOW_TYPES`
  (`api_service/services/recurring_workflows_service.py`). New launches are
  rejected actionably at the schema boundary
  (`CreateExecutionRequest._validate_required_fields`), the recurring
  boundary (`_normalize_target`, `_workflow_bundle_for_target`), and the
  service boundary (`TemporalExecutionService.create_execution`,
  `send_update` retired-update rejection). These rejections are the #4189
  cutover contract, not product surface.
- `frontend/src/generated/openapi.ts` regenerated through its owners
  (container-exported app spec + `openapi-typescript` via
  `tools/generate_openapi_types.py` toolchain). Stale
  `ManifestRunOptions` / `ManifestNodeCountsModel` /
  `ManifestExecutionPolicyModel` / `ManifestRunQueueMetadata` /
  `ManifestRunRequest` / `ManifestRunResponse` blocks are gone; the live app
  spec carries zero `Manifest*` schemas and zero `/manifest*` paths. The
  regen also picked up unrelated pending drift (routes added since the last
  generation); all of it comes from the live app spec. Remaining
  `manifestArtifactRef` fields are §2 retained systems.
- Stale tests updated to the retired posture:
  `test_product_projection_policy_covers_actual_registered_classes` (product
  scope is `MoonMind.UserWorkflow` only),
  `test_cli_help_advertises_no_manifest_command_group` and
  `test_cli_help_does_not_advertise_retired_vector_backend` (pin the #4192
  retirement notice instead of a zero-substring assertion),
  `test_prerequisites_admission_landed_and_owner_live_blocked` and
  `test_inventory_survey_names_no_live_surfaces_and_preserves_fixtures`
  (vector-free checkout reports handler-removal completed; preserved
  evidence is the two in-code entry contracts).
- Rehearsal-tool fix (`tools/qdrant_cutover_rehearsal.py`):
  `check_upgrade_fixture` probes the vector-free boolean instead of
  substring-matching its own `retired-absent` evidence prose, and checks the
  two in-code historical entry contracts instead of removed
  `type: qdrant` file fixtures. The inventory survey records the deleted
  native-RAG modules (`moonmind/rag/settings.py`,
  `api_service/api/routers/retrieval_gateway.py`) as `retired-absent`
  vector-free evidence instead of failing them as unreadable sources.
- Active-doc reconciliation: `docs/Api/ExecutionsApiContract.md`,
  `docs/Temporal/WorkflowSchedulingGuide.md`,
  `docs/Temporal/TemporalScheduling.md`,
  `docs/Temporal/WorkflowExecutionProductModel.md`,
  `docs/Temporal/TemporalPlatformFoundation.md`,
  `docs/Temporal/WorkflowLanguageHardSwitchPlan.md`,
  `docs/Temporal/SourceOfTruthAndProjectionModel.md`,
  `docs/UI/WorkflowConsoleArchitecture.md`, `docs/UI/WorkflowsListPage.md`,
  `docs/UI/RecurringSchedulesPage.md`, `docs/MoonMindArchitecture.md`, and
  `frontend/src/entrypoints/schedules.tsx` page copy no longer advertise
  ManifestIngest as submittable, schedulable, or routable.

## 2. Unrelated retained systems (not ingestion support)

These contain the word `manifest` but are different products. Owner and
removal trigger: only with the owning system itself; never as Manifest-removal
follow-up.

- Step Execution manifests: `StepExecutionSummaryRefModel`,
  `StepExecutionDetailModel`, `StepExecutionBranchMetadataModel`
  (`moonmind/schemas/temporal_models.py`), step-execution projection/detail
  payloads (`api_service/api/routers/executions.py`), and the surviving
  `manifestArtifactRef` / `manifestRefs` fields in `ExecutionModel`,
  `StepExecutionDetailModel`, `StepExecutionProjectionModel` in the
  regenerated OpenAPI bundle. Owner: step-ledger/checkpointing.
- Skill manifests: `manifestRef` / snapshot fields in skill resolution,
  materialization, and registry code (`moonmind/services/skill_*.py`,
  `moonmind/mcp/*registry*.py`). Owner: Skill system.
- Workspace checkpoint capture manifests (`capture-manifest`,
  `capture_workspace_checkpoint` activities and
  `LoadingPlaceholder` generic surfaces). Owner: checkpointing.
- Recovery manifests (`recoveryManifest`, `failedRunRecoveryManifestRef`,
  evidence `retrievalManifestRef` / `memoryManifestRef`). Owner:
  recovery/evidence contracts.
- Build manifests: Vite asset manifest and `tools/verify_vite_manifest.py`,
  container image/runtime manifests, `manifests-filter-grid` CSS hook reused
  by the schedules page (`frontend/src/styles/dashboard.css`,
  `frontend/src/entrypoints/schedules.tsx`). Owner: build/frontend.
- Dead UI plumbing with no live route: `manifest` icon key and `manifests`
  section label (`frontend/src/components/DashboardSystemMenu.tsx`),
  `manifests` loading surface
  (`frontend/src/components/dashboard/LoadingPlaceholder.tsx`). No
  destination uses them and `/manifests` resolves to null (pinned by
  `frontend/src/lib/dashboardRoutes.test.ts`). Owner: dashboard; remove only
  with an owning dashboard cleanup, not as ingestion work.
- Router alias forwarding: `_build_recurring_target`
  (`api_service/api/routers/executions.py`) still forwards a
  `manifestArtifactRef` alias into the recurring target dict; the service
  boundary ignores it for `UserWorkflow` and rejects `ManifestIngest`
  before dispatch. Pinned by
  `test_create_recurring_schedule_accepts_snake_case_target_aliases`
  (alias normalization, not live scheduling). Owner: executions router.
- `ExecutionModel.manifestArtifactRef` historical lineage fallback
  (`api_service/api/routers/executions.py`, reading `manifest_ref` off
  old-release rows when no manifest status snapshot exists). Read-only drain
  support, not a launch path. Owner: executions projection.

## 3. Immutable/historical evidence (read-only, never rewritten)

Per #4189: old-release replay/drain evidence may retain the historical
strings; the new release must not register or launch the feature. Never edit
these to get zero matches.

- `TemporalWorkflowType.MANIFEST_INGEST` enum member
  (`api_service/db/models.py`) and the `WORKFLOW_ENTRY_BY_TYPE` /
  `WORKFLOW_ENTRY_BY_TYPE` `manifest` mappings
  (`moonmind/workflows/temporal/service.py`,
  `api_service/core/sync.py`). Required to load, project, and list
  old-release rows; unreachable for new launches (creation rejects before
  the mapping). Removal trigger: a versioned DB migration with an explicit
  cutover plan — out of scope for MR5 and forbidden without one.
- Historical-read tests and fixtures:
  `_insert_historical_manifest_ingest_record`,
  `test_describe_historical_manifest_execution_exposes_lineage_refs_only`,
  dependency-rejection tests naming ManifestIngest,
  `historical_manifest_entries()` and replay checks in
  `tools/qdrant_cutover_rehearsal.py`. Owner: respective test modules.
- Migration files naming the historical enum
  (`api_service/migrations/versions/0b8e4befb8e5_initial_clean_migration.py`,
  `376_drop_manifest_registry_4192.py` which drops the registry). Alembic
  history is immutable.
- `scripts/migrate_to_temporal_schedules.py` historical mapping. Owner:
  migration scripting; archived with the migration window.
- `docs/DocsReview.md` review table rows for the deleted
  `Rag/LlamaIndexManifestSystem.md` / `Rag/ManifestIngestDesign.md`. A
  point-in-time review; editing it would falsify history.
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §11.2 retired
  lifecycle (kept by design; the section number preserves cross-references).
- `docs/tmp/QdrantCutoverRunbook-4115.md`,
  `docs/tmp/QdrantDocsResidual-4113.md` (bounded migration notes, see §5).

## 4. Negative tests (prove absence; keep green)

- Retired-launch/update rejections:
  `test_create_execution_rejects_retired_manifest_ingest`,
  `test_retired_manifest_update_is_rejected_actionably`,
  recurring retired-target rejection
  (`test_create_definition_rejects_retired_manifest_ingest`).
- Retired-route absence: `test_retired_manifest_status_route_is_gone`,
  `test_retired_manifest_nodes_route_is_gone`,
  dashboard `/manifests` null-route assertion.
- Zero-distribution/zero-wiring guards:
  `test_dependency_removal_landed_no_manifest_only_distributions`,
  `test_tool_manifest_guard_rejects_leaked_retired_descriptor`,
  compose/qdrant-absence and old-env-inert tests.
- Manifest-coupled suites stay deleted: `test_manifests.py`,
  `tests/unit/manifest`, `tests/unit/rag`, `manifests.test.tsx` remain
  absent. Removal trigger for this section: none — these are permanent
  regression guards.

## 5. Bounded migration notes (docs/tmp only)

- This report (`docs/tmp/ManifestRemovalResidual-4192.md`). Retention: until
  MR5 acceptance closes; then archive per repo tmp hygiene. Owner: MR5 (#4192).
- `docs/tmp/QdrantCutoverRunbook-4115.md`,
  `docs/tmp/QdrantDocsResidual-4113.md`,
  `docs/tmp/QdrantRemovalInventory-4105.md`. Retention: recovery window plus
  operator sign-off per #4115; never promoted to canonical docs.

## 6. Blocked prerequisite (environment, not code)

- `poetry lock --no-update` regeneration (rw4): blocked in this sandbox —
  no `poetry` binary and PyPI is unreachable (`403` via proxy), so the
  lockfile content-hash stays stale per its header note. The package set
  itself carries no llama-index/reader/qdrant-client distributions (only the
  header comment names them). Owner: whoever runs dependency/image
  qualification with network access (#4111 ownership for images). Resume
  check: run `poetry lock --no-update`, then verify clean installs and
  rebuilt images contain no Manifest-only distribution and record the
  resolved graph plus surviving consumers.
