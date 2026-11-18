# Omnigent Module Architecture

**Document Class:** Canonical declarative
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-08-29
**Authority:** The dependency and ownership map for the implemented Omnigent packages, the boundaries CI enforces, and the bounded exemptions that remain.

## Related documents

- [`docs/Omnigent/README.md`](./README.md) — module entrypoint and contract owners
- [`docs/Omnigent/ContractOwnership.md`](./ContractOwnership.md) — per-file ownership map
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
`moonmind/omnigent/legacy_retirement.py` owns the exemptions and the legacy
admission table they are tied to.

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
| `moonmind/omnigent/codex_execution_decisions.py` | Request identity, checkpoint branch binding, and follow-up retrieval policy shared by dispatch and admission | Pure |
| `moonmind/omnigent/host_failures.py` | Host failure vocabulary | Pure |
| `moonmind/omnigent/host_ports.py` | Narrow ports for host realization and OAuth host-ledger reclamation | Pure |
| `moonmind/omnigent/control_plane/turn_commands.py` | Canonical turn command use case | Application |
| `moonmind/omnigent/host_runtime.py` | Harness-neutral host realization and cleanup use case | Application |
| `moonmind/omnigent/realizers/` | Versioned execution realizers and the trusted realizer registry | Application |
| `moonmind/omnigent/control_plane/repositories.py` | Session, turn, observation, command, decision, alias, cleanup-authority persistence | Persistence |
| `moonmind/omnigent/harness_platform/stores.py` | Execution-plan, plan-usage, and runtime-binding persistence | Persistence |
| `moonmind/omnigent/host_services/` | Docker/Compose launcher, workspace, skills, mounted tools, GitHub credentials, egress, registration, attestation, cleanup, credential-only OAuth validation (`oauth_credential_validation.py`), and OAuth host-ledger inventory and reclamation (`legacy_host_containers.py`) | Adapter |
| `moonmind/omnigent/mounted_tool_preflight.py` | GitHub credential visibility and repository-access checks for generic hosts | Adapter |
| `moonmind/omnigent/harness_platform/materializers.py`, `credential_materializers.py` | Credential materializer descriptors and provisioning | Adapter |
| `moonmind/omnigent/bridge_*.py`, `execute.py` | Replay-visible bridge transport and session driver | Adapter |
| `moonmind/omnigent/workspace_publication.py`, `bridge_artifacts.py`, `remediation_workspace.py` | Workspace publication, artifact/evidence, checkpoint adapters | Adapter |
| `moonmind/omnigent/bootstrap/` | Deployment bootstrap, image resolution, qualification, provider revalidation | Adapter / composition |
| `moonmind/omnigent/production.py` | Composition root for the generic execution plane | Composition |
| `moonmind/workflows/temporal/workflows/omnigent_session.py` | Durable session supervisor (deterministic workflow code only) | Supervisor |
| `moonmind/workflows/temporal/activities/omnigent_*_activities.py` | Bounded activities that bind the supervisor to adapters | Composition |
| `moonmind/omnigent/workflow_chat_facade.py`, `native_ui.py` | Workflow Chat facade projections | UI facade |
| `api_service/api/routers/omnigent_*.py` | Route contract, authorization, serialization | UI facade |
| `api_service/api/routers/omnigent_bridge_composition.py` | Which concrete store, transport, facade, and credential profile backs a bridge route | Composition |
| `moonmind/omnigent/conformance.py`, `exact_artifact_conformance.py`, `workflow_chat_acceptance.py`, `control_plane/timeline.py` | Conformance, acceptance, and timeline evidence | Evidence |
| `tools/omnigent_faultlab/` | Fault-injection corpus, reference model, and invariants for the reconciler | Evidence (test-facing) |

GitHub projection readiness separates local credential delivery from remote
repository access. The preflight owner's token lookup
discards the token inside the host, and one repository query per execution
environment checks the requested identity and, when required, write permission.
Generic hosts check the host, runner, and OpenCode shell when applicable.
Account-status and duplicate permission queries are not launch prerequisites.
Repository-free tool projections attest only local credential availability.

Transient transport failures and HTTP 5xx responses receive up to four attempts
on the same host and credential, with bounded backoff. A rate-limited probe
reads the reset from the response headers of one REST and one GraphQL request
(GitHub's `/rate_limit` endpoint can report an untouched budget while requests
are rejected) and waits in place once: for `Retry-After` when present,
otherwise for the exhausted resource's reset (at most 15 minutes; one minute
when neither is reported). Authentication,
authorization, identity, and permission failures fail closed. Cancellation
propagates. Generic-host failures preserve redacted attempt evidence and the
original diagnostic even if artifact storage is unavailable. A rejected
credential, identity, or permission reports a host capability failure instead
of directing credential rematerialization. A transport failure or rate limit
that outlasts in-place recovery reports
`OMNIGENT_EXTERNAL_SERVICE_UNAVAILABLE` with `retry_step_execution`: no session
exists yet, so the parent's existing bounded step retry starts a fresh Step
Execution instead of failing the workflow. `OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED`
(`retry_materialization`, for example any timed-out materialization Docker step;
a host image pull resumes from the layers already pulled) gets the same bounded
retry. Hermetic coverage
lives in `test_mounted_tool_preflight.py` and the full handoff regression in
`test_generic_platform_production_services.py`.
| `moonmind/omnigent/legacy_retirement.py`, `session_supervisor_rollback.py` | Legacy admission table, rollback authority | Governance |

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
  `host_services/`, `bootstrap/qualification.py`, and `production.py`.
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
  module needs a bounded exemption that names its retirement path.
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
| Ledger host release | `host_ports.py::OmnigentHostReleasePort` | `host_services/legacy_host_containers.py` | Conformance + typed signature |
| Workspace, skills, tools, GitHub credentials, egress | `host_ports.py` | `host_services/` adapters | Conformance |
| Host leases | `host_leases.py::OmnigentHostLeaseRepository` | in-memory, DB | Paired |
| Provider Profile leases | `provider_leases.py::ProviderLeaseClient` | `ProviderProfileLeaseClient` | Conformance |
| Credential materialization | `harness_platform/materializers.py`, `credential_materializers.py` | per-materializer descriptors | Paired (descriptor contract) |
| Workspace checkpoint/restore | `remediation_workspace.py::RemediationWorkspaceOwner` | `SandboxRemediationWorkspaceOwner` | Conformance |
| Secret resolution | `secret_resolution.py::SecretResolver` | root resolver chain | Conformance |

The generic workspace port uses the remediation owner and canonical checkpoint
restore service. It validates the controller head, execution identity and
admitted Profile/launch snapshot before transferring or restoring its
attempt-owned workspace. Preparation retries preserve candidate
edits; interrupted transfers reconcile their recorded intent. New Git save
handoffs use the canonical checkpoint to restore HEAD, index and worktree,
including deletions, after local workspace loss. They carry the admitted
repository identity so self-contained saves retain Enterprise remotes without
acquiring source credentials. Recorded archive handoffs retain their additive
interpretation. Restored directories and contents transfer to the
selected runtime UID. An existing candidate without admission
proof is preserved with an explicit uncertainty error. The port uses the scoped
Temporal artifact adapter; model and repository credentials remain separate.
Canonical GitHub repository sources use the selected account's shared plan when
the controller supports repository-to-save handoffs. A separate versioned
controller capability also admits a typed full Git commit SHA with publication
mode `none`; abbreviated or unsupported revisions retain their existing owner.
The shared owner verifies the requested commit before first readiness and binds
it into immutable workspace intent and readiness identity. Transient pin fetches
resume only after the trusted clone/configuration phase completes; partial
imports are rebuilt in private staging within the existing retry budget. A controller save
retains the original pin as provenance, but restores its own verified HEAD,
index and worktree instead of resetting later work to the initial commit.
The workspace-source owner preserves repository, connection, access mode and
branch intent when a controller save replaces the initial checkout; authored
source conflicts remain errors. Older parents retain native dispatch. Fresh preparation selects the authored
target branch through the existing volume owner; redelivery preserves later
branches and edits. New sandbox saves restore their captured branch and history,
including detached HEAD; retained manifests keep their prior restore behavior.
Explicit checkpoint-branch creation and interrupted redelivery retain the admitted target. Each
helper mounts only its admitted subtree, preserving sibling isolation.

The stable runtime binding store and legacy harness binding store currently
share a table and binding-ID prefix, but not a serialized authority schema.
Generic recovery inventory selects only stable rows, identified by their
immutable initial `omnigent-runtime-binding-snapshot:sha256:` primary key.
Legacy records (including `cleanup_complete`) stay with their lifecycle owner;
they are not projected into stable cleanup authority. Stable snapshot validation
and the positive Temporal-owner-closed gate remain mandatory. This separation
is needed until the legacy producer and its retained records are retired.

The stable runtime binding store recomputes its snapshot digest from the
persisted row. The shared contract fixture is SQLite, which returns
`DateTime(timezone=True)` columns as naive datetimes, so that digest can only be
reproduced against PostgreSQL; the DB implementation is therefore
conformance-checked here and behavior-verified against a real PostgreSQL
deployment.

The ledger host release port declares one typed `stop_host` capability. The
contract suite fails if it gains another capability, reopens `**kwargs`, or
loses its deployed adapter. `OmnigentHostReclamationPorts` declares the
janitor's *dependency set* over inventory and release; a contract asserts it
adds no capability of its own.

## 5. Retained duplicate architecture and its retirement owners

Whether new work may still select a retained legacy path, and which legacy path
each enforced-boundary exemption shelters, is owned in code by
`moonmind/omnigent/legacy_retirement.py`. CI fails when the repository grows a
legacy surface that no row classifies.

- `RETIREMENT_INVENTORY` holds one row per retained legacy path that a
  new-admission boundary can select or that an architecture exemption names.
  Each row records its owner, **retirement class**, surfaces, the source
  that still admits new work, the actual consumer it serves (journeys,
  explicit choices, retained histories), and the observable removal
  condition. A row that still admits new work also names the shared
  lifecycle operation that would serve its consumer and the concrete outcome
  that operation still lacks, so a path stays live for a named gap, not its
  age. Direct Codex/Claude launch, the managed-runtime launcher, the direct
  Codex AgentSession, the legacy image variables and the native UI chat
  projection each have a row.
- `ARCHITECTURE_BOUNDARY_EXCEPTIONS` holds the boundary exemptions and names the
  row each one shelters.
- `moonmind/omnigent/retirement_surfaces.py` derives the legacy surfaces the
  repository actually contains — non-generic realizers, direct managed runtime
  strategies, provider-specific startup scripts, duplicate Compose services and
  profiles, and non-canonical image variables — from code and deployment
  configuration rather than a hand-maintained list.

### 5.1 Retirement classes

| Class | Meaning |
| --- | --- |
| `active_product_path` | Still the supported path for new work. |
| `new_admission_disabled` | No new plan may select it; recorded work continues. |

Plan compilation (`harness_platform/planner.py:compile_execution_plan`) and runtime
selection (`cutover.py:select_runtime` via `new_work_runtime.py`) consult the class
before admitting, so a trusted planner default, an alternate API client's
explicit selection, a schedule, and a preset are all held to the same
code-owned state. Disabling new admission never affects execution,
cancellation, cleanup, replay, or reads for already-recorded plans.

### 5.2 Removing a retained path

Removal is an ordinary change sized to the component. Delete the code,
its row, any exemption that names the row, and the tests that existed only for
it; keep the behavior tests of the surviving owner. There is no removal stage,
retirement-criterion ledger, or deployment-wide drain census. Protections stay
with the owner of the affected behavior:

- replay of recorded histories — the Temporal replay tests
  (`tests/unit/workflows/temporal/test_legacy_generation_replay.py` and the
  retained workflow histories);
- historical reads — the read-model tests of the component that serves them;
  retained embedded metadata, event cursors and captured terminal transcripts
  use the durable read owners without constructing a transport;
- active work and cleanup — the existing drain and cleanup owners
  (`codex_cutover_drain.py`, `embedded_drain.py`, the host janitors), used only
  for components that actually have active or cleanup consumers;
- credentials — the materializer and lease owners; unknown resource state is
  never permission to delete or release a credential consumer.

`assert_inventory_is_complete` also rejects a row whose surface is gone, so a
deletion removes its row in the same change rather than leaving a stale entry.

### 5.3 Retained components

| Shim / retained component | Owner record | Class today |
| --- | --- | --- |
| Direct Codex launch strategy | `omnigent.legacy.direct_codex_launch` | `active_product_path` |
| Direct Claude launch strategy | `omnigent.legacy.direct_claude_launch` | `active_product_path` |
| `ManagedRuntimeLauncher` for the direct strategies | `omnigent.legacy.managed_runtime_launcher` | `active_product_path` |
| Direct Codex `MoonMind.AgentSession`, session runtime and Docker controller | `omnigent.legacy.direct_agent_session` | `active_product_path` |
| Deprecated generic host image variables | `omnigent.legacy.host_image_variable_alias` | `active_product_path` |
| Deprecated OpenCode image variable | `omnigent.legacy.opencode_host_image_alias` | `active_product_path` |
| Pi host image variable | `omnigent.legacy.pi_host_image_alias` | `active_product_path` |
| `native_ui_compat.py` chat projection | `omnigent.legacy.native_ui_compat` | `active_product_path` |

Direct Codex/Claude and AgentSession adapters retain recorded work, opaque
serialized plans, native extension inputs/direct targets, Profiles without exact shared
translation, and native context or options without an equivalent plan handoff.
Codex/Claude pool steps resolve their account at dispatch through the existing
Profile Manager, then bind that account's immutable plan to the same fenced slot.
Preparation retries reuse the plan, recorded Skill manifest and selected account's
model-resolution evidence without another account choice or capacity unit. Later
Profile edits cannot change that evidence; ordinary explicit metadata keeps its
existing precedence over tier defaults. Codex same-epoch successors carry the selected
account, durable context and verified saved workspace through the parent,
including Continue-As-New. Restoring a verified saved workspace on a continuation
or parent-owned retry replaces its inherited scratch source; replay preserves
older recorded requests. Old parents without this handoff keep native dispatch.
After a drained shared turn reports provider cooldown, new parents restart the
logical step through its existing retry owner and remaining budget. The parent
verifies and adopts the child's scoped
canonical save after worker cleanup; it does not recapture the deleted tree or
rebind the delivered runtime journal. Cancellation starts no successor. Failed save
adoption stops automatic restart and retains its checkpoint reference in recovery
diagnostics. A selected Profile that cannot restore this save reports the missing
capability instead of starting from different inputs. A replacement grant for
that same logical step and account recovers its Profile configuration through the
canonical checkpoint's verified plan; fresh steps and different selected accounts
admit current settings. A replay patch preserves older request payloads.
Fresh native steps with equivalent configuration use this same fenced handoff
after fixed, implicit or pool account selection, without rewriting their stored
submission. Older parents without the handoff retain their recorded commands.
Supported tier publication and repository-capability defaults retain native
explicit-parameter precedence in the plan's recorded tier resolution. Same-step,
same-account recovery preserves supplied and absent defaults without consulting
current tiers. Older plans can recover settings from their retained source grant;
missing or conflicting evidence stops recovery instead of guessing. Repository
work-branch parameters retain authoring metadata in that handoff; physical
checkout branches come from the workspace specification on both paths.
Invalid repository metadata and untranslated native context/workspaces,
environment overrides and other tier options retain their adapter.
An admitted shared context cannot fall back to a fresh direct session when a
later Profile edit introduces unsupported settings; the step reports that gap.
Fixed step accounts, harnesses and distinct model/effort pairs have
their own immutable plans compiled by the same admission owner, including commit-message
tier defaults with explicit step parameters taking precedence. Successors retain
those admitted launch selections; fresh scheduled occurrences resolve current defaults.
Both shared publication paths use the admitted commit message only for new commits,
leaving existing commits and verified publication retries intact. Blank messages keep
the default subject without changing the original task instruction.
Model-only steps retain their existing readmission drift checks and cannot adopt
new launch defaults during recovery.
Repository-authority changes and other untranslated native options keep their adapter. These plans are linked
through the existing artifact owner and carried across successor attempts.
An explicit root rollout target constrains the root plan; independent steps
validate their own targets against their selected Profiles.
The immutable root plan binds each complete step handoff by digest and retains
its verified artifact inputs for saved schedules. Admission, planning and
continuation reject a replaced or missing handoff. Retained plans
without this field preserve their original bytes and plan identities.
Frozen successors retain each step's saved Profile and provider configuration
snapshots. Exact reruns inherit their admitted artifact grants before Temporal
can start.
Generated remediation, verification and evidence retries inherit their controller's
admitted step plan; a replay patch preserves older histories' recorded requests.
Schedule admission shares ordinary submission's step ID defaults and aliases.
Fresh scheduled occurrences resolve current step configuration; their cache
tracks Profile tiers, resolved configuration versions and selected policy authority,
excluding generated plan refs.
Replacing a root without specialized steps explicitly clears prior handoffs.
Equivalent product submissions,
native defaults and same-account step aliases use the shared route, including
persisted schedule steps; selected harness policy is checked before translation.
Fresh serialized canonical agent workflows use the same product admission compiler;
their title, failure policy and accepted retry identity survive translation.
Raw deferred step-account matching and tool-only boundary validation remain intact.
Replaced defaults never select ambient credentials.
OAuth-home delivery attaches the enrollment-owned volume. Codex/Claude nonsecret
shell settings use the existing project-value guard and admitted configuration
artifact for OAuth and API-key Profiles, retaining exact literal values.
Newly admitted OAuth and API-key environment templates, including single-provider
Codex Responses configurations, carry only workspace
and runtime support-directory context, rendered through the shared Profile template renderer
after the attempt's native workspace path is known. Recorded literal values keep
their original interpretation. Native executable wrappers restore values after child environment filtering
without overriding private HOME or editing the enrolled home. Codex OAuth
preferences and nonsecret files authored for a run-support or enrolled HOME use
the same artifact and renderer in the native owner's private app-server HOME.
Selected inherited instruction links become private files; untouched entries stay
visible. Environment templates referencing those files bind to that private HOME
at native launch; generic support paths and recorded literals retain their meaning.
Custom Responses API-key Profiles also bind canonical environment references to
admitted nonsecret files inside the declared Codex HOME at native launch. These
private templates are validated both in frozen configuration and by the portable
launcher. Undeclared files, mixed private/generic references and ambiguous paths
with dot segments or repeated separators retain the direct route.
Credential, hook, cache and history paths cannot be replaced. Native
model/provider/tool authority and enrollment remain intact. Protected settings,
uncarried tool/auth configuration and other HOME choices retain their adapter
until an equivalent handoff exists.
Recorded plans retain their original materializer identity.
Codex runs only on the shared realizer. Recorded `codex-profile-bound@1` plans
and profiled requests without a plan fail closed with a typed
`OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE` result. The legacy session supervisor
launches no host but still stops hosts it recorded. Credential maintenance
validates OAuth homes through the credential-only validator in
`host_services/oauth_credential_validation.py` and reclaims ledger hosts
through `host_services/legacy_host_containers.py`.
API-key Codex and Claude Profiles that translate exactly, including MiniMax's
declared Claude environment bundle and Codex Responses file configuration, run
on the shared lifecycle with their endpoint, model pins and non-secret tuning.
New plans reference a verified artifact containing those settings per credential
slot, so queued starts and frozen successors keep their admitted configuration
after a Profile edit. Refused successor cleanup preserves the original artifact.
Fresh scheduled occurrences resolve current settings without changing recorded
step selections.
Secret values still resolve
after acquisition under the existing credential-generation fence. Older plans
without this snapshot retain their recorded interpretation.
Codex reasoning summaries, verbosity, context and compaction limits, personality,
developer instructions, project-instruction discovery and history preferences reach
its private session config for OAuth and API-key Profiles; native Codex validates
and normalizes these values. API-key Codex Profiles can carry preferences
without declaring another provider and add nonsecret files within their private
HOME, including global instructions. Their declared key and endpoint stay selected.
Codex model-default suppression is frozen with the selected Profile: omitted
models and an explicitly repeated Profile default delegate to the native harness,
while different explicit models and selected tiers keep their pins. The exact
credential-bound host must advertise one native default or report a configured default
through its public native launch/CLI read. Hidden configured models keep their
meaning; missing or ambiguous defaults remain failures.
The admitted comparison model and the observed native default remain distinct
in launch evidence. The same
renderer serves direct and packaged launches; the native wrapper writes only into
its private home, retains frozen settings and reuses a completed projection on
process restart. An interrupted append stops visibly until step recovery provisions
a fresh home, preserving the original credential and history owners. Native Profile aliases translate explicit model overrides once during shared
compilation, using the same lookup as direct launch. Resolved defaults and tiers
keep their selected model.
Tool/authentication and launch settings
without an equivalent projection retain their native route.
Explicit same-epoch Codex context reuse forks durable native history through
the shared lifecycle while keeping each attempt's resources and cleanup fenced.
Credential maintenance drains
a Profile's generic hosts through the generic host janitor before mutating its
OAuth home. The durable consumer inventory is checked first, so Profiles without
generic consumers do not require an execution plane for credential maintenance.
Each live row's `missingOutcome` names its concrete remaining consumer; the Codex/Claude CLIs and Node stay in
the application image until those rows are deleted.

Shared executions render leading commands through the selected harness after
Skill and repository context is prepared. Native Claude receives the command
first; harnesses without command transport receive the complete authored text
as literal input. Retries reuse the recorded prepared-message artifact.

One enforced-boundary exemption is owned by a row above: `omnigent_catalog.py`'s
in-handler readiness projection reading the materializer descriptor registry
(`omnigent.legacy.native_ui_compat`).

### 5.4 Obsolete configuration

`OBSOLETE_CONFIGURATION` is the one deprecate-then-reject table for retired
environment names. Each row names its replacement (or says to remove the
variable) and its deletion owner: the retirement row whose code still honors it,
or its own removal condition once nothing reads it. API and worker startup check
it against what settings read, the process environment over `.env`: during a
deprecation window a supplied value warns naming the replacement; after removal
startup fails. Names match case-insensitively. A blank value, the
shipped template default, or a value the release controller rendered into
`.env.deploy` counts as omitted.

The generic host-image aliases and `OMNIGENT_OPENCODE_HOST_IMAGE_REF` are in
their deprecation window and still honored; the Pi alias is not deprecated.
Names nothing reads any more (`MINIO_ENDPOINT`, `MINIO_BUCKET`,
`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `TEMPORAL_ARTIFACTS_ROOT`, the user
Workflow cutover paths, `MEMORY_LONG_TERM`, and the retired rollout controls
for publication recovery, control-stop continuation and Live Logs) fail startup
for one release, since ignoring a credential alias would silently fall back to a
default and a kept shadow or canary setting would silently go live. Each
retired control's previous default still counts as omitted, in any spelling
settings parsed as that boolean (`0`, `off` and `no` for false).

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
