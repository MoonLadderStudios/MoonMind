# Manifest System Removal Plan

Status: Proposed implementation plan. This document does not implement removal, change existing issues, authorize a deployment, or authorize deletion of operator data.

Reviewed: September 9, 2026. Source baseline: `7bd159dafb44770278e8c5563d47667de6e7c491` on `main`. Repository searches used the immediately preceding indexed revision and targeted source reads were pinned to the baseline. Re-audit callers and overlapping PRs at the implementation revision.

This is temporary execution scaffolding under `docs/tmp/`, as required by [AGENTS.md](../../AGENTS.md). Keep lasting product rules in the existing canonical architecture, execution, context, and operator documents when their implementations change. Delete or archive this plan once implementation and the bounded cutover are complete.

## 1. Decision and target state

Remove the complete native **Manifest product**, including its vector-free successor. MoonMind no longer owns a declarative source-ingestion, transformation, evaluation, or Manifest-to-workflow subsystem. This is feature retirement, not a rename, optional integration, external endpoint for the same backend, or another generic pipeline framework.

The September 7 Qdrant removal epic deliberately preserved generic ManifestIngest. This decision supersedes that preservation requirement. It does not supersede the epic's requirements to preserve ordinary orchestration, authorized context, durable evidence, or operator-controlled data. [S1]

After removal:

- There is no Manifest page, registry, authoring schema, CLI command group, run submission path, ingestion pipeline, or registered `MoonMind.ManifestIngest` workflow.
- Existing normal workflow and Skill interfaces remain the way to submit work. Generic child workflows, dependency graphs, schedules, and artifact handoffs remain where they already serve supported work. No replacement Manifest abstraction is required.
- MoonMind does not ship or initialize a native RAG/indexing platform. Independently managed retrieval can be used through an already-supported, explicit, scoped Skill, tool, or artifact interface. Building or bundling such a replacement is not part of this project.
- Historical executions and artifacts remain readable through authorized generic evidence surfaces. Historical readability does not make the retired workflow launchable, editable, rerunnable, or resumable in the new release.
- Default startup and ordinary workflows need no Manifest configuration, source-reader credentials, embedding credentials, or feature-disable flag.

## 2. What the review established

### The vector-free rewrite has not removed the product

The current operator guide calls the system vector-free and retains source readers, transforms, evaluation, local execution, compilation, and Temporal execution. The current pipeline rejects retired vector blocks but still owns reader dispatch, fetch/transform results, HTML conversion, chunking, and metadata enrichment. Removing Qdrant alone therefore does not achieve this request. [S2, S3]

### The removal crosses shared application boundaries

`ManifestsService` owns registry operations, creates YAML artifacts, submits a Temporal execution, bootstraps compilation, and updates Manifest-specific projection metadata. The REST router exposes registry reads/writes, run submission, and a worker state callback. The frontend queries the shared executions API using `entry=manifest`, so removing the dedicated router alone is insufficient. The shared Temporal lifecycle service also imports Manifest models and projection helpers. [S4, S5, S6, S7]

### There are two historical execution contracts

The canonical registered class explicitly distinguishes `manifest_ref`, which compiles and summarizes, from `manifestArtifactRef`, which orchestrates nodes. It rejects inputs containing both. The class includes patch markers for historical commands and recurring scheduled starts. Do not reinterpret one input as the other or replace the class with a successful no-op. [S8]

Issue #3948 still requests work on public compile/execute semantics, authorization, node mapping, and child terminal evidence. Full feature-completion work is no longer the desired investment. Preserve only the safety obligations required for the selected bounded retirement path. [S9]

### Registration removal can affect historical visibility

`workflow_registry.py` derives product workflow types and projection scope from registered classes. `TemporalExecutionService` consumes those helpers. Removing the Manifest registration must not inadvertently hide old evidence or cause unrelated execution listing to fail. Historical decoding/visibility and new execution admission need separate decisions at the existing boundaries. Do not keep an executable workflow registered merely to display its name. [S7, S10]

### Packaging cleanup is still material

At the reviewed revision, `pyproject.toml` still describes a RAG application and declares `llama-index`, several LlamaIndex reader packages, and `qdrant-client`. This review does not claim the Qdrant epic is complete. Coordinate final dependency removal with #4111 and recheck actual surviving imports before deleting shared libraries. [S11]

## 3. Scope and concrete change map

Paths below are source-backed starting points, not a claim that search returned every caller. The implementation must produce a final caller/disposition audit at its own revision.

| Area | Remove or simplify | Preserve |
| --- | --- | --- |
| Manifest implementation | `moonmind/manifest/`, including loader, interpolation, validator, runner, sync, pipeline, adapters, reader registry, CLI helpers, and Manifest-owned evaluation | A utility only when a concrete supported non-Manifest caller requires it. Move that smallest utility to its existing owning module rather than keeping the package |
| Authoring schemas | `manifest.schema.json`, `moonmind/schemas/manifest_models.py`, `manifest_v0_models.py`, `manifest_ingest_models.py`, and their re-exports | Generic plan, artifact, runtime, Skill, and recovery schemas |
| Submission contracts | `moonmind/workflows/executions/manifest_contract.py`, `manifest_errors.py`, and package exports | Shared authentication, authorization, credential references, security scanning, and ordinary workflow validation |
| Registry/API | `api_service/api/routers/manifests.py`, `api_service/services/manifests_service.py`, `manifest_sync_service.py`, Manifest models in `api_service/api/schemas.py`, and router imports | Generic execution and artifact APIs. Remove only Manifest branches from shared routers |
| Temporal implementation | `moonmind/workflows/temporal/workflows/manifest_ingest.py`, Manifest-only helpers in `temporal/manifest_ingest.py`, `TemporalManifestActivities` and Manifest bindings in `activity_runtime.py` | Normal UserWorkflow execution, child workflow primitives, artifact Activities, and runtime workers |
| Shared orchestration | Manifest entries/imports in `workflow_registry.py`, `worker_runtime.py`, `activity_catalog.py`, `temporal/__init__.py`, and Manifest branches in `service.py` and shared execution schemas | Supported product/operator workflows, existing status taxonomy, authorization, retry/cancel, and generic historical metadata |
| Dashboard | `frontend/src/entrypoints/manifests.tsx`; registrations in `dashboard-app.tsx`, `frontend/src/lib/dashboardRoutes.ts`, and `api_service/api/routers/workflow_console.py`; related boot payloads, capabilities, filters, controls, styles, and tests | General workflow list/detail and artifact viewing. Keep unrelated dashboard pages and shared UI components |
| CLI/configuration | Manifest commands and imports in `moonmind/cli.py`; `TEMPORAL_MANIFEST_CONTINUE_AS_NEW_PHASE_THRESHOLD` in settings and constructor callers | Non-Manifest CLI groups and UserWorkflow continuation controls |
| Scheduling | Manifest target support, saved targets, and the `manifest_run` conversion in `scripts/migrate_to_temporal_schedules.py` | Scheduling of supported ordinary workflows. Do not silently convert retired schedules into ordinary runs |
| Persistence | `ManifestRecord` and its `manifest` table after preservation and migration gates; Manifest-only fields in shared execution records only after a separate historical-read decision | Shared execution records, immutable payloads, ownership, workflow/run IDs, lineage, timestamps, artifact links, and recovery evidence |
| Docs/build/test assets | Dedicated Manifest docs, examples, generated API/catalog entries, CLI help, Manifest-only tests and fixtures once cutover obligations end | Generic database guidance even when located under `docs/Rag/`, Vite asset-manifest infrastructure, generic security and reliability tests |

### Explicit exclusions: do not delete by the word “manifest”

Retain failed-run recovery manifests, saved-work and checkpoint manifests, Skill snapshot manifests, tool/capability inventories, Omnigent effective-capability manifests, Vite build manifests, image/runtime provenance, and GitHub App installation manifests. In particular, `moonmind/workflows/temporal/recovery_manifest.py`, `moonmind/omnigent/effective_capabilities.py`, and `tools/verify_vite_manifest.py` are not the ingestion product.

A user repository may contain a file named `manifest.yaml`, and a normal workflow may legitimately read or produce it. Retirement validation targets MoonMind's retired execution contracts and package ownership, not arbitrary filenames, user prose, or artifact contents.

## 4. Implementation work packages

### MR1. Retire producers and remove authoring surfaces

Inventory and change every entry that can create Manifest work: the dedicated API, shared execution submission, inline/registry UI submission, CLI, saved definitions, recurring schedules, trigger/backfill paths, clones/reruns, updates, presets, and any integration caller discovered during the audit. Check direct Temporal schedule actions as well as API-mediated submissions.

Use the existing unsupported-workflow/capability validation boundary. During cutover, reject explicit new Manifest intent before registry mutation, manifest processing, reader/network access, artifact creation, Temporal start, host launch, or child work. Do not strip the requested feature and report success as an ordinary workflow. Keep generic artifact upload usable for arbitrary user files.

Remove `/manifests` and `/manifests/{name}` from both server and client route registries, navigation, lazy page imports, and page/boot types. Remove Manifest-specific controls from shared workflow detail and creation surfaces. Retire `/api/manifests` and its state callback in the coordinated release. Ordinary unsupported-route behavior is sufficient in the final application. A temporary retirement response is justified only by an identified cutover consumer, not permanent compatibility policy.

Inspect saved drafts and schedules for both workflow-type and legacy target representations. Preserve a private export before changing persisted definitions. Disable retired producers with a clear reason and no automatic reactivation. Old browser state and explicit API payloads must fail visibly rather than discard required semantics. Do not silently rewrite a Manifest schedule as UserWorkflow.

Acceptance: every new-write/trigger path rejects retired work without side effects, removed pages are absent from navigation and bundles, and ordinary creation/scheduling remains usable without new configuration.

### MR2. Preserve historical reads and control the cutover

Start this work alongside MR1, before deleting handlers or enum values. Reuse existing Temporal, artifact, deployment, and Qdrant-cutover owners. Do not create a new migration service, coordinator, or permanent retirement framework.

Inventory each deployment separately. Include all three independently operated MoonMind deployments and every affected namespace/task queue actually found. Do not infer shared state or successful drainage from another device. Repository review cannot establish these live counts.

The private inventory must cover active parents, sleeping/scheduled executions, pending and retryable Activities, children, outstanding controls, recurring producers, stored definitions, registry rows, and evidence references. Identify both persisted Manifest input contracts and any older stored job payloads actually present. Missing inventory is unknown, not zero.

**Preferred cutover:** stop producers, let identified work finish on the existing matching release or cancel it deliberately, verify the children and outstanding work, then deploy the release without Manifest support. Do not assume a general system pause affects Manifest workflows or that canceling a parent has stopped every child/external effect.

If live counts are zero, skip temporary drain machinery and ship the cohesive removal directly. If an existing versioned-worker mechanism is genuinely needed, name its owner, exact consumers, image revision, isolation/routing, and finite removal condition. Do not run incompatible old/new workers against an undifferentiated shared queue. Old executable support belongs to the bounded old-release path, not the target application.

Preserve the existing compilation/node history fixtures for the cutover rehearsal. Test either safe compatibility at the transition or explicit old-release drain. Once the workflow is removed, do not claim the new release replays it; retain the pinned old release and fixtures for the agreed recovery window instead of shipping the class indefinitely.

For closed executions, retain authorized generic list/detail and artifact reads from stored immutable identity/evidence. Make these reads independent of launchability. Remove rerun, retry-node, update, resume, and reset affordances that would recreate the retired feature. A historical response decoder must not grant system ownership or make arbitrary unknown workflow types executable.

Acceptance: no unowned outstanding Manifest work, no silently altered history, old evidence remains readable, and no new-release execution path exists for a retired type.

### MR3. Delete implementation and simplify shared orchestration

After the history/cutover decision, remove the Manifest package, schema families, registry services, contract modules, canonical workflow class, helper implementation, Activities, and re-exports. Remove Manifest-only evaluation rather than converting it into a new native evaluation subsystem.

Delete the registration and all current/legacy Manifest-specific Activity bindings identified by the audit. `manifest.compile` and `manifest.write_summary` are confirmed current bindings. Check older underscore-named handlers and patch-protected commands against the MR2 inventory before their final removal. Preserve shared `artifact.read` and every other generic artifact operation.

Simplify shared execution creation, mutation, status queries, node pagination, compilation/bootstrap, projection, continuation, and serialization code. Remove Manifest-only constructor parameters from callers such as `execution_integrations.py` and `workflow_console.py`, not the routers themselves. Remove obsolete queue-vs-Temporal Manifest response branches instead of retaining unreachable compatibility DTOs.

Do not implement the unfinished public execute path from #3948 as a prerequisite. Do not rename `MoonMind.ManifestIngest` to a generic batch workflow, wrap the retired compiler in a Skill, or replace it with a successful no-op. Preserve only already-supported generic primitives and concrete surviving utility callers.

Acceptance: API, CLI, worker discovery, and catalog generation import without the deleted package. No Manifest workflow or Activity is registered in the target fleet. Ordinary workflow behavior and the MR2 read-only history path remain intact.

### MR4. Apply a forward persistence migration

Before dropping registry state, preserve exact YAML, version/hash, state, and last-run references in protected operator-controlled storage where they are needed. Treat historical YAML and state as potentially sensitive. Do not commit exports or put them in public issues, and do not embed large exports in Temporal history.

Remove `ManifestRecord` and the `manifest` table using a forward Alembic migration after producers/callbacks are retired and preservation is verified. Keep the existing migration chain usable for both fresh installation and upgrades. Do not delete applied revisions merely to erase the word Manifest. Historical migrations must not depend on runtime modules being removed.

Inspect Manifest-only columns and enum use in shared execution tables separately. Preserve immutable type strings, original inputs, artifact refs, lineage, and result evidence where needed for generic historical reads. Removing an active enum member or response-model variant must not make existing rows undecodable. Do not delete an entire execution row or shared artifact because it originated from a Manifest.

Use existing artifact retention and authorization. Retirement is not authorization to purge MinIO, PostgreSQL, workspaces, saved-work evidence, or unrelated volumes. Record the irreversible migration boundary and recovery procedure. Rolling back code after a destructive schema migration is not sufficient; rehearse restoration with the matching database/export and old release without discarding unrelated newer work.

Acceptance: fresh migration and old-database upgrade pass, former Manifest executions remain readable, preserved exports are recoverable, and unsupported rollback paths stop actionably rather than booting broken code.

### MR5. Remove dependencies, obsolete assets, and contradictory documentation

Coordinate with #4111. Remove LlamaIndex/readers and other dependencies whose last supported caller disappears, then regenerate the lockfile and any maintained dependency exports. Verify direct and transitive dependencies in a clean resolved installation and built images, not only the edited TOML. Do not delete shared HTTP, YAML, provider SDK, or artifact libraries by association. Remaining native-RAG consumers belong to the existing Qdrant retirement work, not a reason to preserve Manifest indefinitely.

Inspect Dockerfile copies, package data, Compose mounts/commands, examples, test setup, startup hooks, maintenance tools, and configuration exports for Manifest-only assumptions. Do not invent a new service or feature flag. Preserve existing deployment access, credentials, volumes, and unrelated service responsibilities.

Delete active standalone Manifest guidance:

- `docs/Rag/LlamaIndexManifestSystem.md`
- `docs/Rag/ManifestIngestDesign.md`
- `docs/UI/ManifestsPage.md`
- Manifest-only example YAML and the dedicated schema.

Reconcile incoming links and assumptions in README, package description, roadmap, API contracts, workflow catalog/lifecycle/product/visibility docs, scheduling guidance, UI architecture, Skill instructions, and CLI help. Keep useful non-RAG context assembly guidance and general database documentation rather than deleting `docs/Rag/` wholesale. Prefer existing canonical documents over a new permanent “Manifest replacement” document.

Update the sources of generated OpenAPI/TypeScript definitions and Temporal catalogs, then regenerate them with their existing tooling, including `tools/generate_temporal_catalog.py`. Remove only the retired dashboard chunk, not Vite's asset manifest or validation tools. Update #4103/#4113/#4114/#4115 instructions that still say generic ManifestIngest must survive.

Acceptance: clean installation/build/startup has no Manifest dependency, configuration requirement, executable registration, supported API schema, UI route, command, or active product promise. Residual references are limited to explicit historical/migration evidence, negative assertions, or unrelated retained systems.

### MR6. Qualify the integrated removal

Use impact-selected `./tools/test_unit.sh` and `./tools/test_integration.sh` coverage, the existing frontend test/typecheck/build commands, clean package/image checks, and the real production registration and API boundaries. Do not substitute a grep-only gate or mocked constructor test for the integrated acceptance matrix below.

Existing tests such as `tests/unit/api/routers/test_manifests.py`, `tests/unit/api/test_manifest_run_request_schema.py`, `tests/unit/services/test_manifests_service.py`, `test_manifest_sync_service.py`, and the Temporal Manifest unit tests need explicit disposition. Delete tests that only prove the retired feature works. Preserve or replace security, history, and shared-boundary assertions. Audit test collection and shared mocks so removing an import does not break unrelated suites.

`tests/integration/workflows/temporal/test_manifest_registration_boundary.py` already exercises compilation/artifacts and a separate node path with real children. Use its histories and controls for the bounded old-release rehearsal, then replace active-fleet expectations with retirement checks. Its existing fixture plan is not proof of complete public Manifest execution, and that missing feature is not work to add now. [S12]

| Gate | Required evidence |
| --- | --- |
| Admission | Dedicated and shared submissions, CLI, stale drafts, schedules/triggers, rerun/reset/resume, and integration inputs cannot start retired work or silently become normal workflows |
| No effects on rejection | No reader/network call, registry write, compile, host launch, paid execution, or child workflow occurs because of a rejected Manifest request |
| Fleet removal | Actual production worker composition and catalogs contain no Manifest workflow or current/legacy Manifest Activity after the cutover gate |
| Historical safety | Both old entry contracts are covered by the selected replay/drain rehearsal; old execution identity and authorized artifacts survive the database upgrade and remain read-only |
| Ordinary workflow regression | Real UI/API submission through UserWorkflow, supported runtime launch, explicit input/context, chat, artifacts, terminal evidence, retry/cancel, and supported recovery continue to work |
| Shared primitives | Ordinary recurring schedules, dependency/child execution, Skill snapshots, saved-work/recovery manifests, and artifact retention continue without Manifest imports |
| Isolation | Old records, exported YAML, artifact refs, and failed access never acquire broader owner/system authority or weaken existing access controls |
| Defaults and packaging | Fresh default startup and upgraded startup require no Manifest/vector settings; test both omitted/default and explicit equivalent supported inputs; clean dependencies and images pass |
| UI and generated contracts | Removed routes/chunks/controls are absent, generic history still renders, and regenerated OpenAPI/TypeScript/catalog outputs agree with production behavior |
| No reintroduction | Targeted ownership/import/registration checks reject the retired product, while legitimate recovery/Skill/Vite manifests and arbitrary user files remain allowed |

Test every runtime/harness configuration currently supported by the changed path, including the applicable Codex, Claude Code, and OpenCode modes. Preserve truthful support status; this project does not promise new support for combinations that are not yet qualified.

Publish exact test commands/results and clearly separate hermetic evidence from live deployment verification. A repository test fixture cannot certify drainage on any of the three deployments.

## 5. Sequence and release boundaries

Start MR1's caller audit, MR2's historical-state analysis, and MR6's test design together. Stop producers before draining. MR3's deletion depends on the cutover decision, not on finishing the retired feature. MR4's destructive migration depends on producer retirement and verified preservation. MR5 accompanies the affected code rather than leaving a broken package or contradictory docs for a later cleanup. MR6 verifies the integrated result.

These six packages are reviewable work units, not a requirement for six independently deployed revisions. Ship a coherent application/schema/dependency/UI revision. Keep temporary old-release support only when real outstanding work requires it. Do not create a long-lived split architecture to make the deletion look incremental.

No active work means a simpler release: verify the empty inventory, preserve historical data as needed, run the upgrade/read tests, and deploy the removal without compatibility workers. With active work, the terminal gate includes parents, children, pending/retryable Activities, scheduled starts, and recurring producers, not only a parent workflow count.

## 6. Existing backlog reconciliation

These are proposed dispositions. This plan does not mutate the issues.

| Existing work | Required reconciliation |
| --- | --- |
| #4103, #4108 | Supersede the explicit generic-ManifestIngest preservation requirement. Keep native-vector retirement and data/evidence safety. Do not reopen completed vector deletion merely to rebuild Manifest |
| #3948 | Supersede feature-completion work after recording any remaining retirement-critical history, authorization, cancellation, and evidence obligations under MR2/MR6. Do not close it as successfully implemented Manifest functionality |
| #4111, #4112, #4113 | Coordinate dependency/image cleanup, CLI removal, docs/examples, and product wording. Remove obsolete instructions to preserve Manifest commands or ingestion contracts |
| #4114, #4115 | Replace the target-release expectation that ManifestIngest survives with rejection plus read-only historical evidence. Keep both historical input contracts in bounded cutover fixtures and preserve real operator gates |
| #3944, #3959 | Reuse replay-safety and generated-catalog ownership rather than introducing duplicate mechanisms |
| #2618, #2215 | Reassess reader-abstraction and retrieval-evaluation work. Retire native-ingestion-only scope; preserve an independent concrete requirement only if it has a supported owner, without a speculative replacement platform |
| #3939 | Reconcile the CLI plan's old requirement to preserve the Manifest command group. Ordinary authenticated workflow CLI work remains valuable |

Check issue and PR state again when assigning work. Concurrent Qdrant, authentication, and orchestration changes may already have removed some targets. Coordinate shared files without mixing unrelated refactors into this project.

## 7. Completion and limits of this review

Removal is complete when the native Manifest product is absent from implementation, admission, worker registration, schemas, persistence ownership, CLI, UI, shipped dependencies, generated contracts, and active documentation, with the historical-data and normal-workflow gates above satisfied. An absent page or workflow class alone is not completion.

Retain immutable history and operator exports according to the existing retention policy. Delete bounded compatibility scaffolding after its verified final consumer is gone. Archive/delete this temporary plan when its implementation and cutover obligations are resolved.

This plan is based on current repository source, documentation, and issue review. No application tests, live workflow inventory, production database export, deployment change, or data deletion were performed. A local checkout was unavailable, so a full repository-wide import/collection audit remains an implementation task rather than a claimed result.

## Source references

All source links below are pinned to the reviewed revision. Issue links represent the reviewed backlog and may change.

- [S1: Qdrant removal epic #4103](https://github.com/MoonLadderStudios/MoonMind/issues/4103)
- [S2: Current Manifest operator guide](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/docs/Rag/LlamaIndexManifestSystem.md)
- [S3: Vector-free Manifest pipeline](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/moonmind/manifest/pipeline.py)
- [S4: Manifest registry service](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/api_service/services/manifests_service.py)
- [S5: Manifest REST router](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/api_service/api/routers/manifests.py)
- [S6: Manifest frontend](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/frontend/src/entrypoints/manifests.tsx)
- [S7: Shared Temporal lifecycle service](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/moonmind/workflows/temporal/service.py)
- [S8: Canonical workflow and historical entries](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/moonmind/workflows/temporal/workflows/manifest_ingest.py)
- [S9: Remaining Manifest feature work #3948](https://github.com/MoonLadderStudios/MoonMind/issues/3948)
- [S10: Production registration and projection classification](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/moonmind/workflows/temporal/workflow_registry.py)
- [S11: Package metadata and dependencies](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/pyproject.toml)
- [S12: Existing Manifest integration boundary tests](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/tests/integration/workflows/temporal/test_manifest_registration_boundary.py)
- [S13: Existing Qdrant cutover runbook](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/docs/tmp/QdrantCutoverRunbook-4115.md)
- [S14: Manifest registry table and shared execution models](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/api_service/db/models.py)
- [S15: Current product and runtime direction](https://github.com/MoonLadderStudios/MoonMind/blob/7bd159dafb44770278e8c5563d47667de6e7c491/README.md)
