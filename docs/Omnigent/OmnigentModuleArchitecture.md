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
| `moonmind/omnigent/host_ports.py`, `execution_ports.py` | Narrow ports for host realization and profile-bound execution | Pure |
| `moonmind/omnigent/control_plane/turn_commands.py` | Canonical turn command use case | Application |
| `moonmind/omnigent/host_runtime.py` | Harness-neutral host realization and cleanup use case | Application |
| `moonmind/omnigent/realizers/` | Versioned execution realizers and the trusted realizer registry | Application |
| `moonmind/omnigent/control_plane/repositories.py` | Session, turn, observation, command, decision, alias, cleanup-authority persistence | Persistence |
| `moonmind/omnigent/harness_platform/stores.py` | Execution-plan, plan-usage, and runtime-binding persistence | Persistence |
| `moonmind/omnigent/execution_adapters.py` | Provider Profile authority, execution policy snapshot, and Temporal attempt adapters | Persistence |
| `moonmind/omnigent/host_services/` | Docker/Compose launcher, workspace, skills, mounted tools, GitHub credentials, egress, registration, attestation, cleanup | Adapter |
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
  lifecycle-coordination work.
- Deterministic session/turn identity has one owner,
  `control_plane/identities.py`.
- Production modules never import test doubles or acceptance fixtures.
- Canonical session lifecycle code contains no harness-name literal. Approving a
  harness is registration data in
  `harness_platform/harness_registry.py`.

## 4. Ports

Ports are narrow and single-concern. There is no broad "client", "store",
"coordinator", or "runtime" interface that owns unrelated concerns.

| Concern | Port | Implementations |
| --- | --- | --- |
| Execution-plan storage | `harness_platform/stores.py::OmnigentExecutionPlanStore` | in-memory, DB, API-transaction |
| Plan usage / retry identity | `harness_platform/stores.py::OmnigentExecutionPlanUsageStore` | in-memory, DB |
| Runtime-binding persistence | `harness_platform/stores.py::OmnigentRuntimeBindingStore` | in-memory, DB |
| Stable runtime binding | `runtime_bindings.py` store protocol | in-memory, DB |
| Harness catalog observation | `harness_platform/catalog_service.py::OmnigentHarnessCatalogRepository` | in-memory, DB |
| Provider inventory | `harness_platform/catalog_service.py::OmnigentInventoryClient` | HTTP client, fake |
| Host realization and cleanup | `host_ports.py` (`launcher`, `registration`, `attestation`, `cleanup`) | Docker adapters, test doubles |
| Workspace, skills, tools, GitHub credentials, egress | `host_ports.py` | `host_services/` adapters |
| Host leases | `host_leases.py::OmnigentHostLeaseRepository` | in-memory, DB |
| Provider Profile leases | `provider_leases.py::ProviderLeaseClient` | lease client, fake |
| Credential materialization | `harness_platform/materializers.py`, `credential_materializers.py` | per-materializer descriptors |
| Provider Profile authority, policy snapshot, attempt ordinal | `execution_ports.py` | `execution_adapters.py` |
| Workspace checkpoint/restore | `remediation_workspace.py` owner protocol | sandbox owner, test double |
| Secret resolution | `secret_resolution.py` | root resolver chain |

`tests/unit/omnigent/test_adapter_contracts.py` runs one shared behavior
contract per port across the hermetic implementation and the deployed
implementation, so a test double cannot diverge on idempotency, fencing, or
conflict vocabulary.

## 5. Compatibility shims and their retirement owners

Every retained legacy path and every enforced-boundary exemption is owned in
code by `moonmind/omnigent/legacy_retirement.py`.
`RETIREMENT_INVENTORY` lists the legacy paths; `ARCHITECTURE_BOUNDARY_EXCEPTIONS`
lists the boundary exemptions and names the legacy path whose #3712 criteria
retire each one.

| Shim / retained path | Owner record | Retires when |
| --- | --- | --- |
| `bridge_store.py` overloaded bridge session row | `omnigent.legacy.bridge_persistence` | base #3712 criteria pass |
| `execute.py` legacy session driver | `omnigent.legacy.bridge_execution` | base criteria + cumulative remediation |
| `profile_bound_execution.py` coordinator (and its default port selection) | `omnigent.legacy.profile_bound_execution` | base criteria + browser-to-host acceptance |
| `native_ui_compat.py` chat projection | `omnigent.legacy.native_ui_compat` | base criteria + native chat acceptance |
| `cutover.py` Codex-through-Omnigent selection | `omnigent.legacy.codex_cutover_selection` | base #3712 criteria pass |
| `omnigent_catalog.py` in-handler readiness projection reading the materializer descriptor registry | `omnigent.legacy.native_ui_compat` | the readiness projection moves behind an application service |

Temporary supervisor rollout flags are listed in `TEMPORARY_ROLLOUT_FLAGS` with
their retirement trigger, so a rollout flag cannot become a permanent alternate
architecture.

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
