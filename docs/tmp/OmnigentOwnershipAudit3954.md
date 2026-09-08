# Omnigent caller-backed ownership audit — #3954

**Document Class:** Imperative working document
**Working Type:** Status / Checklist Tracker
**Status:** Audit recorded; test execution blocked by container infrastructure
**Updated:** 2026-09-08
**Issue:** [MoonLadderStudios/MoonMind#3954](https://github.com/MoonLadderStudios/MoonMind/issues/3954)
**Canonical Target:** [Module architecture](../Omnigent/OmnigentModuleArchitecture.md), [Bridge contract](../Omnigent/OmnigentBridge.md), [Workflow Chat](../UI/WorkflowChatPanel.md), [Secrets System](../Security/SecretsSystem.md)
**Delete/Archive Trigger:** Archive after issue verification retains this audit, its disposition, and the eventual test results as durable evidence. Re-audit affected rows when the upstream pin or their production callers change.

## Scope and result

This implements the bounded audit backlog in the `PARTIALLY_IMPLEMENTED`
assessment for #3954. The baseline is MoonMind
`e3a800f67f42e0187ff59cd5a46d8eb6a27b5ee2`. The issue's earlier review revision
is historical context, not the checkout used for these conclusions.

The Omnigent submodule was initialized and inspected at the unchanged gitlink
`f04b0354fb5344c1ea8b92795ceb6760a9ad7595`; both its package and Python client
declare version **0.12.0**. This is source-contract inspection, not live host
qualification. The trusted brief and assessment remain in
`artifacts/github-issue-implement-{brief,assessment}.json`.

Fourteen candidate groups below cover the issue's suspected wire, presentation,
catalog, lifecycle, and state overlap, including all **29** current
`omnigent_*` ORM tables in `api_service/db/models.py`. Grouping follows actual
callers and shared retention boundaries, not names or module size. Shared
Provider Profile, secret, capacity, workspace, and artifact stores are also
identified where they own a dependency.

**Disposition: zero production-code, table, or field removals.** Upstream frame
codecs and native presentation are already delegated. The remaining inspected
representations have MoonMind consumers or governance semantics without a
verified equivalent replacement at this pin. No SDK migration, data drain,
runtime retirement, or new compatibility framework is justified by this audit.
Three additional stream-to-journal regression cases preserve a concrete reason
the current transport cannot simply be replaced by the SDK parser. Malformed
SSE frames remain covered at the client parse contract; the bounded batch
activity degrades transport parse errors to `readStatus="unavailable"` while
the authoritative snapshot remains terminal authority (residual, see T1).
Runtime behavior, generated schemas, UI code, and workflow payloads are unchanged.

## Pinned upstream contracts inspected

All upstream paths below refer to the gitlink above. A supported upstream
operation is not by itself an equivalent replacement for MoonMind governance.

| Ref | Verified upstream surface | Replacement limit |
| --- | --- | --- |
| U1 | [Python client](../../omnigent/sdks/python-client/omnigent_client/_client.py), [SessionsNamespace](../../omnigent/sdks/python-client/omnigent_client/_sessions.py): public `create`, `create_from_agent_id`, `get`, `post_event`, `resolve_elicitation`, `interrupt`, `stream`. [Core routes](../../omnigent/omnigent/server/routes/sessions/routes_core.py) and [event routes](../../omnigent/omnigent/server/routes/sessions/routes_events.py) supply `/v1/sessions`, session snapshots, event POST, and SSE. | The SDK has real session APIs; it is not dismissed as nonexistent. Its stream parses typed envelopes and skips malformed/unknown events. MoonMind needs bounded raw-event capture and critical-drift rejection. Its top-level client creates its own HTTP client; `SessionsNamespace` accepts an injected client but does not provide the full governed inventory, cleanup, and resource surface. See O1. |
| U2 | [Host frames](../../omnigent/omnigent/host/frames.py): `decode_host_frame`, `encode_host_frame`, host hello/launch/stop/result dataclasses. [Runner tunnel frames](../../omnigent/omnigent/runner/transports/ws_tunnel/frames.py): upstream tunnel message codec. | The codecs implement wire mechanics. They do not authorize MoonMind owners, credential generations, correlation claims, or byte budgets. MoonMind already executes these upstream implementations. |
| U3 | [Native session client](../../omnigent/web/src/lib/sessionsApi.ts), [updates socket](../../omnigent/web/src/lib/sessionUpdatesSocket.ts), and U1 routes. The [network fixture](../../tests/fixtures/omnigent/native_ui_network_contract_v1.json) binds 45 reviewed routes and 12 source-file digests to this commit. | Upstream owns rendering and transport shapes. Browser authorization, identity virtualization, outbound scans, and post-cleanup evidence remain binding-scoped MoonMind operations. A global upstream browser URL is not a substitute. |
| U4 | [Harness route](../../omnigent/omnigent/server/routes/harnesses.py) `GET /v1/harnesses` returns `data` and `setup_steps`; [built-in agent route](../../omnigent/omnigent/server/routes/builtin_agents.py) `GET /v1/agents` has bounded cursor pagination; [host routes](../../omnigent/omnigent/server/routes/hosts.py) expose `/v1/hosts` and host-specific model options, credential detection/store, and harness install. | These are current endpoint observations and upstream host operations. They do not supply MoonMind's immutable catalog refs, trust approvals, profile usage references, exact-host admission, or enrollment-generation authority. |
| U5 | [Resource routes](../../omnigent/omnigent/server/routes/sessions/routes_resources.py) expose session/workspace content; [event routes](../../omnigent/omnigent/server/routes/sessions/routes_events.py) expose session deletion; U1 supplies stop/interrupt events. [SDK LocalServer](../../omnigent/sdks/python-client/omnigent_client/_server.py) launches and stops a local subprocess. | Provider resources and subprocess termination do not attest MoonMind artifact persistence, repository publication, lease release, owned-container absence, or preservation of enrollment-owned credential homes. |

## Caller-backed ownership table

`S*` references identify concrete persisted consumers in the next table. `T*`
references identify executable coverage below; they are **test-source evidence,
not passing results in this run**. Every retained row states the owner of the
decision separately from the owner of the upstream side effect.

| Candidate / classification | Actual production entrypoint and owned state or decision | Persisted consumers | Pinned upstream contract | Surviving owner / disposition | Coverage |
| --- | --- | --- | --- | --- | --- |
| O1 HTTP/SSE wire handling — mechanics plus governance | [Production composition](../../moonmind/omnigent/production.py) `build_generic_omnigent_execution_services` and [bridge composition](../../api_service/api/routers/omnigent_bridge_composition.py) inject [OmnigentHttpClient](../../moonmind/workflows/adapters/omnigent_client.py); `run_omnigent_execution` and `OmnigentBridgeProxy` consume it. Owns bounded transport, credential/header isolation, failure classification and raw stream delivery. | S7–S9 and S11 consume results; the pool itself is process-local. | U1/U4/U5; SDK overlaps individual operations but does not establish equivalent raw-stream/error/budget behavior. | Retain the existing HTTP adapter and [transport pool](../../moonmind/omnigent/transport.py). Upstream owns endpoint semantics; MoonMind owns its transport limits and admission. No second SDK path added. | T1, T8 |
| O2 Host/runner frame definitions — upstream mechanics already delegated | [EmbeddedHostChannelRegistry](../../moonmind/omnigent/embedded_host_channel.py) constructs [OmnigentHostProtocolAdapter](../../moonmind/omnigent/host_protocol_adapter.py) and calls [runner_frames](../../moonmind/omnigent/runner_protocol_adapter.py). Correlation, direction, byte-size, and host-version checks surround the upstream codec. | S7, S11–S12 bind channel ownership; channel futures are process-local. | U2, loaded from the pinned source rather than a separately installed package. | Upstream owns serialization/dataclasses; retain the MoonMind admission/channel boundary. There is no local copied frame schema to remove. | T2 |
| O3 Chat presentation — upstream UI plus MoonMind facade/read model | [WorkflowChatNative](../../frontend/src/entrypoints/WorkflowChatNative.tsx) fetches the authorized binding and mounts its URL; [_serve_native_ui](../../api_service/api/routers/omnigent_native_ui.py) serves stock assets through [native_ui](../../moonmind/omnigent/native_ui.py). [Workflow chat facade](../../moonmind/omnigent/workflow_chat_facade.py) controls routes/capabilities. | S7, S9 and captured final snapshots in S10/S13; generated `WorkflowChatBinding` is a facade contract. | U3. | Upstream owns the active transcript/composer. Retain scoped bootstrap, allowlist, terminal continuation actions, and legacy read-only evidence view. They do not implement a second ordinary composer. | T3 |
| O4 Harness catalog loading — observation cache, immutable evidence, trust authority | [OmnigentHarnessCatalogService.synchronize](../../moonmind/omnigent/harness_platform/catalog_service.py), composed in `production.py`, turns authenticated inventories into build-bound snapshots. [Planning service](../../moonmind/omnigent/harness_platform/planning_service.py) resolves their refs. | S4–S6, S13. | U4 exposes inventory, not immutable MoonMind trust or retained plan dependencies. | Upstream owns installed inventory. MoonMind owns trust and freshness admission; catalog snapshots remain downstream observations with protected pinned history. | T4 |
| O5 Agent catalog projection — bounded read model | [Agent-profile router](../../api_service/api/routers/omnigent_agent_profiles.py) `_refresh_upstream_projection` calls [synchronize_upstream_inventory](../../api_service/services/omnigent_agent_profile_service.py); bootstrap, smoke, and [selection service](../../api_service/services/omnigent_agent_profile_selection.py) read the projection. | S2–S3. | U4's paginated built-in-agent list is the source. | Retain endpoint/version provenance, availability and last-success/last-attempt timestamps. No local agent registration authority is substituted for upstream inventory. Combining this read model with O4 requires consumer migration, not a table-name match. | T1, T4 |
| O6 Agent/Provider Profiles and policies — MoonMind authority plus immutable evidence | `create_profile`, `create_version`, `activate`, `resolve_snapshot` in the [profile router](../../api_service/api/routers/omnigent_agent_profiles.py); [policy service](../../api_service/services/omnigent_policies.py) and [planning service](../../moonmind/omnigent/harness_platform/planning_service.py) bind authorization and policy before launch. | S1–S2, S5, S13; shared Provider Profiles and slot leases. | U1 agent/session fields and U4 credential routes describe provider inputs; they do not replace owner-scoped MoonMind policy/version/usage admission. | Retain MoonMind profile selection, authorization and immutable snapshots. Upstream executes the admitted agent/model configuration. | T4, T5, T8 |
| O7 Execution plans/runtime bindings — immutable authority and fenced control | `OmnigentExecutionPlanningService.plan` uses [plan/usage stores](../../moonmind/omnigent/harness_platform/stores.py); [GenericOmnigentHostRealizer.execute](../../moonmind/omnigent/realizers/generic_host.py) consumes the plan and advances the binding. | S5–S6, S7, S11, S13. | U1/U4 have session/host IDs, not the execution-plan digest or MoonMind revision/generation contract. | Retain immutable plan ownership and fenced execution-scoped binding. Provider IDs are references, not competing execution identities. | T5 |
| O8 Bridge/canonical session records and chat aliases — control plus attempt/read projection | [Bridge store](../../moonmind/omnigent/bridge_store.py) `get_or_create`/`resolve_chat_binding`, [canonical store](../../moonmind/omnigent/control_plane/repositories.py), and [turn commands](../../moonmind/omnigent/control_plane/turn_commands.py) `attach_provider_session` bind workflow/run/step to the provider session. | S7, S9–S10; API chat reads and Temporal activities retain these refs. | U1 owns the provider session snapshot, not MoonMind workflow ownership or historical browser-binding lookup. | Canonical repositories/turn commands own session authority; bridge rows retain attempt identity/evidence and compatibility consumers. Retain both until those consumers are migrated and drained; do not infer independent mutation authority from overlapping fields. | T3, T5, T6 |
| O9 Turn commands, retry/cancel and reconciliation — durable MoonMind decisions | [CanonicalTurnCommandService.claim/settle](../../moonmind/omnigent/control_plane/turn_commands.py), wired in both realizers by `production.py`, governs mutations. [Session supervisor](../../moonmind/workflows/temporal/workflows/omnigent_session.py) and its [activities](../../moonmind/workflows/temporal/activities/omnigent_session_activities.py) own durable scheduling/evidence handoffs. | S7–S8, S10; Temporal histories carry immutable refs/fencing metadata. | U1 can POST events/interrupt; that is not a durable cross-producer command claim, retry owner, or terminal-evidence contract. | MoonMind owns command identity, admission and settlement; Omnigent owns admitted provider turn execution. Retain reconciler, journals and supervisor. | T5, T8 |
| O10 Event journals and timeline — evidence/read models | `run_omnigent_execution`/bridge ingestion call [build_omnigent_bridge_event](../../moonmind/omnigent/bridge_events.py) and bridge-store append methods; [timeline service](../../api_service/services/omnigent_session_timeline_service.py) projects the canonical records. | S8–S9 and artifact-backed raw/normalized journals. | U1 SSE and U3 updates are provider events; they lack MoonMind sequence/actor/workflow scope and retained replay after host cleanup. | Upstream owns emitted observations; MoonMind owns bounded durable capture and normalized projections. A journal or timeline is not a new session controller. | T1, T3, T6 |
| O11 Host lifecycle, capacity and leases — resource authority | `GenericOmnigentHostRealizer._execute_lifecycle/_cleanup` uses [GenericOmnigentHostRuntime](../../moonmind/omnigent/host_runtime.py), [host lease repository](../../moonmind/omnigent/host_leases.py), and [host cleanup service](../../moonmind/omnigent/host_services/cleanup.py); janitors recover incomplete cleanup. | S6, S10–S11; shared machine-capacity reservations and Provider Profile leases. | U4 reports/controls upstream hosts; U5 `LocalServer` manages a local process, not MoonMind's leased Docker workspace and host. | Retain MoonMind launch, exact-host admission, capacity and cleanup ownership. Omnigent owns its host/runner mechanics. Heartbeat or process exit cannot stand in for cleanup evidence. | T5, T7, T8 |
| O12 Credential materialization/host authentication — MoonMind authority | [OmnigentCredentialProvisioningService.materialize_all/cleanup_all](../../moonmind/omnigent/credential_materializers.py), composed in `production.py`, resolves selected profile credentials. [Host auth store](../../moonmind/omnigent/host_auth_store.py) persists embedded-host auth generations. | S11–S13; shared managed secrets, OAuth sessions, Provider Profiles and leases. | U4 has host credential detection/store, and U2 authenticates a host channel. Neither transfers MoonMind enrollment ownership or grants use of another runtime's credentials. | Retain credential refs, generation fences and materializer ownership. Run-owned material may be reclaimed; enrollment-owned homes remain owned by enrollment. | T2, T7 |
| O13 Capture manifests, terminal results, publication and cleanup evidence — immutable evidence plus side-effect authority | [Bridge artifact gateway/harvester](../../moonmind/omnigent/bridge_artifacts.py), realizer `_publish_repository/_cleanup`, [workspace publication](../../moonmind/omnigent/workspace_publication.py), and [session drain](../../moonmind/omnigent/session_cleanup.py) bridge provider output to durable MoonMind results. | S7–S10, S13; shared artifact/link/pin records, publication and checkpoint refs. | U5 reads provider files/stops sessions; it does not publish a MoonMind repository result or preserve authorized evidence after provider cleanup. | Retain MoonMind artifact and publication authority. The realizer claims canonical cleanup before drain/host/materializer cleanup and releases provider capacity last. Auxiliary telemetry/log errors do not redefine primary completion. | T3, T7, T8 |
| O14 Embedded/profile-bound/direct lifecycle overlap — retained execution and replay dependencies | [Bridge composition](../../api_service/api/routers/omnigent_bridge_composition.py) selects proxy or [embedded facade](../../moonmind/omnigent/bridge_embedded.py); production registers `CodexProfileBoundRealizer` beside the generic realizer. [Profile-bound coordinator](../../moonmind/omnigent/profile_bound_execution.py), OAuth host runtime/janitor and managed strategies have inventoried callers. | S7, S9, S11–S12; old Temporal histories, pending publication/checkpoint work and historical readers. | U1/U2/U5 supply runtime mechanics, not proof of full generic replacement for each retained support row. | Retain exact components under the existing [retirement inventory](../../moonmind/omnigent/legacy_retirement.py). Do not change admission/rollback classes without qualification, drain, replay and historical-read evidence. | T2, T8, T9 |

## Persisted consumer inventory

All table names here have the `omnigent_` prefix; it is omitted below for
readability. The list is exhaustive for the 29 current Omnigent ORM tables,
not a declaration that all readers have drained. Each row identifies concrete
surviving consumers that already prevent deletion. No table/field is selected
for removal, so no empty-consumer or completed-migration claim is made.

| Ref | Tables | Production writers and readers; retention/ownership consequence |
| --- | --- | --- |
| S1 | `policies`, `policy_versions`, `policy_events` | `api_service/services/omnigent_policies.py` writes policy versions/audit events; policy routes, profile validation and launch-policy resolution read them. Immutable policy refs and their audit history cannot be replaced by current upstream defaults. |
| S2 | `agent_profiles`, `agent_profile_versions`, `agent_profile_audit_events`, `agent_profile_usage` | Profile router owns version/activation/audit mutations; `omnigent_agent_profile_selection.py` binds usage; execution, schedule and checkpoint creation/read paths consume pinned versions. Deletion guards and historical usage require these rows even after an upstream agent disappears. |
| S3 | `upstream_agent_projections` | `synchronize_upstream_inventory` writes bounded observed metadata; profile selection, bootstrap, smoke validation and profile routes read it. Identity includes endpoint/upstream ID/version; a five-minute freshness check and recorded failures distinguish last-known data from launchable data. Missing inventory marks unavailable rather than deleting history. |
| S4 | `harness_catalog_snapshots`, `harness_trust_records` | Catalog service/repository persists observations/trust; planning and host resolution load by immutable ref. Pruning keeps the newest 50 observations per endpoint plus refs pinned by trust records, Agent Profile versions, and execution plans. Refresh must not rewrite the authority snapshot of an existing run. |
| S5 | `execution_plans`, `execution_plan_usages` | `harness_platform/stores.py` and `planning_service.py` persist compiled plans and bind request usages; API admission and worker realizer lookup read the same refs. Plans pin catalog, policy, skills and credential binding sets. Replanning pending work against latest inventory would change authority. |
| S6 | `runtime_bindings` | `DbRuntimeBindingStore` in `harness_platform/stores.py` persists staged bindings; the generic realizer and recovery paths read/advance revision and generation. Old serialized binding shapes retain digest coverage. A provider session snapshot cannot reconstruct these fences. |
| S7 | `bridge_sessions`, `sessions`, `chat_binding_aliases` | Bridge store and canonical repositories persist attempt mappings, canonical ownership and aliases; session activities, facade authorization, timeline and terminal chat resolution read them. Bridge artifact refs, published-work metadata and old binding IDs remain retained-data consumers. Canonical turn commands attach the upstream session before dispatch settlement. |
| S8 | `turn_attempts`, `commands`, `observations`, `reconciliation_decisions` | `control_plane/repositories.py`, turn-command service and supervisor activities persist fenced claims, decisions and observations; retries, reconciliation, admission and timeline read them. These are MoonMind command/control evidence, not upstream event objects. Pending Temporal work remains a consumer. |
| S9 | `bridge_session_events` | Bridge-store append/deduplication methods allocate ordered journal entries; facade SSE/replay and capture read them. Provider event IDs assist deduplication; MoonMind sequences and workflow scope remain necessary. Terminal reconciliation does not delete live rows. |
| S10 | `cleanup_authority` | `control_plane/cleanup_authority.py` and canonical repositories claim/complete cleanup; realizers, turn admission and janitors share the fence. Terminal result refs and host/credential cleanup obligations outlive an upstream stop acknowledgment. |
| S11 | `oauth_host_bindings`, `oauth_host_leases`, `host_bindings_v2`, `host_leases_v2` | Legacy `oauth_hosts.py`/`oauth_host_runtime.py` and generic `host_leases.py` write their own resource generations; production realizers, catalog readiness and their janitors read them. Both generations still have admission/execution/cleanup consumers. Credential homes, owned hosts and capacity reservations must drain under the owning generation before any future drop. |
| S12 | `host_auth_profiles` | `host_auth_store.py` writes activation/rotation/revocation; bridge composition and embedded-host admission read the active profile. References to managed secret generations are auth authority; upstream server users or credential detection do not replace it. |
| S13 | `credential_runtimes`, `credential_binding_sets` | Planning persists binding sets; credential provisioning persists run handles and loads cleanup handles; host resolution and realizer recovery read them. Shared Provider Profiles/enrollment own reusable homes, while handles identify run-owned cleanup. Artifact refs recording provisioning/cleanup remain durable evidence. |

### Data and version interaction

The removal set is explicitly **empty**, including generated OpenAPI types,
database migrations, legacy image fields and UI binding fields. API and worker
versions therefore continue to exchange the same plan refs, binding digests,
session IDs, activity payloads and artifact manifests; no cutover, backfill,
credential deletion or history rewrite occurs in this change.

Before selecting any of S1–S13 for a later removal, the owner must enumerate the
remaining readers/writers at that revision, disable the relevant new admission,
migrate or drain pending work, account for mixed API/worker deployment and
recorded Temporal histories, preserve historical artifact lookup, and prove
resource/credential cleanup ownership. A successful unit test is not proof
that a deployment has zero active leases or retained readers. No deployment
drain evidence was available or inferred here.

## Residual dependencies and concrete removal criteria

| Residual | Condition required before removal; coordination |
| --- | --- |
| O1 HTTP/SSE client | A reviewed public API at an exact upstream pin must preserve bounded raw frames, redacted errors, critical drift rejection, injected pool ownership, per-operation timeouts, header isolation, and required inventory/resource/cleanup calls. Run T1/T8 across the replacement boundary, then delete superseded callers together. Importing private SDK parsing helpers or keeping two transports would not establish equivalence. |
| O2/O3 upstream source and native facade | Upstream already owns mechanics/presentation. Keep source pin and native route-digest review. A new UI host API may reduce rebasing code only after scoped HTTP/SSE/WebSocket, unknown-route, identity and post-cleanup tests pass. Retain MoonMind authorization regardless; coordinate #3956. |
| O4/O5 separate catalog projections | A unified observation path may remove repeated collection only after profile selection, smoke/bootstrap, planner trust/freshness and immutable historical refs share a verified source. Preserve S2/S4/S5 pins and the difference between observation and trust. Coordinate projection writes with #3946; current upstream inventory alone does not meet this condition. |
| O8/O10 bridge projections | Canonical session/command owners survive. Any field reduction requires migration of bridge reads, terminal artifacts, chat binding lookup and journal consumers, plus old-history replay evidence. Coordinate session ownership with #3935 and projection writers with #3946; no new parallel control state is introduced here. |
| O11/O14 retained lifecycle generations | Apply the existing inventory/guards, not a new framework. `profile_bound_realizer`, `profile_bound_execution`, `oauth_host_runtime`, `bridge_execution`, `bridge_persistence`, and `native_ui_compat` still have active-product dependencies; `oauth_host_janitor` is cleanup-only, `oauth_session_activities`/`provider_profile_capacity_consumer` support active execution, and `managed_session_replay_patches` retains replay. The same inventory records direct Codex/Claude, startup/image surfaces, migration inventory and historical runbook consumers. Generic support qualification, admission/rollback closure, active-owner drain and retained-read disposition must pass for each affected row before deleting code, configuration or fields. Coordinate #3925 and #3835. |
| O12/O13 credential and evidence ownership | No upstream deletion API qualifies as a replacement. Preserve enrollment-owned credentials and retained artifacts. Only run-owned resources with generation-matched cleanup evidence may be reclaimed by their current owner. Publication/checkpoint consumers must settle before workspace release. |

## Boundary evidence and validation

The following existing tests are the proof obligations for retained behavior,
not a claim of cross-host equivalence or live provider qualification. The new
T1 test covers the audited transport/journal handoff with the actual public
client and normalizer, substituting only the HTTP peer. It sends an unknown
event, an unknown status and a blank status before a valid completion; each
must fail before normalization can accept completion. These are the drift
cases the production Temporal handoff propagates: the batch activity
normalizes collected events outside its bounded stream-read block, so contract
errors fail the activity. Malformed JSON and non-object frames instead raise
from `stream_events()` inside `collect_bounded_batch()` and are degraded to
`readStatus="unavailable"` (the supervisor gates only on the snapshot status);
they remain covered at the client parse contract by the existing
`parse_sse_line` rejection test. This transport-parse degradation is a
recorded residual, not a passing claim for the Temporal path.

| Ref | Executable evidence and boundary covered |
| --- | --- |
| T1 | [HTTP client tests](../../tests/unit/workflows/adapters/test_omnigent_client.py): new `test_stream_to_journal_rejects_critical_drift` (three contract-drift cases); existing `test_parse_sse_line_redacts_payload_and_rejects_malformed_frames` covers malformed-frame rejection at the parse contract, plus pagination, credential-header isolation, timeout/pool and redaction tests. [Bridge event tests](../../tests/unit/omnigent/test_bridge_events.py) distinguish critical failure from bounded optional-resource diagnostics. This is why the pinned SDK's skip-on-unknown parser is not an equivalent replacement. |
| T2 | [Host codec tests](../../tests/unit/omnigent/test_host_protocol_adapter.py) execute pinned upstream frames and reject incompatible/misdirected/oversized frames; [embedded channel tests](../../tests/unit/omnigent/test_embedded_host_channel.py) cover correlation, disconnect and substituted runner identity. |
| T3 | [Native compatibility tests](../../tests/unit/omnigent/test_native_ui_compat.py) bind the source pin/digests to the route manifest; [unknown-route tests](../../tests/unit/omnigent/test_omnigent_facade_unknown_route_fails_closed.py) cover denial. [Workflow chat router tests](../../tests/unit/api/routers/test_omnigent_workflow_chat.py), including `test_non_owner_gets_non_enumerating_binding_unknown` and `test_terminal_items_page_uses_captured_snapshot_after_provider_cleanup`, cover ownership and artifact-backed terminal reads. |
| T4 | [Catalog repository tests](../../tests/unit/omnigent/test_harness_catalog_repository.py) exercise SQL persistence, fresh re-observation and pinned-history retention; [profile service tests](../../tests/unit/services/test_omnigent_agent_profile_service.py) cover projection freshness/compatibility. [Profile router tests](../../tests/unit/api/routers/test_omnigent_agent_profiles.py) and [harness-platform tests](../../tests/unit/omnigent/test_harness_platform.py) retain owner/admission coverage. |
| T5 | [Runtime binding store tests](../../tests/unit/omnigent/test_runtime_binding_store.py) cover stale generation/writer and previous binding digest shape; [canonical producer tests](../../tests/unit/omnigent/test_canonical_turn_producers.py) exercise production realizer dispatch, provider-session attachment, remediation lineage and cleanup fences. |
| T6 | [Bridge-store tests](../../tests/unit/omnigent/test_bridge_store.py) cover idempotent attempts, event replay/deduplication, immutable authority conflict, historical binding backfill and terminal refs. [Timeline API tests](../../tests/unit/omnigent/test_omnigent_session_timeline_api.py) preserve downstream read-model coverage. |
| T7 | [OAuth home tests](../../tests/unit/omnigent/test_oauth_home_materializers.py) cover Codex/Claude credential compatibility, stale generation and preservation of enrollment-owned volumes; [host cleanup tests](../../tests/unit/omnigent/test_host_cleanup_service.py) cover resource cleanup evidence. These are hermetic tests, not commands against deployment Docker. |
| T8 | [Fake-server execution](../../tests/integration/omnigent/test_execute_fake_server.py) crosses HTTP/SSE and artifact capture; [supervisor failure handoff](../../tests/integration/omnigent/test_session_supervisor_failure_handoff.py) crosses real persistence/activity invocation and primary terminal evidence; [publication journey](../../tests/integration/reliability_journey/test_omnigent_publication_semantics_journey.py) uses real Git/workspaces/publication with static/on-demand profile-bound execution, cleanup and replay. No new runtime combination is admitted by this audit. |
| T9 | [Retirement tests](../../tests/unit/omnigent/test_legacy_retirement.py) retain active-owner/replay/historical-read/rollback removal guards and [module architecture tests](../../tests/unit/omnigent/test_module_architecture.py) enforce module ownership. |

Two managed `moonmind container python-tests` submissions were made. Neither
started pytest: both returned `failureClass=infrastructure`, `exitCode=None`,
and **“container ownership could not be read from the container backend.”**
This is a provisioning/ownership-read failure, not a repository assertion
failure. Test execution remains required in a functioning backend before the
later verification/publication gate can claim passing evidence.

| Submission | Requested suite | Durable terminal evidence |
| --- | --- | --- |
| Unit | HTTP client, host codec/channel, native compatibility, catalog repository, runtime binding, canonical producers, bridge events, OAuth homes, retirement, profile service, unknown-route facade, Workflow Chat router, host cleanup, module architecture (15 files). | `container-job:db4d8bd6c9ab4b178e1e08b3b7e9d8f3`; logs `art_01M1ZATGYT4C2CBHDFX6WQBY5Y`; artifacts `art_01M1ZATH3XS1ZKFMP8GH4JGM5H`. Local transcript: `artifacts/ownership-audit-3954-unit-tests.log`. |
| Integration boundaries | All three T8 files, preserving their `integration_ci` test targets through the managed Python test wrapper. | `container-job:cc3a1844d3c44a0bb1dd01d199e84fbd`; logs `art_01M1ZATJFK5X31XE4KR58DT50G`; artifacts `art_01M1ZATJWYBDEZDJ1526KDXD5P`. Local transcript: `artifacts/ownership-audit-3954-boundary-tests.log`. |

T4 profile-router/harness-platform and T6 bridge-store/timeline suites are
additional retained source coverage; they were not included in the two
submissions. No tests are represented as passed by inspection. No direct
Docker route, provider credentials, PR, or issue mutation was used.

Local static validation resolved all 82 relative report links, matched all 29
ORM table names to S1–S13, parsed the changed Python file, and matched all 12
native-UI fixture source hashes against the initialized pin. `git diff --check`
was clean. The repository's advisory documentation checkers emitted no
findings; because their canonical-doc filter excludes `docs/tmp/`, the explicit
local link inventory supplies this working report's link validation. These
checks do not substitute for pytest execution.

## Measured outcome and acceptance traceability

| Measure | Baseline → this change | Explanation |
| --- | --- | --- |
| Candidate groups dispositioned | 0 → 14 in this audit | Each row joins production callers, state consumers, pinned upstream surface, surviving owner and tests. Existing documentation/retirement inventories remain their own authorities. |
| Current `omnigent_*` ORM tables | 29 → 29 | S1–S13 enumerate all 29. No table or field had a demonstrated replacement plus consumer-drain evidence. |
| Production code/schema/UI/config lines removed or added | 0 / 0 | Existing upstream delegation is not counted as a new reduction. No quota overrides ownership. |
| New regression cases | 0 → 3 | Transport-to-journal critical drift (unknown event/status/blank). Malformed-frame rejection stays covered at the client parse contract; transport-parse degradation in the batch activity is a recorded residual. Execution is blocked as recorded above. |
| New audit reports | 0 → 1 | This working report owns only the revision-specific findings; the bridge contract gains a scope clarification only (bridge binding store vs control-plane session authority, O8), with no behavior change. |

Reproduce the table count by reading `__tablename__` assignments beginning with
`omnigent_` in `api_service/db/models.py`; compare production changes with
`git diff e3a800f67f42e0187ff59cd5a46d8eb6a27b5ee2 -- api_service moonmind frontend`.

D1/A1 are addressed by O1–O14 and U1–U5. D2/D3/A2 are addressed by the explicit
mechanics/authority/cache/evidence dispositions and S1–S13. D5/A5 have an empty
removal set with concrete retained consumers and no invented drain proof.
D6 records residual criteria without changing the existing retirement
framework. A6 records the justified zero-removal measurement. Previously met
D4/A3/A4 remain bounded by T1–T9; test-source coverage is preserved, but passing
execution evidence remains unavailable in this environment. The later verifier
owns the issue verdict.
