# Omnigent Module Architecture

**Document Class:** Canonical declarative
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-08-29
**Authority:** The dependency and ownership map for the implemented Omnigent packages, the boundaries CI enforces, and the bounded exemptions that remain.

**Issue:** [MoonLadderStudios/MoonMind#3711](https://github.com/MoonLadderStudios/MoonMind/issues/3711).

## Related documents

- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md) — target harness-platform model
- [`docs/Omnigent/ControlPlaneAggregates.md`](./ControlPlaneAggregates.md)
- [`docs/Omnigent/ControlPlaneConcurrencyAndFencing.md`](./ControlPlaneConcurrencyAndFencing.md)
- [`docs/Omnigent/OmnigentLifecycleReconciler.md`](./OmnigentLifecycleReconciler.md)
- [`docs/Omnigent/CodexSupportAndCutover.md`](./CodexSupportAndCutover.md)

## 1. What this document owns

This document describes the packages that exist today, who owns which decision,
and which direction dependencies are allowed to point. It is enforced, not
aspirational: `tests/unit/omnigent/test_module_architecture.py` parses the
repository and fails when a rule below is violated, and
`moonmind/omnigent/legacy_retirement.py` owns the exemptions and the #3712
criteria that retire them.

The target harness-platform semantics — catalog trust, Agent Profiles, Host
Classes, capability negotiation, plan compilation — remain owned by
`OmnigentHarnessPlatformDesign.md`. This document owns only module ownership and
dependency direction.

## 2. Package map

| Package | Responsibility | Layer |
| --- | --- | --- |
| `moonmind/omnigent/reconciler/` | Pure lifecycle contracts and reducer | Pure |
| `moonmind/omnigent/control_plane/records.py`, `identities.py` | Canonical session/turn records and the single owner of deterministic identity vocabulary | Pure |
| `moonmind/omnigent/harness_platform/` value modules (`agent_profile`, `attestation`, `capabilities`, `catalog`, `credential_bindings`, `execution_plan`, `failures`, `harness_registry`, `runtime_binding`, `skills`, `support`) | Immutable schemas, capability/support decisions, approved-harness registration data | Pure |
| `moonmind/omnigent/codex_execution_decisions.py` | Launch classification, budget authority, request identity, and authored-request binding for the legacy Codex realizer | Pure |
| `moonmind/omnigent/host_failures.py` | Host failure vocabulary | Pure |
| `moonmind/omnigent/host_auth_contracts.py` | Embedded host-auth profile, failure, and rotation vocabulary | Pure |
| `moonmind/omnigent/host_ports.py`, `execution_ports.py` | Narrow ports for host realization and profile-bound execution | Pure |
| `moonmind/omnigent/control_plane/turn_commands.py` | Canonical turn command use case | Application |
| `moonmind/omnigent/host_runtime.py` | Harness-neutral host realization and cleanup use case | Application |
| `moonmind/omnigent/realizers/` | Versioned execution realizers and the trusted realizer registry | Application |
| `moonmind/omnigent/control_plane/repositories.py` | Session, turn, observation, command, decision, alias, cleanup-authority persistence | Persistence |
| `moonmind/omnigent/harness_platform/stores.py` | Execution-plan, plan-usage, and runtime-binding persistence | Persistence |
| `moonmind/omnigent/execution_adapters.py` | Provider Profile authority, execution policy snapshot, and Temporal attempt adapters | Persistence |
| `moonmind/omnigent/host_services/` | Docker/Compose launcher, workspace, skills, mounted tools, GitHub credentials, egress, registration, attestation, cleanup, legacy host container inventory | Adapter |
| `moonmind/omnigent/host_auth_profile.py`, `host_auth_store.py` | Embedded host-auth SecretRef resolution, readiness projection, durable profile row | Adapter |
| `moonmind/omnigent/harness_platform/materializers.py`, `credential_materializers.py` | Credential materializer descriptors and provisioning | Adapter |
| `moonmind/omnigent/bridge_*.py`, `execute.py`, `oauth_host_runtime.py`, `profile_bound_execution.py` | Replay-visible legacy Codex transport, session driver, host lifecycle, and coordinator | Adapter (legacy) |
| `moonmind/omnigent/workspace_publication.py`, `bridge_artifacts.py`, `remediation_workspace.py` | Workspace publication, artifact/evidence, checkpoint adapters | Adapter |
| `moonmind/omnigent/bootstrap/` | Deployment bootstrap, image resolution, qualification, provider revalidation | Adapter / composition |
| `moonmind/omnigent/production.py` | Composition root for the generic execution plane | Composition |
| `moonmind/workflows/temporal/workflows/omnigent_session.py` | Durable session supervisor (deterministic workflow code only) | Supervisor |
| `moonmind/workflows/temporal/activities/omnigent_*_activities.py` | Bounded activities that bind the supervisor to adapters | Composition |
| `moonmind/omnigent/workflow_chat_facade.py`, `native_ui.py` | Workflow Chat facade projections | UI facade |
| `api_service/api/routers/omnigent_*.py` | Route contract, authorization, serialization | UI facade |
| `api_service/api/routers/omnigent_bridge_composition.py` | Which concrete store, transport, facade, and credential profile backs a bridge route | Composition |
| `moonmind/omnigent/conformance.py`, `exact_artifact_conformance.py`, `workflow_chat_acceptance.py`, `control_plane/timeline.py` | Conformance, acceptance, and timeline evidence | Evidence |
| `moonmind/omnigent/faultlab/` | Fault-injection corpus, reference model, and invariants for the reconciler | Evidence (test-facing) |
| `moonmind/omnigent/legacy_retirement.py`, `session_migration_inventory.py`, `session_supervisor_rollback.py` | #3712 retirement inventory, migration inventory, rollback authority | Governance |

## 3. Allowed dependency direction

Dependencies point inward, from adapters toward application coordination and
pure contracts.

| Layer | May depend on | Must not depend on |
| --- | --- | --- |
| Pure | Other pure modules, validation libraries | API, SQL, Temporal, HTTP, container, process, filesystem, settings, or environment |
| Application | Pure contracts and narrow ports | FastAPI, SQLAlchemy, Temporal clients, HTTP clients, Docker clients, concrete adapters, harness-name selection |
| Persistence | Pure records and database models | Router policy, provider transport policy |
| Adapter (provider transport, host lifecycle, workspace, credential) | Pure, application, persistence, workspace/credential adapters | UI facade policy, plan recompilation |
| Composition | Every concrete capability needed to assemble a supported realizer | Alternate authority semantics or harness-name selection |
| UI facade | Application services and projections | Persistence mutation, provider identity selection, host lifecycle, credential materialization |
| Evidence | Immutable plan/binding/session refs and observed artifacts | Replacement plan, session, or lifecycle authority |

Additional enforced rules:

- Container and process authority (`docker`, `subprocess`) exists only in
  `host_services/`, `oauth_host_runtime.py`, `bootstrap/qualification.py`, and
  `production.py`.
- Environment variables and deployment settings are read at the
  composition/infrastructure boundary. Pure, application, persistence, UI, and
  evidence modules receive them as data.
- Provider-native vocabulary (endpoint routes, provider runtime ids) is
  normalized at adapter boundaries and never appears in pure, application,
  persistence, UI, or evidence modules.
- Routers perform no Docker, provider, credential-materialization, or
  lifecycle-coordination work. A route handler may *name* a store, facade, or
  host-auth type — annotations, dependency signatures, and the error vocabulary
  it maps to HTTP are route contract — but may not construct one, open a
  database session, or resolve a credential. Selecting the backing
  implementation is composition and lives in
  `api_service/api/routers/omnigent_bridge_composition.py`, the one router-layer
  module excluded from the route-handler rules.
- Container authority is a raw `docker`/`docker-compose` argument vector as much
  as it is a Docker SDK import. Assembling one is owned by `host_services/`,
  `bootstrap/`, `credential_materializers.py`,
  `opencode_runtime_validation.py`, and `production.py`; every other Omnigent
  module needs a bounded exemption that names its #3712 retirement path.
- Deterministic session/turn identity has one owner,
  `control_plane/identities.py`.
- Production modules never import test doubles or acceptance fixtures.
- Canonical session lifecycle code contains no harness-name literal. Approving a
  harness is registration data in
  `harness_platform/harness_registry.py`.

## 4. Ports

Ports are narrow and single-concern. There is no broad "client", "store",
"coordinator", or "runtime" interface that owns unrelated concerns.

`Coverage` is what `tests/unit/omnigent/test_adapter_contracts.py` enforces
today. **Paired** means one shared behavior contract runs across the hermetic
and the deployed implementation, so a test double cannot diverge on
idempotency, fencing, or conflict vocabulary. **Conformance** means the
deployed implementation is machine-checked against the declared port so the two
cannot drift apart, without a second implementation to pair against.

| Concern | Port | Implementations | Coverage |
| --- | --- | --- | --- |
| Execution-plan storage | `harness_platform/stores.py::OmnigentExecutionPlanStore` | in-memory, DB, API-transaction | Paired |
| Plan usage / retry identity | `harness_platform/stores.py::OmnigentExecutionPlanUsageStore` | in-memory, DB | Paired |
| Runtime-binding persistence | `harness_platform/stores.py::OmnigentRuntimeBindingStore` | in-memory, DB | Paired |
| Stable runtime binding | `runtime_bindings.py::StableRuntimeBindingStore` | in-memory, DB | Behavior contract on in-memory; DB is conformance-only |
| Harness catalog observation | `harness_platform/catalog_service.py::OmnigentHarnessCatalogRepository` | in-memory, DB | Paired |
| Provider inventory | `harness_platform/catalog_service.py::OmnigentInventoryClient` | `OmnigentHttpClient` | Conformance |
| Host realization and cleanup | `host_ports.py` (`launcher`, `registration`, `attestation`, `cleanup`) | Docker adapters, test doubles | Conformance |
| Legacy host container inventory and reclamation | `host_ports.py::OmnigentHostContainerInventoryPort` | `host_services/legacy_host_containers.py`, hermetic double | Paired |
| Host preparation, workspace publication, provider session inspection, host release | `execution_ports.py`, `host_ports.py::OmnigentHostReleasePort` | `oauth_host_runtime.py` | Conformance + signature drift |
| Workspace, skills, tools, GitHub credentials, egress | `host_ports.py` | `host_services/` adapters | Conformance |
| Host leases | `host_leases.py::OmnigentHostLeaseRepository` | in-memory, DB | Paired |
| Provider Profile leases | `provider_leases.py::ProviderLeaseClient` | `ProviderProfileLeaseClient` | Conformance |
| Credential materialization | `harness_platform/materializers.py`, `credential_materializers.py` | per-materializer descriptors | Paired (descriptor contract) |
| Provider Profile authority, policy snapshot, attempt ordinal | `execution_ports.py` | `execution_adapters.py` | Conformance |
| Workspace checkpoint/restore | `remediation_workspace.py::RemediationWorkspaceOwner` | `SandboxRemediationWorkspaceOwner` | Conformance |
| Secret resolution | `secret_resolution.py::SecretResolver` | root resolver chain | Conformance |

The stable runtime binding store recomputes its snapshot digest from the
persisted row. The shared contract fixture is SQLite, which returns
`DateTime(timezone=True)` columns as naive datetimes, so that digest can only be
reproduced against PostgreSQL; the DB implementation is therefore
conformance-checked here and behavior-verified against a real PostgreSQL
deployment.

The legacy Codex host adapter satisfies four separate ports — host preparation,
workspace publication, provider session inspection, and host release — each with
a declared signature rather than an untyped `**kwargs` payload. The contract
suite fails if a port reunifies those capabilities, reopens `**kwargs`, or
drifts away from the adapter's signature. `ProfileBoundHostPorts` and
`OmnigentHostReclamationPorts` declare *dependency sets* over those ports; a
contract asserts they add no capability of their own.

## 5. Retained duplicate architecture and its retirement owners

Every retained legacy component and every enforced-boundary exemption is owned in
code by `moonmind/omnigent/legacy_retirement.py`. A document checklist is not
sufficient: the inventory is the authority, and CI fails when the repository
grows a legacy surface that no row classifies.

- `RETIREMENT_INVENTORY` holds one row per retained component. Each row records
  its owner, **retirement class**, last new-write/new-admission source, active
  resource dependencies, replay and historical-read dependencies, rollback
  dependency and exact permitted rollback generations, required evidence
  (`applicable_criteria`), earliest removal stage, and removal guard test.
- `ARCHITECTURE_BOUNDARY_EXCEPTIONS` holds the boundary exemptions and names the
  row whose criteria retire each one.
- `moonmind/omnigent/retirement_surfaces.py` derives the legacy surfaces the
  repository actually contains — non-generic realizers, direct managed runtime
  strategies, provider-specific startup scripts, duplicate Compose services and
  profiles, and non-canonical image variables — from code and deployment
  configuration rather than a hand-maintained list.
- `moonmind/omnigent/retirement_drain.py` aggregates active-owner probe
  observations into fail-closed drain evidence.

### 5.1 Retirement classes

Every retained component carries exactly one class. The order is the staged
convergence order:

| Class | Meaning |
| --- | --- |
| `active_product_path` | Still the supported path for new work. |
| `new_admission_disabled` | No new plan may select it; recorded work continues. |
| `rollback_only` | New work only under an exactly allowlisted rollback generation. |
| `active_execution_support` | Owns work that is already running. |
| `cleanup_only` | Owns reclamation for work that has stopped. |
| `temporal_replay_only` | A replay-visible wrapper for recorded histories. |
| `historical_read_only` | Read model for recorded work. |
| `migration_tool` | Moves records; never runs work. |
| `eligible_for_removal` | Every dependency drained and every window closed. |
| `removed` | The implementation is gone. |

`active_product_path` and `rollback_only` are the only classes that admit new
work. Plan compilation
(`harness_platform/planner.py:compile_execution_plan`) and runtime selection
(`cutover.py:select_runtime`) both consult the class before admitting, so a
trusted planner default, an alternate API client's explicit selection, a
schedule, and a preset are all held to the same code-owned state. Disabling new
admission never affects execution, cancellation, cleanup, or reads for
already-recorded plans.

### 5.2 Removal stages

`RemovalStage` is the ordered staging from product selectors (1) through
historical readers (9). A `RemovalPlan` targets exactly one stage and cites the
rows it removes; `evaluate_removal_plan` returns the eligible rows, the
remaining blockers, and the guard tests the PR must show passing. A row may not
be swept into a stage earlier than its `earliest_removal_stage`, so a historical
reader can never be deleted alongside a product selector.

Removal eligibility is fail-closed on every axis: a still-admitting class, an
undrained active owner, an open replay/historical-read/rollback window, a
missing rollback exercise, or an unmet retirement criterion all block.

### 5.3 Retained components

| Shim / retained component | Owner record | Class today | Earliest removal stage |
| --- | --- | --- | --- |
| Direct Codex launch strategy | `omnigent.legacy.direct_codex_launch` | `active_product_path` | 1 product selectors |
| Direct Codex session runtime | `omnigent.legacy.direct_codex_session_runtime` | `active_product_path` | 7 launch-only code |
| Direct Codex session adapter | `omnigent.legacy.direct_codex_session_adapter` | `active_product_path` | 7 launch-only code |
| Direct Codex bridge compatibility producer | `omnigent.legacy.direct_codex_bridge_compat` | `active_product_path` | 9 historical readers |
| Direct Claude launch strategy | `omnigent.legacy.direct_claude_launch` | `active_product_path` | 1 product selectors |
| `codex-profile-bound@1` realizer | `omnigent.legacy.profile_bound_realizer` | `active_product_path` | 1 product selectors |
| `profile_bound_execution.py` coordinator (and its default port selection) | `omnigent.legacy.profile_bound_execution` | `active_product_path` | 7 launch-only code |
| `oauth_host_runtime.py` launch, mount, credential-volume, and egress argument vectors | `omnigent.legacy.oauth_host_runtime` | `active_product_path` | 6 OAuth-host orchestration |
| Legacy OAuth host janitor | `omnigent.legacy.oauth_host_janitor` | `cleanup_only` | 6 OAuth-host orchestration |
| OAuth session Activity registrations | `omnigent.legacy.oauth_session_activities` | `active_execution_support` | 3 composition-root registrations |
| Provider Profile capacity/lease consumer | `omnigent.legacy.provider_profile_capacity_consumer` | `active_execution_support` | 3 composition-root registrations |
| Codex static host scripts and Compose profile | `omnigent.legacy.codex_static_host_startup` | `active_product_path` | 4 startup and Compose |
| Claude static host scripts and Compose profile | `omnigent.legacy.claude_static_host_startup` | `active_product_path` | 4 startup and Compose |
| Pre-consolidation `omnigent-host` service and projection entrypoint | `omnigent.legacy.projection_host_startup` | `active_product_path` | 4 startup and Compose |
| Legacy generic host image variables | `omnigent.legacy.host_image_variable_alias` | `active_product_path` | 5 image and environment aliases |
| OpenCode shared-image variable / `omnigent-host-opencode` alias | `omnigent.legacy.opencode_host_image_alias` | `active_product_path` | 5 image and environment aliases |
| Pi host image variable | `omnigent.legacy.pi_host_image_alias` | `active_product_path` | 5 image and environment aliases |
| Persisted per-provider bootstrap image fields | `omnigent.legacy.persisted_bootstrap_image_fields` | `active_product_path` | 5 image and environment aliases |
| `bridge_store.py` overloaded bridge session row | `omnigent.legacy.bridge_persistence` | `active_product_path` | 9 historical readers |
| `execute.py` legacy session driver | `omnigent.legacy.bridge_execution` | `active_product_path` | 2 new-write API paths |
| `native_ui_compat.py` chat projection | `omnigent.legacy.native_ui_compat` | `active_product_path` | 9 historical readers |
| `cutover.py` Codex-through-Omnigent selection | `omnigent.legacy.codex_cutover_selection` | `active_product_path` | 1 product selectors |
| Managed-session replay patch branches | `omnigent.legacy.managed_session_replay_patches` | `temporal_replay_only` | 8 replay wrappers |
| Session migration inventory | `omnigent.legacy.session_migration_inventory` | `migration_tool` | 9 historical readers |
| `#3834` static-host startup runbook | `omnigent.legacy.static_host_startup_runbook` | `historical_read_only` | 4 startup and Compose |

Two enforced-boundary exemptions are additionally owned by rows above:
`omnigent_catalog.py`'s in-handler readiness projection reading the materializer
descriptor registry (`omnigent.legacy.native_ui_compat`), and
`oauth_host_runtime.py`'s raw Docker/Compose argument vectors
(`omnigent.legacy.oauth_host_runtime`) — moving the launch argument vector would
change what in-flight histories were started with.

Temporary supervisor rollout flags are listed in `TEMPORARY_ROLLOUT_FLAGS` with
their retirement trigger, so a rollout flag cannot become a permanent alternate
architecture.

### 5.4 Obsolete configuration

`OBSOLETE_CONFIGURATION` names each legacy image/environment identity, its
replacement, and its owning retirement row. `assert_obsolete_configuration` runs
at API startup: during a variable's deprecation window a supplied value produces
an actionable operator warning naming the replacement, and after removal startup
is rejected outright. No obsolete value is ever silently ignored. Nothing is
deprecated today — the aliases remain the supported way to pin a prior image
while the rollback window is open.

## 6. Adding an approved harness

Adding an approved harness is data, not lifecycle code:

1. Synchronize the endpoint catalog and classify trust.
2. Register the harness in `harness_platform/harness_registry.py` (canonical id,
   aliases, execution target, Host Class, materializer, auth model).
3. Publish the Host Class or runtime pack and the credential materializer
   descriptor.
4. Publish the Agent Profile and its support evidence.

No Temporal activity, realizer, session supervisor, or generic host runtime
change is required. `tests/unit/omnigent/test_second_harness_conformance.py`
proves this with a synthetic harness, and the architecture contract fails if a
harness name reappears in canonical lifecycle code.
