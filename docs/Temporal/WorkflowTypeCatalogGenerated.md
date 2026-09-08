<!-- GENERATED FROM PRODUCTION REGISTRIES - DO NOT EDIT -->
# Temporal Workflow Type Reference (Generated)

Mechanical reference for MoonLadderStudios/MoonMind#3959, generated from the production
registration owners. Do not edit by hand; regenerate with:

```
python tools/generate_temporal_catalog.py --out docs/Temporal/WorkflowTypeCatalogGenerated.md
```

Providing owners:

- Workflows: `moonmind/workflows/temporal/workflow_registry.py`
  (`raw_workflow_registrations`, `validate_workflow_registrations`)
- Activities/fleets/routes: `moonmind/workflows/temporal/activity_catalog.py`
  (`build_default_activity_catalog`) with worker bindings in
  `moonmind/workflows/temporal/activity_runtime.py`
  (`validate_activity_catalog_runtime_bindings`)
- Worker construction: `moonmind/workflows/temporal/worker_entrypoint.py`
  and `moonmind/workflows/temporal/worker_runtime.py` via
  `moonmind/workflows/temporal/workers.py`
- Search Attributes: `moonmind/workflows/temporal/service.py`,
  `moonmind/workflows/temporal/scheduled_start.py`,
  `moonmind/workflows/temporal/workflows/run.py`, and
  `moonmind/workflows/temporal/workflows/managed_runtime_workspace_cleanup.py`
- Authored lifecycle semantics:
  `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`

Projection scope (`product` / `operator` / `excluded`) is the
registry's visibility declaration only. It is not authorization,
action capability, source readiness, or retirement status. Operator
types link to the operator surface in
`docs/Temporal/SourceOfTruthAndProjectionModel.md`; valid controls per type live in
`docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §6.2. Version-looking names (for example
`MoonMind.PublicationRecoveryV1`) are first-class registrations and
are never omitted. Conditional workflow-queue polling (start queue
plus pre-patch replay queue) is declared by
`get_workflow_poll_task_queues`; conditional registrations, when
present, are listed with their supported condition below.

## Workflow registrations

| Temporal type | Module / class owner | Projection scope | Lifecycle |
| --- | --- | --- | --- |
| `MoonMind.AgentRun` <a id="moonmindagentrun"></a> | `moonmind.workflows.temporal.workflows.agent_run.MoonMindAgentRun` | `operator` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#113-moonmindagentrun-lifecycle) |
| `MoonMind.AgentSession` <a id="moonmindagentsession"></a> | `moonmind.workflows.temporal.workflows.agent_session.MoonMindAgentSessionWorkflow` | `operator` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#115-moonmindagentsession-lifecycle) |
| `MoonMind.CheckpointBranchTurn` <a id="moonmindcheckpointbranchturn"></a> | `moonmind.workflows.temporal.workflows.checkpoint_branch_turn.MoonMindCheckpointBranchTurnWorkflow` | `operator` | [lifecycle](#moonmindcheckpointbranchturn) |
| `MoonMind.ContainerJob` <a id="moonmindcontainerjob"></a> | `moonmind.workflows.temporal.workflows.container_job.MoonMindContainerJobWorkflow` | `operator` | [lifecycle](#moonmindcontainerjob) |
| `MoonMind.ControlStopContinuation` <a id="moonmindcontrolstopcontinuation"></a> | `moonmind.workflows.temporal.workflows.control_stop_continuation.MoonMindControlStopContinuationWorkflow` | `operator` | [lifecycle](#moonmindcontrolstopcontinuation) |
| `MoonMind.ManagedRuntimeWorkspaceCleanup` <a id="moonmindmanagedruntimeworkspacecleanup"></a> | `moonmind.workflows.temporal.workflows.managed_runtime_workspace_cleanup.MoonMindManagedRuntimeWorkspaceCleanupWorkflow` | `excluded` | [lifecycle](#moonmindmanagedruntimeworkspacecleanup) |
| `MoonMind.ManagedSessionReconcile` <a id="moonmindmanagedsessionreconcile"></a> | `moonmind.workflows.temporal.workflows.managed_session_reconcile.MoonMindManagedSessionReconcileWorkflow` | `excluded` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#116-moonmindmanagedsessionreconcile-lifecycle) |
| `MoonMind.ManifestIngest` <a id="moonmindmanifestingest"></a> | `moonmind.workflows.temporal.workflows.manifest_ingest.MoonMindManifestIngestWorkflow` | `product` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#112-moonmindmanifestingest-lifecycle) |
| `MoonMind.MergeAutomation` <a id="moonmindmergeautomation"></a> | `moonmind.workflows.temporal.workflows.merge_automation.MoonMindMergeAutomationWorkflow` | `operator` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#119-moonmindmergeautomation-lifecycle) |
| `MoonMind.OAuthSession` <a id="moonmindoauthsession"></a> | `moonmind.workflows.temporal.workflows.oauth_session.MoonMindOAuthSessionWorkflow` | `operator` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#118-moonmindoauthsession-lifecycle) |
| `MoonMind.OmnigentOAuthHostJanitor` <a id="moonmindomnigentoauthhostjanitor"></a> | `moonmind.workflows.temporal.workflows.omnigent_oauth_host_janitor.MoonMindOmnigentOAuthHostJanitorWorkflow` | `excluded` | [lifecycle](#moonmindomnigentoauthhostjanitor) |
| `MoonMind.OmnigentSession` <a id="moonmindomnigentsession"></a> | `moonmind.workflows.temporal.workflows.omnigent_session.MoonMindOmnigentSessionWorkflow` | `operator` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#114-moonmindomnigentsession-lifecycle) |
| `MoonMind.PRResolver` <a id="moonmindprresolver"></a> | `moonmind.workflows.temporal.workflows.pr_resolver.MoonMindPRResolverWorkflow` | `operator` | [lifecycle](#moonmindprresolver) |
| `MoonMind.ProviderProfileManager` <a id="moonmindproviderprofilemanager"></a> | `moonmind.workflows.temporal.workflows.provider_profile_manager.MoonMindProviderProfileManagerWorkflow` | `operator` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#117-moonmindproviderprofilemanager-lifecycle) |
| `MoonMind.PublicationRecoveryV1` <a id="moonmindpublicationrecoveryv1"></a> | `moonmind.workflows.temporal.workflows.publication_recovery.MoonMindPublicationRecoveryWorkflow` | `operator` | [lifecycle](#moonmindpublicationrecoveryv1) |
| `MoonMind.UserWorkflow` <a id="moonminduserworkflow"></a> | `moonmind.workflows.temporal.workflows.run.MoonMindUserWorkflow` | `product` | [lifecycle](WorkflowTypeCatalogAndLifecycle.md#111-moonminduserworkflow-lifecycle) |

`MoonMind.ManifestIngest` retains two entry contracts as inputs to the
single registered type above: the current catalogued-Activity path and
the historical `manifest_read` / `manifest_compile` commands kept for
replay (`moonmind/workflows/temporal/workflows/manifest_ingest.py`).
They are not duplicate catalog entries.

## Workflow task queues

New workflow starts use `mm.workflow.user.v2`. The workflow fleet polls:

- `mm.workflow.user.v2`
- `mm.workflow`

A pre-patch replay queue is polled only when it differs from the
start queue (`get_workflow_poll_task_queues`); otherwise the fleet
polls the start queue alone. This conditional queue is routing
plumbing for in-flight histories, not product semantics.

## Workflow-queue handler routing

Current lane (new calls route here):

- `integration.external_adapter_execution_style`
- `integration.get_activity_route`
- `integration.resolve_adapter_metadata`
- `integration.resolve_external_adapter`

Historical-only (pre-cutover histories, no new calls):

- `checkpoint_branch.turn.mark_running`
- `checkpoint_branch.turn.persist_terminal`
- `checkpoint_branch.turn.persist_terminal_rejection`

The workflow-fleet helpers host regular Temporal activities colocated
with deterministic workflow code, not Temporal Local Activities.

## Activity catalog routes

| Activity type | Fleet | Task queue |
| --- | --- | --- |
| `agent_runtime.build_launch_context` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.cancel` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `agent_runtime.capture_workspace_checkpoint` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.cleanup_managed_runtime_files` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.clear_session` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.ensure_docker_sidecar` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.evaluate_terminal_evidence` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `agent_runtime.fetch_result` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.fetch_session_summary` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.interrupt_turn` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.launch` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.launch_session` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.load_session_snapshot` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.prepare_turn_instructions` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.publish_artifacts` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.publish_bridge_events` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.publish_session_artifacts` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.publish_terminal_checkpoint` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.reclaim_docker_storage` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.reconcile_managed_sessions` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.restore_workspace_checkpoint` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.send_turn` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.session_status` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.status` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.steer_turn` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_runtime.terminate_session` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_skill.build_prompt_index` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_skill.materialize` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_skill.query_on_demand` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_skill.request_on_demand` | `agent_runtime` | `mm.activity.agent_runtime` |
| `agent_skill.resolve` | `agent_runtime` | `mm.activity.agent_runtime` |
| `artifact.compute_preview` | `artifacts` | `mm.activity.artifacts` |
| `artifact.create` | `artifacts` | `mm.activity.artifacts` |
| `artifact.lifecycle_sweep` | `artifacts` | `mm.activity.artifacts` |
| `artifact.link` | `artifacts` | `mm.activity.artifacts` |
| `artifact.list_for_execution` | `artifacts` | `mm.activity.artifacts` |
| `artifact.pin` | `artifacts` | `mm.activity.artifacts` |
| `artifact.publish_report_bundle` | `artifacts` | `mm.activity.artifacts` |
| `artifact.read` | `artifacts` | `mm.activity.artifacts` |
| `artifact.unpin` | `artifacts` | `mm.activity.artifacts` |
| `artifact.write_complete` | `artifacts` | `mm.activity.artifacts` |
| `checkpoint_branch.turn.mark_running` | `artifacts` | `mm.activity.artifacts` |
| `checkpoint_branch.turn.persist_terminal` | `artifacts` | `mm.activity.artifacts` |
| `checkpoint_branch.turn.persist_terminal_rejection` | `artifacts` | `mm.activity.artifacts` |
| `container_job.acquire_image` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.cancel` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.cleanup` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.create_container` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.observe_container` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.project_status` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.publish_evidence` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.reconcile_container` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.remove_container` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.repair_projection` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.resolve_workspace` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.start_container` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.status` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.stop_container` | `agent_runtime` | `mm.activity.agent_runtime` |
| `container_job.submit` | `agent_runtime` | `mm.activity.agent_runtime` |
| `execution.dependency_status_snapshot` | `artifacts` | `mm.activity.artifacts` |
| `execution.notify_completion` | `agent_runtime` | `mm.activity.agent_runtime` |
| `execution.record_terminal_state` | `artifacts` | `mm.activity.artifacts` |
| `integration.codex_cloud.cancel` | `integrations` | `mm.activity.integrations` |
| `integration.codex_cloud.fetch_result` | `integrations` | `mm.activity.integrations` |
| `integration.codex_cloud.start` | `integrations` | `mm.activity.integrations` |
| `integration.codex_cloud.status` | `integrations` | `mm.activity.integrations` |
| `integration.jules.answer_question` | `integrations` | `mm.activity.integrations` |
| `integration.jules.cancel` | `integrations` | `mm.activity.integrations` |
| `integration.jules.fetch_result` | `integrations` | `mm.activity.integrations` |
| `integration.jules.get_auto_answer_config` | `integrations` | `mm.activity.integrations` |
| `integration.jules.list_activities` | `integrations` | `mm.activity.integrations` |
| `integration.jules.send_message` | `integrations` | `mm.activity.integrations` |
| `integration.jules.start` | `integrations` | `mm.activity.integrations` |
| `integration.jules.status` | `integrations` | `mm.activity.integrations` |
| `integration.omnigent.execute` | `agent_runtime` | `mm.activity.agent_runtime` |
| `integration.omnigent.oauth_host_janitor` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `integration.omnigent.profile_bound_execute` | `agent_runtime` | `mm.activity.agent_runtime` |
| `integration.openclaw.execute` | `integrations` | `mm.activity.integrations` |
| `integration.resolve_adapter_metadata` | `workflow` | `mm.workflow.user.v2` |
| `manifest.compile` | `artifacts` | `mm.activity.artifacts` |
| `manifest.write_summary` | `artifacts` | `mm.activity.artifacts` |
| `memory.apply_policy` | `integrations` | `mm.activity.integrations` |
| `memory.evaluate_proposals` | `integrations` | `mm.activity.integrations` |
| `merge_automation.complete_post_merge_github` | `integrations` | `mm.activity.integrations` |
| `merge_automation.complete_post_merge_jira` | `integrations` | `mm.activity.integrations` |
| `merge_automation.evaluate_readiness` | `integrations` | `mm.activity.integrations` |
| `merge_automation.request_automated_review` | `integrations` | `mm.activity.integrations` |
| `mm.skill.execute` | `llm` | `mm.activity.llm` |
| `mm.skill.execute` | `agent_runtime` | `mm.activity.agent_runtime` |
| `mm.skill.execute` | `artifacts` | `mm.activity.artifacts` |
| `mm.skill.execute` | `deployment` | `mm.activity.deployment` |
| `mm.skill.execute` | `integrations` | `mm.activity.integrations` |
| `mm.skill.execute` | `sandbox` | `mm.activity.sandbox` |
| `mm.tool.execute` | `agent_runtime` | `mm.activity.agent_runtime` |
| `mm.tool.execute` | `artifacts` | `mm.activity.artifacts` |
| `mm.tool.execute` | `deployment` | `mm.activity.deployment` |
| `mm.tool.execute` | `integrations` | `mm.activity.integrations` |
| `mm.tool.execute` | `llm` | `mm.activity.llm` |
| `mm.tool.execute` | `sandbox` | `mm.activity.sandbox` |
| `oauth_session.cleanup_stale` | `artifacts` | `mm.activity.artifacts` |
| `oauth_session.ensure_volume` | `agent_runtime` | `mm.activity.agent_runtime` |
| `oauth_session.mark_failed` | `artifacts` | `mm.activity.artifacts` |
| `oauth_session.prepare_credential_maintenance` | `agent_runtime` | `mm.activity.agent_runtime` |
| `oauth_session.register_profile` | `artifacts` | `mm.activity.artifacts` |
| `oauth_session.revalidate_bound_host` | `agent_runtime` | `mm.activity.agent_runtime` |
| `oauth_session.start_auth_runner` | `agent_runtime` | `mm.activity.agent_runtime` |
| `oauth_session.stop_auth_runner` | `agent_runtime` | `mm.activity.agent_runtime` |
| `oauth_session.update_status` | `artifacts` | `mm.activity.artifacts` |
| `oauth_session.update_terminal_session` | `artifacts` | `mm.activity.artifacts` |
| `oauth_session.verify_cli_fingerprint` | `agent_runtime` | `mm.activity.agent_runtime` |
| `oauth_session.verify_volume` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.admit_generic_host_capacity` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `omnigent.ensure_host` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.ensure_provider_profile_lease` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.ensure_provider_session` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.evaluate_session_admission` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.harvest_evidence` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.heartbeat_host_lease` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `omnigent.load_failure_authority` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.load_reconciliation_inputs` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.observe_snapshot` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.persist_decision` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.persist_failure` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.persist_signal_intents` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.prepare_child_execution_plan` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.publish_workspace` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.read_event_batch` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.record_terminal` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.release_leases` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `omnigent.resolve_intent` | `agent_runtime` | `mm.activity.agent_runtime` |
| `omnigent.stop_host` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `omnigent.stop_provider_session` | `agent_runtime` | `mm.activity.agent_runtime.control` |
| `omnigent.submit_turn` | `agent_runtime` | `mm.activity.agent_runtime` |
| `plan.check_preset_capabilities` | `llm` | `mm.activity.llm` |
| `plan.generate` | `llm` | `mm.activity.llm` |
| `plan.validate` | `llm` | `mm.activity.llm` |
| `pr_resolver.classify_gate` | `integrations` | `mm.activity.integrations` |
| `pr_resolver.finalize_merge` | `integrations` | `mm.activity.integrations` |
| `pr_resolver.read_snapshot` | `integrations` | `mm.activity.integrations` |
| `pr_resolver.resolve_selector` | `integrations` | `mm.activity.integrations` |
| `pr_resolver.verify_merged` | `integrations` | `mm.activity.integrations` |
| `pr_resolver.verify_remote_head` | `integrations` | `mm.activity.integrations` |
| `pr_resolver.write_terminal_result` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.acquire_credential_maintenance_lease` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.ensure_manager` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.list` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.manager_state` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.pending_request_order` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.reset_manager` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.sync_slot_leases` | `artifacts` | `mm.activity.artifacts` |
| `provider_profile.verify_lease_holders` | `artifacts` | `mm.activity.artifacts` |
| `publication_recovery.cleanup` | `agent_runtime` | `mm.activity.agent_runtime` |
| `publication_recovery.observe` | `integrations` | `mm.activity.integrations` |
| `publication_recovery.persist_result` | `artifacts` | `mm.activity.artifacts` |
| `publication_recovery.publish` | `integrations` | `mm.activity.integrations` |
| `publication_recovery.publish_candidate` | `agent_runtime` | `mm.activity.agent_runtime` |
| `publication_recovery.restore_candidate` | `agent_runtime` | `mm.activity.agent_runtime` |
| `publication_recovery.verify` | `integrations` | `mm.activity.integrations` |
| `repo.create_pr` | `integrations` | `mm.activity.integrations` |
| `repo.merge_pr` | `integrations` | `mm.activity.integrations` |
| `resilience.compile_policy` | `artifacts` | `mm.activity.artifacts` |
| `sandbox.apply_patch` | `sandbox` | `mm.activity.sandbox` |
| `sandbox.checkout_repo` | `sandbox` | `mm.activity.sandbox` |
| `sandbox.run_command` | `sandbox` | `mm.activity.sandbox` |
| `sandbox.run_tests` | `sandbox` | `mm.activity.sandbox` |
| `step.review` | `llm` | `mm.activity.llm` |
| `step_checkpoint.create` | `artifacts` | `mm.activity.artifacts` |
| `step_checkpoint.create_v2` | `artifacts` | `mm.activity.artifacts` |
| `step_checkpoint.validate` | `artifacts` | `mm.activity.artifacts` |
| `worker.verify_workflow_capability` | `integrations` | `mm.activity.integrations` |
| `workload.run` | `agent_runtime` | `mm.activity.agent_runtime` |
| `workspace.apply_checkpoint` | `sandbox` | `mm.activity.sandbox` |
| `workspace.apply_policy` | `sandbox` | `mm.activity.sandbox` |
| `workspace.capture_checkpoint` | `sandbox` | `mm.activity.sandbox` |
| `workspace.classify_git_effect` | `sandbox` | `mm.activity.sandbox` |

## Search Attributes

Required:

- `mm_owner_id` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_owner_type` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_state` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_updated_at` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_entry` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_scheduled_for` (owner: `moonmind/workflows/temporal/scheduled_start.py`)
- `mm_started_at` (owner: `moonmind/workflows/temporal/workflows/run.py`)

Optional (only when product filtering requires them):

- `mm_repo` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_integration` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_target_runtime` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_target_skill` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_stage` (owner: `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md §5.2`)
- `mm_title` (owner: `moonmind/workflows/temporal/service.py`)
- `mm_has_dependencies` (owner: `moonmind/workflows/temporal/workflows/run.py`)
- `mm_dependency_count` (owner: `moonmind/workflows/temporal/workflows/run.py`)
- `AgentRunId` (owner: `moonmind/workflows/temporal/workflows/run.py`)
- `RuntimeId` (owner: `moonmind/workflows/temporal/workflows/run.py`)
- `SessionId` (owner: `moonmind/workflows/temporal/workflows/run.py`)
- `SessionEpoch` (owner: `moonmind/workflows/temporal/workflows/run.py`)
- `SessionStatus` (owner: `moonmind/workflows/temporal/workflows/run.py`)
- `IsDegraded` (owner: `moonmind/workflows/temporal/workflows/managed_runtime_workspace_cleanup.py`)

Runtime and primary-skill attributes must be registered before API
filters or facets query them.

