# Manifest System Removal Plan

**Document Class:** Imperative working document
**Status:** Remaining repository work and separately authorized rollout
**Reviewed:** 2026-09-20
**Product owner:** [Workflow Type Catalog and Lifecycle](../Temporal/WorkflowTypeCatalogAndLifecycle.md)
**Deployment owner:** [Docker Compose Update System](../Steps/DockerComposeUpdateSystem.md)
**Tracking:** [#4187](https://github.com/MoonLadderStudios/MoonMind/issues/4187), with live obligations retained in [#4189](https://github.com/MoonLadderStudios/MoonMind/issues/4189)

## 1. Decision and target state

Remove MoonMind's native Manifest product, including the vector-free source-reader, transform, evaluation, and compiler successor. Do not rename it, put it behind an optional switch, wrap it in a Skill, or create a replacement ingestion framework.

The operator has explicitly chosen removal. Current canonical lifecycle guidance marks `MoonMind.ManifestIngest` retired. The earlier requirement to wait for another documentation authorization is obsolete. Reconcile contradictory owning guidance with the affected work, not through another approval gate. This working plan still does not authorize production mutation or invent desired behavior independently of the operator and canonical owners.

Preserve normal UserWorkflow execution, generic child/dependency and scheduling primitives, explicit scoped context, chat, Skills, artifacts, publication, and supported recovery. A retired submission must not silently become a successful ordinary workflow.

Preserve checkpoint/recovery/saved-work, Skill/capability, Vite, image-provenance, and GitHub App manifests. A user file named `manifest.yaml` is ordinary content. Target actual retired contracts and registrations, not a blanket word or filename ban.

## 2. Current evidence and its limits

Source review at `65e1cf06b8bc82cab3a8ccf5c880d2cea7681b72` found existing retirement handling and tests, not an untouched implementation backlog. The lifecycle document already excludes ManifestIngest from new-release controls, and `moonmind/gates/manifest_ingest_drain.py` supplies an existing known-versus-unavailable observation boundary. Reuse those owners instead of adding another gate or retirement ledger.

`tests/unit/config/test_manifest_retirement_qualification_4193.py` includes request/model/import checks and source-scan helpers. Those can establish narrower facts, not a full served UI-to-runtime journey or deployed drainage. Its historical status descriptions must not be mistaken for current execution results. Add only missing real boundary coverage.

The [original September 9 plan and source map](https://github.com/MoonLadderStudios/MoonMind/blob/65e1cf06b8bc82cab3a8ccf5c880d2cea7681b72/docs/tmp/ManifestSystemRemovalPlan.md) remain available as historical evidence. Old file lists, package counts, and pending labels are not instructions to restore deleted code or repeat finished work.

## 3. Remaining work packages

These are existing owners, not new engines, serial approval phases, or independently selectable runtime modes. Inspect current code and PRs before implementing a former checklist item.

### MR1. Retire producers and remove authoring surfaces

Owner: #4188. Finish only remaining actual API, CLI, UI, preset, shared submission/control, and saved schedule/draft consumers. Reject retired intent before reader/network access, registry/artifact writes, host launches, or child work. Ordinary unsupported-route behavior is sufficient once a temporary response has no caller.

Remove obsolete navigation, redirects, generated client contracts, and controls with their consumers. Server admission remains authoritative when an old browser or saved input presents stale intent. Direct privileged Temporal administration is a deployment concern, not a guarantee supplied by deleting an HTTP route. Never silently convert retired schedules into ordinary work.

### MR2. Preserve historical reads and control the cutover

Owner: #4189. Separate historical readability from launchability. Preserve exact workflow/run/type identity, source/result references, child lineage, timestamps, and recorded domain outcome through existing generic readers.

Both original input meanings survive: `manifest_ref` compiles/summarizes, while `manifestArtifactRef` executes nodes. Do not reinterpret them, invoke the retired parser to display them, or return successful no-op results. Missing or degraded status/metadata must remain safely readable or explicitly unavailable rather than acquiring fabricated ownership or success.

New-release rerun/reset/resume/update/clone operations cannot recreate the retired type. Any bounded old-release operation required for actual active work remains separately authorized and scoped. New-release historical decoding is not a claim that the new binary replays a deleted workflow.

### MR3. Delete implementation and simplify shared orchestration

Owner: #4190. Remove remaining product packages, schemas, services, worker/Activity registrations, compiler/evaluation paths, and their dead callers. Keep only utilities needed by a real supported non-Manifest caller, under that caller's existing owner.

Do not complete the unfinished old feature to preserve its tests. Do not retain executable registration merely to display history or introduce a compatibility backend for hypothetical consumers. Necessary old-release support has identified work, isolation, and a finite removal condition.

### MR4. Apply a forward persistence migration

Owner: #4191. Use the existing versioned migration, artifact, and deployment owners. Preserve needed private registry content and references before destructive changes and verify the preservation can be used. Old registry YAML and state may be sensitive; they do not belong in public Git, issue comments, or large Temporal payloads.

Keep shared history rows, stable IDs, artifact links, original inputs, and decodable historical type/state fields. Applied migration chains remain usable without importing deleted live packages. Required conversion failures stop unsafe exposure rather than pretending an empty or partial result succeeded.

Prefer transactional rollback of incomplete database work and existing forward repair after committed changes. A code rollback alone cannot undo a destructive schema change. Never restore an entire shared PostgreSQL/Temporal snapshot over newer work or purge unrelated volumes, artifacts, credentials, or checkpoints.

### MR5. Remove dependencies, obsolete assets, and contradictory documentation

Owner: #4192, reusing existing dependency and generated-catalog tooling. Remove dependencies whose last supported caller disappeared, obsolete configuration/initialization, build assets, and dedicated product examples. Verify actual imports, clean installation/build, startup, and generated contracts, not only package-name text.

Keep real shared YAML/HTTP/provider/artifact utilities, general database guidance, and Vite asset support. Owning docs should describe removal, retained generic behavior, and necessary historical exceptions without creating a replacement Manifest document or a new inventory service.

### MR6. Qualify the integrated removal

Owner: #4193. Reuse child tests and the existing API, browser, PostgreSQL, Temporal, runtime, and artifact runners. Add only missing tests at real changed boundaries: retired-intent rejection with no effects, production registration, populated migration/history reads, and a representative ordinary workflow through its required runtime/artifact result.

Preserve relevant access, cancellation, interruption, and lost-acknowledgment coverage at their existing owners. A thin harness-specific difference requires a test where affected, not a duplicate full product matrix for every harness and historic release. Old-release replay fixtures serve actual transition questions; do not ship the removed feature to run them.

Use targeted development tests and current-candidate GitHub Actions for broader verification. Keep required selection and failure aggregation truthful. A short PR account of outcomes and gaps is enough; no fixed test count, permanent plan-coverage ledger, universal scanner, paid-model trial, documentation-wording test, or broad local-suite prerequisite is required.

## 4. Repository completion versus rollout

Repository completion means the product is removed from actual supported admission, registration, implementation, packaging, persistence ownership, UI/CLI, and generated interfaces while retained behavior has relevant execution evidence. An absent page, passing grep, or closed-ticket count is insufficient.

This can be reported independently of live rollout if any remaining deployment and retention obligations stay explicitly tracked in #4189. A unavailable live device is not a failed implementation test and must not consume repeated code-remediation attempts. No fixture, current-candidate CI run, or model verdict certifies a production inventory or cutover.

The operator uses three independent deployments. Each targeted deployment is observed separately when authorized. Evidence from one neither certifies another nor imposes an all-three readiness gate on a safe individual update or on repository CI. Do not claim the whole fleet migrated until all relevant observations actually exist.

## 5. Bounded per-deployment procedure

Use existing authorized deployment/Temporal/state tools, not a new preflight service or background retirement controller. Establish whether legacy state actually exists. A fresh installation with no retained legacy state does not need historical cleanup receipts. An upgraded deployment cannot infer absence from a failed query or a missing file alone.

Stop relevant producers before final consumer observation. Include actual direct schedules, sleeping/active parents, pending/retryable work, child/external effects, delayed callbacks, and registry references where they can still create work or hold data. Keep pagination and unavailable results honest. Parent cancellation is not proof that every child or cleanup stopped.

If positive observations show no consumers, skip compatibility machinery. Otherwise finish or deliberately cancel the identified work on its matching existing release, under explicit authority, and reconcile remaining effects/evidence before removal. Do not introduce permanent candidate/retained fleets, unsafe shared-queue mixing, or successful no-op Activities.

Apply the normal in-place update with required preservation and migration checks. Keep deployment-owned configuration, keys, mounts, published bindings, and ingress protection. Verify the actual configured operator hostname/port, dashboard/assets, and relevant API path after the operation. Container health or a different localhost path does not prove LAN/VPN/proxy access. Use existing probes, including the deployed UI asset verifier where applicable, without requiring a healthy application as the sole repair path.

Record actual observed operation/result and unresolved work in the existing local/protected records. Retain old image/evidence only for real recovery obligations. Cleanup affects only positively owned resources after their last consumer and retention obligation end. No production mutation is authorized by writing this plan.

## 6. Backlog reconciliation and documentation lifetime

The removal target supersedes old requirements to preserve generic ManifestIngest in the vector-removal work and to finish native reader/compiler features. Reuse existing replay, artifact, dependency, CLI, and catalog work instead of reopening it as another project. Surviving normal workflow capabilities keep their original owners.

Current issue descriptions own remaining scope. Historical maps are reference material, not another backlog to execute. Feature changes update their owning docs and tests together; no documentation-approval pipeline is required.

Archive or remove this temporary plan after needed guidance is in the canonical owners and concrete deployment/recovery references have another home. Pending real obligations may remain in #4189 without preserving an obsolete permanent architecture. This review did not run application tests, inspect live inventories, export production data, or perform any deployment or deletion.
