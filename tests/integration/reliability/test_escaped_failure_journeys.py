from __future__ import annotations

import asyncio
import hashlib
import json
import runpy
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity
from temporalio.testing import ActivityEnvironment

from api_service.db.models import Base
from api_service.services.omnigent_policies import bootstrap_document
from moonmind.omnigent import oauth_host_runtime as oauth_host_runtime_module
from moonmind.omnigent.bridge_events import build_omnigent_bridge_event
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_ITEM_FRONTIER_KEY,
    OmnigentBridgeSessionStore,
)
from moonmind.omnigent.execute import (
    OmnigentSessionStillRunningError,
    _await_marked_turn_terminal,
    _marked_turn_item_state,
    _persisted_pre_dispatch_item_ids,
    _safe_heartbeat,
    _snapshot_confirms_current_turn_terminal,
    _snapshot_contains_current_turn_progress,
    omnigent_activity_heartbeat,
)
from moonmind.omnigent.execution_profiles import compile_effective_launch
from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import (
    HOST_PROFILE_BUSY_ERROR_CODE,
    OmnigentOAuthHostError,
)
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
    _bind_exact_host,
)
from moonmind.omnigent.stock_agents import CODEX_STOCK_AGENT_NAME
from moonmind.security.egress import (
    EGRESS_CONFIG_DIGEST,
    ENFORCER_IMPLEMENTATION,
    EgressAttestation,
    OMNIGENT_EGRESS_PROFILE,
    omnigent_proxy_env,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    SendCodexManagedSessionTurnRequest,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRuntimeStepExecutionLaunch,
    AgentTerminalContract,
    ManagedRunRecord,
)
from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.provider_profiles.lease_client import (
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
)
from moonmind.workflows.adapters.codex_session_adapter import (
    CodexSessionAdapter,
    _pr_resolver_terminal_contract,
)
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentResolvedTarget,
    build_omnigent_session_create_payload,
    build_omnigent_selection,
    resolve_omnigent_target,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.provider_failures import classify_provider_failure
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    CodexManagedSessionRuntime,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module


def _egress_attestation() -> EgressAttestation:
    return EgressAttestation(
        profileRef=OMNIGENT_EGRESS_PROFILE.ref,
        profileDigest=OMNIGENT_EGRESS_PROFILE.digest,
        enforcerImplementation=ENFORCER_IMPLEMENTATION,
        backendRef="replay-test",
        networkRef=OMNIGENT_EGRESS_PROFILE.network_ref,
        gatewayRef=OMNIGENT_EGRESS_PROFILE.gateway_ref,
        appliedRuleDigest="sha256:" + "a" * 64,
        configDigest=EGRESS_CONFIG_DIGEST,
        gatewayImageDigest="sha256:" + "b" * 64,
        healthResult="healthy",
        validatedAt=datetime(2026, 8, 12, tzinfo=timezone.utc),
        validationResult="passed",
    )
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_AGENT_REQUIRED_CAPABILITIES_PROPAGATION_PATCH,
    RUN_HEADLESS_REMEDIATION_VERIFIED_WORKSPACE_PATCH,
    RUN_WORKFLOW_HEADLESS_REMEDIATION_PATCH,
    RUN_WORKFLOW_OWNED_REMEDIATION_HEAD_PATCH,
    RUN_OMNIGENT_STOCK_AGENT_IDENTITY_PATCH,
    RUN_PUBLISH_MODE_REPOSITORY_OPERATION_PATCH,
    RUN_PUBLISHED_BRANCH_HANDOFF_PATCH,
    RUN_REMEDIATION_EXPLICIT_EVIDENCE_INPUTS_PATCH,
    MoonMindRunWorkflow,
)
from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence
from tests.integration.reliability.helpers import (
    FinalizationFaultInjector,
    NestedYieldProcess,
    load_replay,
)
from tests.helpers.codex_session_runtime import launch_request, write_fake_app_server
from tests.unit.workflows.adapters.test_codex_session_adapter import (
    _binding,
    _pr_resolver_request,
    _terminal_contract_test_adapter,
    _turn_response,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _finalize_and_capture_summary,
)
from tests.unit.omnigent.test_oauth_profile_lifecycle import (
    _drive_authority_chain_coordinator,
    _run_coordinator_failure_case,
    _binding as _oauth_binding,
    _host_lease as _oauth_host_lease,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


async def test_omnigent_pr_step_reuses_candidate_against_original_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:6103dddf at the published-branch request boundary."""

    replay_id = "omnigent-pr-step-candidate-base"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        lambda: SimpleNamespace(**manifest["workflowInfo"]),
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        lambda patch_id: patch_id
        in {
            RUN_PUBLISHED_BRANCH_HANDOFF_PATCH,
            RUN_PUBLISH_MODE_REPOSITORY_OPERATION_PATCH,
        },
    )

    parent = MoonMindRunWorkflow()
    parent._publish_context.update(manifest["publishContext"])
    request = parent._build_agent_execution_request(
        node_inputs=manifest["nodeInputs"],
        node_id=manifest["logicalStepId"],
        tool_name="omnigent",
    )

    assert request.parameters["repositoryOperation"] == expected[
        "repositoryOperation"
    ]
    assert request.workspace_spec is not None
    assert request.workspace_spec["startingBranch"] == expected["publishBaseBranch"]
    assert request.workspace_spec["targetBranch"] == expected["candidateBranch"]
    assert request.workspace_spec["repositoryTarget"]["revision"]["commitSha"] == (
        expected["candidateHeadSha"]
    )


@activity.defn(name="reliability.omnigent_activity_heartbeat_probe")
async def _omnigent_activity_heartbeat_probe() -> None:
    async with omnigent_activity_heartbeat(interval_seconds=0.01):
        _safe_heartbeat(
            {"omnigentSessionId": "session-replay", "eventsCaptured": 1}
        )
        await asyncio.sleep(0.035)


async def test_profile_bound_activity_heartbeats_preflight_and_fences_cancelled_cleanup() -> None:
    """Replay the resolver timeout/retry race at the Activity-host boundary."""

    manifest = load_replay(
        "omnigent-profile-bound-heartbeat-timeout", "manifest.json"
    )
    expected = load_replay(
        "omnigent-profile-bound-heartbeat-timeout", "expected-outcome.json"
    )
    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity(manifest["activityType"])

    assert route.timeouts.heartbeat_timeout_seconds == manifest[
        "heartbeatTimeoutSeconds"
    ]
    assert expected["periodicLifecycleHeartbeat"] is True
    assert expected["preservesStreamingHeartbeatState"] is True

    heartbeats: list[tuple[object, ...]] = []
    environment = ActivityEnvironment()
    environment.on_heartbeat = lambda *details: heartbeats.append(details)
    await environment.run(_omnigent_activity_heartbeat_probe)
    heartbeat_payloads = [
        detail
        for callback_args in heartbeats
        for detail in callback_args
        if isinstance(detail, dict)
    ]
    assert len(heartbeat_payloads) >= 2
    assert any(payload.get("activityAlive") is True for payload in heartbeat_payloads)
    assert all(
        payload.get("omnigentSessionId") == "session-replay"
        for payload in heartbeat_payloads
        if payload.get("activityAlive") is True
    )

    events, actions, owner_calls = await _run_coordinator_failure_case(
        fail_at="session_create",
        code="activity_cancelled",
        injected_error=asyncio.CancelledError(),
    )
    cleanup_event = next(
        payload
        for event_type, payload in events
        if event_type == "host_cleanup" and payload["status"] == "waiting"
    )
    assert cleanup_event["metadata"]["janitorRequired"] is True
    assert expected["cancelledAttemptCleanup"] == "retry_or_janitor_reconciliation"
    assert "host_stop" not in owner_calls
    assert "provider_released" not in actions
    assert expected["releaseProviderLeaseOnCancellation"] is False


async def test_evicted_turn_marker_preserves_terminal_and_retry_authority() -> None:
    """Replay mm:d41f834c at both terminal and host-authority boundaries."""

    replay_id = "omnigent-evicted-turn-marker-retry-authority"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    baseline_item_ids = _persisted_pre_dispatch_item_ids(
        SimpleNamespace(
            metadata_={
                FIRST_MESSAGE_ITEM_FRONTIER_KEY: [
                    str(item["id"])
                    for item in manifest["preDispatchSnapshot"]["items"]
                ]
            }
        )
    )
    assert baseline_item_ids is not None
    assert expected["frontierAuthority"] == "durable_bridge_metadata"
    terminal_snapshot = manifest["terminalSnapshot"]
    marker = manifest["currentTurnMarker"]

    state = _marked_turn_item_state(
        terminal_snapshot,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    )
    assert state["markerIndex"] == -1
    assert state["boundarySource"] == expected["terminalBoundarySource"]
    assert _snapshot_contains_current_turn_progress(
        terminal_snapshot,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    ) is expected["acceptCurrentTurnProgress"]
    assert _snapshot_confirms_current_turn_terminal(
        terminal_snapshot,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    ) is expected["acceptCurrentTurnTerminal"]

    class CappedSnapshotClient:
        async def get_session(self, _session_id: str) -> dict[str, object]:
            return terminal_snapshot

    status, snapshot = await _await_marked_turn_terminal(
        client=CappedSnapshotClient(),
        session_id=manifest["sessionId"],
        marker=marker,
        baseline_item_ids=baseline_item_ids,
        event_count=len(manifest["observedProviderEvents"]),
        terminal_status="completed",
        interval_seconds=0.001,
        quiet_period_seconds=0.002,
    )
    assert status == "completed"
    assert snapshot is terminal_snapshot

    events, actions, owner_calls = await _run_coordinator_failure_case(
        fail_at="resource_harvest",
        code="OMNIGENT_CURRENT_TURN_TERMINAL_AMBIGUOUS",
        injected_error=OmnigentSessionStillRunningError(
            "current marked turn did not reach terminal state"
        ),
    )
    cleanup_event = next(
        payload
        for event_type, payload in events
        if event_type == "host_cleanup" and payload["status"] == "waiting"
    )
    assert cleanup_event["code"] == expected["ambiguousCleanupCode"]
    assert cleanup_event["metadata"]["janitorRequired"] is True
    assert "host_stop" not in owner_calls
    assert "host_remove" not in owner_calls
    assert "provider_released" not in actions
    assert expected["retryAuthority"] == "same_host_profile_and_bridge"


async def test_pr_resolver_child_compiles_bindable_stock_agent_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the resolver child that selected the removed ``codex`` name."""

    replay_id = "omnigent-stock-agent-catalog-identity"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["failedChildWorkflowId"],
        run_id=manifest["failedChildRunId"],
        parent=None,
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)

    workflow = MoonMindRunWorkflow()
    workflow._profile_snapshots = manifest["profileSnapshots"]
    request = workflow._build_agent_execution_request(
        node_inputs=manifest["nodeInputs"],
        workflow_parameters=manifest["workflowParameters"],
        node_id="node-1",
        tool_name="omnigent",
    )

    assert (
        RUN_OMNIGENT_STOCK_AGENT_IDENTITY_PATCH
        == "run-omnigent-stock-agent-identity-v1"
    )
    assert request.parameters["omnigent"]["agent"]["agentName"] == (
        CODEX_STOCK_AGENT_NAME
    )
    bootstrap = bootstrap_document(
        host_mode="on_demand_docker",
        execution_profile_ref="omnigent-codex@1",
    ).model_dump(by_alias=True, mode="json")
    assert bootstrap["execution"]["agentIdentities"] == [
        expected["bootstrapPolicyAgentIdentity"]
    ]
    assert manifest["cutoverAuthority"]["durableHostLaunchPolicyRef"] == (
        expected["managedProfileLaunchPolicyRef"]
    )
    assert manifest["cutoverAuthority"]["staleManagedProfileLaunchPolicyRef"] != (
        expected["managedProfileLaunchPolicyRef"]
    )
    assert expected["exactRerunLaunchPolicyRef"] == (
        expected["managedProfileLaunchPolicyRef"]
    )
    assert expected["legacyTrustedIdentityRemovedFromAuthoredParameters"] is True
    assert manifest["cutoverAuthority"]["staleExactRerunAgentProfileRef"].endswith(
        "@1"
    )

    async def list_agents():
        return [expected["catalogAgent"]]

    async def reject_upload(_bundle_ref: str):
        raise AssertionError("stock identity resolution must not upload a bundle")

    bound_request = _bind_exact_host(
        request,
        host_id="host-stock-replay",
        workspace_path="/workspaces/run",
        profile_authorization={"providerProfileId": "codex_openai_oauth"},
        harness="codex-native",
        agent_name=CODEX_STOCK_AGENT_NAME,
    )
    target = await resolve_omnigent_target(
        build_omnigent_selection(bound_request),
        list_agents=list_agents,
        upload_agent_bundle=reject_upload,
        default_agent=None,
    )
    assert target.agent_id == expected["resolvedAgentId"]
    assert target.agent_name == expected["resolvedAgentName"]


async def test_runtime_switch_rebinds_managed_session_authority_before_activity() -> (
    None
):
    """Replay mm:d3ca1354 at the AgentRun-to-Activity request boundary."""
    replay_id = "managed-session-runtime-switch"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    request = AgentExecutionRequest.model_validate(manifest["request"])
    agent_run = MoonMindAgentRun()

    agent_run._apply_runtime_selection_update(
        request,
        manifest["runtimeUpdate"],
    )
    agent_run._synchronize_runtime_selection_authority(request)
    activity_payload = request.model_dump(mode="json", by_alias=True)

    assert activity_payload["agentId"] == expected["agentId"]
    assert activity_payload["executionProfileRef"] == expected[
        "executionProfileRef"
    ]
    assert activity_payload["managedSession"] is expected["managedSession"]
    assert activity_payload["stepExecution"]["runtimeSessionReset"] is expected[
        "runtimeSessionReset"
    ]
    assert activity_payload["stepExecution"]["runtimeSelection"] == expected[
        "runtimeSelection"
    ]
    AgentExecutionRequest.model_validate(activity_payload)


async def test_oauth_maintenance_lease_replays_through_activity_update_boundary() -> (
    None
):
    manifest = load_replay("oauth-maintenance-external-update", "manifest.json")
    expected = load_replay(
        "oauth-maintenance-external-update", "expected-outcome.json"
    )

    class Adapter:
        def __init__(self) -> None:
            self.update_name = ""
            self.payload: dict[str, object] = {}

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            return None

        async def update_workflow(self, _workflow_id, update_name, payload):
            self.update_name = update_name
            self.payload = payload
            return {
                "profile_id": manifest["profileId"],
                "lease_id": payload["owner_id"],
            }

    route = build_default_activity_catalog().resolve_activity(
        manifest["expectedActivityType"]
    )
    adapter = Adapter()
    lease = await ProviderProfileLeaseClient(adapter).acquire_maintenance_lease(
        runtime_id=manifest["runtimeId"],
        profile_id=manifest["profileId"],
        owner_id=manifest["leaseRequest"]["ownerId"],
        purpose=CredentialLeasePurpose(manifest["leaseRequest"]["purpose"]),
        metadata={"oauthSessionId": "oas-replay"},
        owner_is_workflow=True,
    )

    assert route.task_queue == manifest["expectedTaskQueue"]
    assert lease.lease_id == manifest["leaseRequest"]["ownerId"]
    assert adapter.update_name == expected["acknowledgedBy"]
    assert adapter.payload["metadata"]["ownerIsWorkflow"] is expected[
        "ownerIsWorkflow"
    ]


async def test_completed_batch_turn_without_fanout_evidence_fails() -> None:
    replay_id = "batch-workflows-missing-fanout-evidence"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    evaluation = evaluate_terminal_evidence(
        manifest["terminalContract"], workspace_path=manifest["workspacePath"]
    )
    assert manifest["agentTurn"]["status"] == "completed"
    assert manifest["postRecords"] == []
    assert evaluation.satisfied is False
    assert evaluation.failure_code == expected["failureCode"]
    assert expected["parentState"] == "failed"


async def test_auto_publish_one_shot_deferral_reenters_before_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:a5625f36 through evidence, continuation, and recovery seams."""

    replay_id = "auto-publish-one-shot-deferred"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / manifest["agentRunId"] / "repo"
    workspace.mkdir(parents=True)
    for relative in manifest["dirtyFiles"]:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("uncommitted recovery work\n", encoding="utf-8")

    now = datetime.now(timezone.utc)
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId=manifest["agentRunId"],
            workflowId=f"{manifest['incidentWorkflowId']}:agent:fix-comments",
            ownerRunId="incident-run",
            logicalStepId="fix-comments",
            executionOrdinal=1,
            agentId=manifest["runtime"],
            runtimeId=manifest["runtime"],
            status="completed",
            exitCode=manifest["processExitCode"],
            startedAt=now,
            finishedAt=now,
            workspacePath=str(workspace),
        )
    )
    activities = TemporalAgentRuntimeActivities(run_store=run_store)
    agent_run = MoonMindAgentRun()
    agent_run.run_id = manifest["agentRunId"]
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch_id: True)

    async def execute_activity(
        name: str,
        payload: object,
        **_kwargs: object,
    ) -> object:
        if name == "agent_runtime.evaluate_terminal_evidence":
            assert isinstance(payload, dict)
            assert payload["selectedSkill"] == manifest["selectedSkill"]
            evaluated = await activities.agent_runtime_evaluate_terminal_evidence(
                payload
            )
            return evaluated.model_dump(mode="json", by_alias=True)
        assert name == expected["terminalCheckpointActivity"]
        return {
            "intent": "terminal_checkpoint",
            "status": "pushed",
            "reasonCode": expected["terminalCheckpointReason"],
            "source": "live_workspace",
            "attempted": True,
            "branchPushed": True,
            "branchName": "recovery/mm-a5625f36",
            "headSha": "abc123",
            "remoteVerified": True,
            "idempotencyKey": (
                f"terminal-contract-checkpoint-v1:{manifest['agentRunId']}"
            ),
        }

    agent_run._execute_routed_activity = execute_activity  # type: ignore[method-assign]
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId=manifest["runtime"],
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=f"{manifest['incidentWorkflowId']}:fix-comments",
        instructionRef="Resolve all applicable review comments and publish.",
        stepExecution={
            "workflowId": manifest["incidentWorkflowId"],
            "runId": "incident-run",
            "logicalStepId": "fix-comments",
            "executionOrdinal": 1,
            "stepExecutionId": manifest["terminalContract"]["executionRef"],
            "runtimeContextPolicy": "fresh_agent_run",
        },
        workspaceSpec={
            "workspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": manifest["runtime"],
                "agentRunId": manifest["agentRunId"],
                "relativePath": "repo",
            }
        },
        terminalContract=manifest["terminalContract"],
        parameters={
            "publishMode": manifest["publishMode"],
            "metadata": {
                "moonmind": {"selectedSkill": manifest["selectedSkill"]}
            },
        },
    )
    evaluated = await agent_run._evaluate_terminal_contract(
        request=request,
        result=AgentRunResult(
            summary="Process exited cleanly after scheduling a wake-up.",
        ),
    )

    assert evaluated.failure_class == expected["failureClass"]
    assert evaluated.metadata["failureCode"] == expected["failureCode"]
    assert (
        evaluated.metadata["terminalContractRecoveryOutcome"]
        == expected["recoveryOutcome"]
    )
    continuation = agent_run._fresh_process_terminal_contract_request(
        request=request,
        result=evaluated,
    )
    assert continuation is not None
    assert continuation.workspace_spec["workspaceLocator"]["kind"] == expected[
        "workspaceKind"
    ]
    assert agent_run._terminal_contract_fresh_process_history[-1]["mode"] == expected[
        "continuationMode"
    ]

    exhausted = evaluated.model_copy(
        update={
            "metadata": {
                **dict(evaluated.metadata or {}),
                "terminalContractRecoveryOutcome": "exhausted",
            }
        }
    )
    checkpointed = await agent_run._publish_terminal_contract_failure_checkpoint(
        request=request,
        result=exhausted,
    )
    assert checkpointed.failure_class == expected["failureClass"]
    assert checkpointed.metadata["terminalPublication"]["reasonCode"] == expected[
        "terminalCheckpointReason"
    ]


async def test_verified_remediation_push_reaches_draft_publication_handoff() -> None:
    """Replay the two orphaned branches through the production authority seam."""
    replay_id = "draft-publication-authority-gap"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    for case in manifest["cases"]:
        workflow_run = MoonMindRunWorkflow()
        raw_result = {"outputs": case["pushResult"]}
        assert workflow_run._publication_feasibility(raw_result)["reason"] == (
            "publication_state_ambiguous"
        )

        accepted = TemporalAgentRuntimeActivities._accepted_repository_evidence(
            case["pushResult"]
        )
        assert accepted is not None
        assert accepted["schemaVersion"] == expected[
            "acceptedEvidenceSchemaVersion"
        ]
        assert accepted["authority"] == expected["acceptedEvidenceAuthority"]

        feasibility = workflow_run._publication_feasibility(
            {"outputs": {"acceptedRepositoryEvidence": accepted}}
        )
        assert feasibility["feasible"] is expected["publicationFeasible"]
        assert feasibility["reason"] == expected[
            "publicationFeasibilityReason"
        ]
        assert workflow_run._terminal_gate_handoff_kind(
            publish_mode=manifest["publishMode"],
            draft_publication_policy=manifest["draftPublicationPolicy"],
            publication_feasible=feasibility["feasible"],
        ) == expected["terminalHandoff"]


async def test_completed_batch_turn_is_rejected_at_agent_run_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the escaped MM-1201 journey through AgentRun's authority handoff."""
    replay_id = "batch-workflows-missing-fanout-evidence"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    agent_run = MoonMindAgentRun()

    async def execute_activity(name: str, payload: dict, **kwargs: object) -> dict:
        assert name == "agent_runtime.evaluate_terminal_evidence"
        assert kwargs["task_queue"] == "mm.activity.agent_runtime"
        activities = TemporalAgentRuntimeActivities(client_adapter=object())
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(payload)
        return evaluated.model_dump(mode="json", by_alias=True)

    # Patch only the Temporal SDK handoff. Keep AgentRun's production catalog
    # lookup and route construction in the replay so catalog drift cannot be
    # hidden by a test double.
    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="mm-1201",
        idempotencyKey="mm-1201:replay",
        workspaceSpec={"workspacePath": manifest["workspacePath"]},
        terminalContract=manifest["terminalContract"],
    )
    provider_result = AgentRunResult(
        summary=manifest["agentTurn"]["assistantText"],
        metadata={"workspacePath": manifest["workspacePath"]},
    )

    result = await agent_run._evaluate_terminal_contract(
        request=request, result=provider_result
    )

    assert result.failure_class == expected["failureClass"]
    assert result.metadata["failureCode"] == expected["failureCode"]
    assert result.metadata["terminalContractMissingEvidence"] == expected["missingEvidence"]
    assert result.metadata["terminalContractAuthority"] == "MoonMind.AgentRun"

    parent = MoonMindRunWorkflow()
    parent._owner_type = "user"
    parent._owner_id = "mm-1201-replay"
    diagnostic = parent._record_result_failure_diagnostic(
        stage="execute",
        category=result.failure_class,
        source="child_workflow",
        step_id="batch-workflows",
        step_title="batch-workflows",
        message=result.summary,
        child_workflow_id="agent-run-mm-1201",
        terminal_evidence=result.metadata,
    )
    from moonmind.workflows.temporal.workflows import run as run_workflow_module

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime(2026, 7, 11, tzinfo=timezone.utc),
    )
    summary = await _finalize_and_capture_summary(
        monkeypatch,
        parent,
        parameters={"publishMode": "none"},
        status="failed",
        error=diagnostic["message"],
    )

    assert summary["finishOutcome"]["code"] == "FAILED"
    assert summary["failure"]["failureCode"] == expected["failureCode"]
    assert summary["failure"]["terminalContractMissingEvidence"] == expected[
        "missingEvidence"
    ]


async def test_successful_batch_fanout_is_compacted_before_publish_activity_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:dc7271dd at the AgentRun-to-publish activity boundary."""

    replay_id = "agent-run-publish-metadata-overflow"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    shape = manifest["resultShape"]
    request = AgentExecutionRequest.model_validate(manifest["request"])
    assert request.managed_session is not None
    queued_children = []
    for index in range(shape["queuedChildCount"]):
        issue_number = shape["firstIssueNumber"] + index
        execution_id = f"mm:00000000-0000-0000-0000-{index:012d}"
        target_ref = f"{shape['repository']}#{issue_number}"
        queued_children.append(
            {
                "provider": "github",
                "ref": target_ref,
                "workflowId": execution_id,
                "executionId": execution_id,
                "targetRef": target_ref,
                "idempotencyKey": (
                    f"batch-workflows:github:{target_ref}:sha256:"
                    + "a" * shape["idempotencyDigestChars"]
                ),
            }
        )

    provider_result = AgentRunResult(
        summary=shape["summary"],
        metadata={
            "agentRunId": request.managed_session.agent_run_id,
            "diagnosticsRef": "sess:" + "d" * 66,
            "lastAssistantText": "A" * shape["lastAssistantTextChars"],
            "operator_summary": "B" * shape["operatorSummaryChars"],
            "queuedChildCount": len(queued_children),
            "queuedChildren": queued_children,
            "stderrArtifactRef": "sess:" + "e" * 60,
            "stdoutArtifactRef": "sess:" + "o" * 60,
            "instructionRefOmitted": True,
            "instructionRefSha256": "f" * 64,
            "instructionRefLengthChars": 2247,
            "terminalContractAuthority": "MoonMind.AgentRun",
            "terminalContractEvidencePath": manifest[
                "workspaceArtifactManifest"
            ]["terminalEvidencePath"],
            "terminalContractExecutionRef": "execution:" + "e" * 118,
            "terminalContractId": manifest["workspaceArtifactManifest"][
                "contractId"
            ],
            "terminalContractOutcome": expected["terminalOutcome"],
            "terminalContractSatisfied": True,
        },
    )
    agent_run = MoonMindAgentRun()
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": (
                "mm:replay-parent:agent:tpl:batch-github-workflows:01:replay"
            ),
            "run_id": "00000000-0000-0000-0000-000000000001",
            "search_attributes": {},
            "parent": None,
        },
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch: True)

    prepublication_result = agent_run._enrich_result_metadata(
        request=request,
        result=provider_result,
    )
    assert prepublication_result is not None
    prepublication_metadata = dict(prepublication_result.metadata)
    prepublication_metadata["resiliencyPolicy"] = {
        "noProgressTimeoutSeconds": 1800,
        "retryPolicy": {},
        "runtime": "codex_cli",
        "stuckAction": "request_intervention",
    }
    prepublication_result = prepublication_result.model_copy(
        update={"metadata": prepublication_metadata}
    )
    with pytest.raises(ValueError, match="metadata must serialize"):
        AgentRunResult.model_validate(
            prepublication_result.model_dump(mode="json", by_alias=True)
        )

    published_payloads: list[AgentRunResult] = []

    async def execute_activity(
        name: str,
        payload: AgentRunResult,
        **kwargs: object,
    ) -> dict:
        assert name == manifest["activityName"]
        assert kwargs["task_queue"] == manifest["expectedTaskQueue"]
        validated = AgentRunResult.model_validate(
            payload.model_dump(mode="json", by_alias=True)
        )
        metadata_bytes = len(
            json.dumps(
                validated.metadata,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert metadata_bytes <= expected["maxMetadataBytes"]
        published_payloads.append(validated)
        return validated.model_dump(mode="json", by_alias=True)

    # Patch only the Temporal SDK handoff so production activity routing remains
    # part of the escaped-failure replay.
    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)

    result = await agent_run._publish_terminal_result(
        request=request,
        result=prepublication_result,
    )

    assert result.failure_class is None
    assert expected["parentState"] == "succeeded"
    assert result.metadata["terminalContractOutcome"] == expected["terminalOutcome"]
    assert result.metadata["queuedChildCount"] == expected["queuedChildCount"]
    assert len(published_payloads) == 1
    projected_children = published_payloads[0].metadata["queuedChildren"]
    assert len(projected_children) == expected["queuedChildCount"]
    assert set(projected_children[0]) == set(expected["preservedQueuedChildFields"])
    assert not set(projected_children[0]).intersection(
        expected["artifactOnlyQueuedChildFields"]
    )


async def test_dependabot_build_titles_replay_through_portable_skill_classifier() -> (
    None
):
    """Replay mm:c837ff3b through the resolved Skill's classification boundary."""
    replay_id = "dependabot-build-title-contract"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    repo_root = Path(__file__).resolve().parents[3]
    skill = runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-dependabot-resolver"
            / "bin"
            / "batch_dependabot_resolver.py"
        )
    )
    args = SimpleNamespace(
        title_regex=skill["DEFAULT_TITLE_REGEX"],
        package_managers=[],
        include_security_updates=True,
        max_prs=None,
        merge_method="squash",
        max_iterations=5,
        priority=0,
        max_attempts=3,
    )

    queue_requests, skipped, matched_count = skill["_build_request_records"](
        manifest["repository"],
        manifest["pullRequests"],
        args,
        skill["RuntimeSelection"](),
    )
    drift_prs = [
        item["pr"]
        for item in skipped
        if item.get("reason") == "title-mismatch"
        and item.get("likelyVersionBump") is True
    ]

    assert matched_count == expected["matchedCount"]
    assert [item.pr_number for item in queue_requests] == expected[
        "matchedPrNumbers"
    ]
    assert skipped == expected["skipped"]
    assert drift_prs == expected["titleContractDriftPrs"]


async def test_invalid_batch_range_records_terminal_failure_without_retry(
    tmp_path: Path,
) -> None:
    """Replay mm:3df0b867 through the terminal and parent retry boundaries."""
    manifest = load_replay("batch-workflows-invalid-range", "manifest.json")
    expected = load_replay(
        "batch-workflows-invalid-range", "expected-outcome.json"
    )
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    workspace.mkdir()
    spool.mkdir()
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(manifest["terminalEvidence"]), encoding="utf-8"
    )

    result = await TemporalAgentRuntimeActivities().agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(workspace),
            "artifactSpoolPath": str(spool),
            "terminalContract": manifest["terminalContract"],
            "result": {"summary": "Batch range validation failed."},
        }
    )

    assert result.failure_class == expected["failureClass"]
    assert result.provider_error_code == expected["failureCode"]
    assert result.metadata["terminalContractMissingEvidence"] == expected[
        "missingEvidence"
    ]

    parent = MoonMindRunWorkflow()
    retryable = parent._activity_result_retryable(
        {"outputs": result.model_dump(mode="json", by_alias=True)},
        failure_message="execution_error",
        tool_type="agent_runtime",
    )
    assert retryable is expected["retryable"]
    assert expected["parentState"] == "failed"


async def test_standalone_omnigent_resolver_rejects_unowned_continuation_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:7f52b94b at the runtime-instruction and retry boundaries."""

    replay_id = "omnigent-pr-resolver-unowned-continuation"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    request = AgentExecutionRequest.model_validate(manifest["request"])
    assert request.terminal_continuation_authority is None
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        lambda _patch: True,
    )

    parent = MoonMindRunWorkflow()
    instruction = parent._terminal_continuation_authority_instruction(request)
    assert f"continuation authority: {expected['continuationAuthority']}" in instruction
    assert ("Treat this execution as standalone" in instruction) is expected[
        "standaloneInstruction"
    ]

    retryable = parent._activity_result_retryable(
        manifest["childResult"],
        failure_message="execution_error",
        tool_type="agent_runtime",
    )

    assert retryable is expected["retryable"]
    assert expected["genericRetryCount"] == 0
    assert expected["parentState"] == "failed"


async def test_completed_batch_no_op_replays_through_production_activity_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay the endless WorkflowTaskFailed incident at its routing boundary."""
    manifest = load_replay("agent-run-terminal-evidence-routing", "manifest.json")
    workspace = tmp_path / "repo"
    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True)
    targets_bytes = json.dumps(manifest["resolvedTargets"]).encode("utf-8")
    (artifacts / "batch-workflows-targets.json").write_bytes(targets_bytes)
    terminal_evidence = dict(manifest["terminalEvidence"])
    terminal_evidence["targetsSha256"] = hashlib.sha256(targets_bytes).hexdigest()
    (artifacts / "batch-workflows-result.json").write_text(
        json.dumps(terminal_evidence), encoding="utf-8"
    )

    async def execute_activity(name: str, payload: dict, **kwargs: object) -> dict:
        assert name == manifest["activityName"]
        assert kwargs["task_queue"] == manifest["expectedTaskQueue"]
        activities = TemporalAgentRuntimeActivities(client_adapter=object())
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(payload)
        return evaluated.model_dump(mode="json", by_alias=True)

    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=f"{manifest['incidentWorkflowId']}:replay",
        workspaceSpec={"workspacePath": str(workspace)},
        terminalContract=manifest["terminalContract"],
    )

    result = await MoonMindAgentRun()._evaluate_terminal_contract(
        request=request,
        result=AgentRunResult(
            summary="No child workflows were queued.",
            metadata={"workspacePath": str(workspace)},
        ),
    )

    assert result.failure_class is None
    assert result.metadata["terminalContractId"] == "batch_workflows_fanout.v1"
    assert result.metadata["queuedChildCount"] == 0


async def test_retry_batch_artifacts_in_spool_replay_as_terminal_success(
    tmp_path: Path,
) -> None:
    """Replay the false-negative fan-out through the production activity boundary."""

    replay_id = "batch-workflows-spool-retry-identity"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    workspace.mkdir()
    spool.mkdir()
    targets_bytes = json.dumps(manifest["resolvedTargets"]).encode("utf-8")
    (spool / "batch-workflows-targets.json").write_bytes(targets_bytes)
    terminal_evidence = dict(manifest["terminalEvidence"])
    terminal_evidence["targetsSha256"] = hashlib.sha256(targets_bytes).hexdigest()
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(terminal_evidence),
        encoding="utf-8",
    )

    result = (
        await TemporalAgentRuntimeActivities().agent_runtime_evaluate_terminal_evidence(
            {
                "workspacePath": str(workspace),
                "artifactSpoolPath": str(spool),
                "terminalContract": manifest["terminalContract"],
                "result": {"summary": "Queued both child workflows."},
            }
        )
    )

    assert not (workspace / "artifacts").exists()
    assert result.failure_class is expected["failureClass"]
    assert result.metadata["queuedChildCount"] == expected["queuedChildCount"]
    assert (
        result.metadata["terminalContractExecutionRef"] == expected["executionRef"]
    )
    assert expected["parentState"] == "completed"


async def test_successful_batch_without_publication_skips_prepublication_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the false-failure incident through the finalization boundary."""

    replay_id = "batch-fanout-no-publish-checkpoint"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 7, 14, 7, 14, tzinfo=timezone.utc)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": manifest["logicalStepId"],
                "inputs": {"title": "Queue GitHub issue workflows"},
            }
        ],
        dependency_map={manifest["logicalStepId"]: []},
        updated_at=now,
    )
    row = workflow._step_ledger_row_for(manifest["logicalStepId"])
    assert row is not None
    row["executionOutcome"] = manifest["executionOutcome"]
    workflow._publish_status = "not_required"
    workflow._publish_reason = (
        f"queued {manifest['queuedChildCount']} child workflows"
    )
    checkpoint_calls: list[str] = []

    async def checkpoint(
        _logical_step_id: str,
        *,
        boundary: str,
        updated_at: datetime,
    ) -> str:
        checkpoint_calls.append(boundary)
        raise AssertionError("publishMode none has no pre-publication boundary")

    monkeypatch.setattr(
        workflow,
        "_record_canonical_step_checkpoint",
        checkpoint,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _id: True)

    checkpoint_failed = await workflow._record_prepublication_checkpoint(
        manifest["logicalStepId"],
        publish_mode=manifest["publishMode"],
        updated_at=now,
    )
    completion = workflow._determine_publish_completion(
        parameters={"publishMode": manifest["publishMode"]}
    )

    assert checkpoint_failed is False
    assert len(checkpoint_calls) == expected["prepublicationCheckpointCalls"]
    assert completion[0] == expected["completionStatus"]
    assert completion[2] is False
    assert workflow._attention_required is expected["attentionRequired"]
    assert expected["parentState"] == "completed"


def _materialize_workspace_fixture(replay_id: str, workspace: Path) -> None:
    manifest = load_replay(replay_id, "workspace-manifest.json")
    for item in manifest["artifacts"]:
        target = workspace / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(item["content"]), encoding="utf-8")


async def test_nested_yield_attempts_remain_non_terminal(tmp_path: Path) -> None:
    replay_id = "incomplete-terminal-contract"
    expected = load_replay(replay_id, "expected-outcome.json")
    process = NestedYieldProcess("inner-shell-3145")
    workspace = tmp_path / "repo"
    _materialize_workspace_fixture(replay_id, workspace)

    first_yield = process.first_tool_yield()
    wrapper_result = process.wrapper_completes()
    satisfied, missing, metadata = _pr_resolver_terminal_contract(str(workspace))

    assert first_yield == {"session_id": "inner-shell-3145", "status": "running"}
    assert wrapper_result["status"] == "completed"
    assert process.inner_active is True, "wrapper completion terminated inner process"
    assert satisfied is False, "attempt artifact incorrectly became terminal evidence"
    assert missing == expected["missingEvidence"]
    assert metadata["prResolverLatestAttempt"]["attemptCount"] == 2
    requests: list[SendCodexManagedSessionTurnRequest] = []

    async def send_turn(
        request: SendCodexManagedSessionTurnRequest,
    ) -> object:
        requests.append(request)
        return _turn_response(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
            turn_id=f"turn-{len(requests)}",
        )

    binding = _binding()
    adapter = _terminal_contract_test_adapter(tmp_path, send_turn=send_turn)
    handle = await adapter.start(_pr_resolver_request(binding, workspace))

    # Provider adapters translate one runtime turn. AgentRun owns terminal
    # evidence evaluation and any capability-aware bounded continuation.
    assert handle.status == "completed"
    assert len(requests) == 1
    assert {
        (item.session_id, item.session_epoch, item.thread_id) for item in requests
    } == {(binding.session_id, binding.session_epoch, "thread-terminal-contract")}


@pytest.mark.parametrize("recover", [True, False], ids=["recovered", "exhausted"])
async def test_nested_yield_continuation_replays_through_production_agent_run_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    """MoonLadderStudios/MoonMind#3145 continuation journey across the agent route.

    AgentRun owns capability-aware bounded continuation. Replay the incident at
    the production activity-routing and terminal-evidence boundaries: every
    continuation activity must resolve through the real catalog to the managed
    agent-runtime queue, evaluate real workspace evidence, and preserve a stable
    session/thread/epoch identity across bounded continuation turns.
    """
    replay_id = "incomplete-terminal-contract"
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / "repo"
    _materialize_workspace_fixture(replay_id, workspace)

    # AgentRun evaluates terminal evidence and drives continuation outside a live
    # Temporal event loop here; enable the continuation patch gates and provide a
    # workflow info stub so the production helper runs in-process.
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="reliability-3145:agent:node-1",
        run_id="reliability-3145-run",
        search_attributes={},
        parent=None,
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch: True)

    binding = _binding()
    execution_ref = "mm:reliability-3145:terminal-contract"
    container_id = "ctr-reliability-3145"
    thread_id = "thread-terminal-contract"
    request = _pr_resolver_request(binding, workspace).model_copy(
        update={
            "terminal_contract": AgentTerminalContract(
                contractId="pr_resolver_terminal.v1",
                relativePath="var/pr_resolver/result.json",
                expectedSchemaVersion="moonmind.pr-resolver-result.v1",
                executionRef=execution_ref,
            )
        }
    )

    turns: list[SendCodexManagedSessionTurnRequest] = []
    routed_queues: set[str] = set()

    async def execute_activity(name: str, payload: object, **kwargs: object) -> object:
        # Keep AgentRun's production catalog lookup and route construction in the
        # replay; only the Temporal SDK handoff is doubled, so catalog drift that
        # sent continuation activities to the wrong worker cannot be hidden.
        routed_queues.add(kwargs["task_queue"])
        if name == "agent_runtime.evaluate_terminal_evidence":
            activities = TemporalAgentRuntimeActivities(client_adapter=object())
            evaluated = await activities.agent_runtime_evaluate_terminal_evidence(
                payload
            )
            return evaluated.model_dump(mode="json", by_alias=True)
        if name == "agent_runtime.load_session_snapshot":
            return {
                "binding": binding.model_dump(mode="json", by_alias=True),
                "status": "active",
                "containerId": container_id,
                "threadId": thread_id,
            }
        if name == "agent_runtime.send_turn":
            turns.append(payload)
            if recover and len(turns) == 1:
                # A recovered continuation writes satisfied terminal evidence:
                # a merged disposition whose executionRef matches the contract,
                # plus the required auto-publish artifact.
                result_path = workspace / "var/pr_resolver/result.json"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(
                    json.dumps(
                        {
                            "executionRef": execution_ref,
                            "mergeAutomationDisposition": "merged",
                        }
                    ),
                    encoding="utf-8",
                )
                publish_path = workspace / "artifacts/publish_result.json"
                publish_path.parent.mkdir(parents=True, exist_ok=True)
                publish_path.write_text(
                    json.dumps(
                        {
                            "schemaVersion": "moonmind.publish.auto.v1",
                            "mode": "auto",
                            "owner": "agent",
                            "skillId": "pr-resolver",
                            "executionRef": execution_ref,
                            "status": "verified",
                            "action": "merge",
                            "repository": "MoonLadderStudios/MoonMind",
                            "branch": "feature",
                            "localHead": "abc123",
                            "remoteBranchHead": None,
                            "remoteVerified": True,
                            "pushed": False,
                            "merged": True,
                            "prUrl": (
                                "https://github.com/MoonLadderStudios/"
                                "MoonMind/pull/1"
                            ),
                            "blockedReason": None,
                            "verificationCommands": ["gh pr view 1"],
                        }
                    ),
                    encoding="utf-8",
                )
            return _turn_response(
                session_id=payload.session_id,
                session_epoch=payload.session_epoch,
                container_id=payload.container_id,
                thread_id=payload.thread_id,
                turn_id=f"continuation-{len(turns)}",
            ).model_dump(mode="json", by_alias=True)
        if name == "agent_runtime.fetch_result":
            return AgentRunResult(
                summary="continuation completed",
                metadata={"workspacePath": str(workspace)},
            ).model_dump(mode="json", by_alias=True)
        raise AssertionError(f"unexpected activity: {name}")

    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)

    result = await MoonMindAgentRun()._evaluate_terminal_contract(
        request=request,
        result=AgentRunResult(
            summary="wrapper completed while inner process remained active",
            metadata={"workspacePath": str(workspace)},
        ),
    )

    # Every terminal-contract continuation activity crosses the production
    # managed agent-runtime route, never a per-test task queue.
    assert routed_queues == {"mm.activity.agent_runtime"}
    expected_turns = 1 if recover else expected["continuationCount"]
    assert len(turns) == expected_turns
    assert {
        (turn.session_id, turn.session_epoch, turn.thread_id) for turn in turns
    } == {(binding.session_id, binding.session_epoch, thread_id)}
    assert result.metadata["terminalContractContinuationCount"] == expected_turns
    if recover:
        assert result.failure_class is None
        assert result.metadata["terminalContractRecoveryOutcome"] == "recovered"
    else:
        assert result.failure_class == expected["failureClass"]
        assert result.metadata["failureCode"] == expected["failureCode"]
        assert result.metadata["terminalContractRecoveryOutcome"] == "incomplete"


async def test_sandbox_checkpoint_rejects_managed_workspace_without_resolving_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    expected = load_replay(replay_id, "expected-outcome.json")
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "sandbox-root")

    sandbox_calls = 0

    def forbidden_sandbox_resolver(*_args: object, **_kwargs: object) -> Path:
        nonlocal sandbox_calls
        sandbox_calls += 1
        raise AssertionError("managed workspace reached sandbox resolver")

    monkeypatch.setattr(activities, "_resolve_workspace", forbidden_sandbox_resolver)
    payload = {
        "identity": {
            "workflowId": "wf-reliability-3145",
            "runId": "run-3145",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "kind": "worktree_archive",
        "workspaceLocator": {
            "kind": "managed_runtime",
            "runtimeId": "codex",
            "agentRunId": "run-3145",
        },
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "reliability-3145-after-execution",
    }
    with pytest.raises(WorkspaceLocatorResolutionError) as exc_info:
        await activities.workspace_capture_checkpoint(payload)

    assert exc_info.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    assert sandbox_calls == expected["sandboxResolverCalls"] == 0


async def test_managed_checkpoint_waits_for_authoritative_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:8a09888d before the AgentRun workspace exists."""
    replay_id = "managed-checkpoint-missing-locator"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()
    parent_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["runId"],
        task_queue="mm.workflow.merge_automation",
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: parent_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)

    activity_calls: list[str] = []

    async def capture_activity(
        activity_type: str,
        _payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        activity_calls.append(activity_type)
        raise AssertionError("managed capture ran without an authoritative locator")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        capture_activity,
    )
    now = datetime(2026, 7, 14, 5, 25, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": "node-1", "inputs": {"title": "Investigate"}}],
        dependency_map={"node-1": []},
        updated_at=now,
    )
    parent._mark_step_running("node-1", updated_at=now, summary="Investigating")
    parent._record_step_workspace_capture_input("node-1", manifest["stepInputs"])

    checkpoint_ref = await parent._record_canonical_step_checkpoint(
        "node-1",
        boundary=manifest["boundary"],
        updated_at=now,
    )

    assert checkpoint_ref is None
    assert activity_calls == expected["activityCalls"]
    assert parent._step_checkpoint_capture_outcomes["node-1"] == expected[
        "captureOutcome"
    ]


async def test_omnigent_checkpoint_waits_for_workspace_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:06cb96c8 before the Omnigent coordinator materializes its repo."""
    replay_id = "omnigent-checkpoint-before-workspace"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()
    parent_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["runId"],
        task_queue="mm.workflow.merge_automation",
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: parent_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)

    activity_calls: list[str] = []

    async def capture_activity(
        activity_type: str,
        _payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        activity_calls.append(activity_type)
        raise AssertionError("sandbox capture ran before Omnigent materialized the repo")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        capture_activity,
    )
    now = datetime(2026, 8, 5, 6, 30, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": "node-1", "inputs": {"title": "Implement"}}],
        dependency_map={"node-1": []},
        updated_at=now,
    )
    parent._mark_step_running("node-1", updated_at=now, summary="Implementing")
    parent._record_step_workspace_capture_input(
        "node-1",
        manifest["stepInputs"],
        initialize_omnigent_capture=True,
    )

    capture_input = parent._step_workspace_capture_inputs["node-1"]
    assert capture_input["workspaceLocator"] == manifest["escapedActivityPayload"][
        "workspaceLocator"
    ]
    checkpoint_ref = await parent._record_canonical_step_checkpoint(
        "node-1",
        boundary=manifest["boundary"],
        updated_at=now,
    )

    assert checkpoint_ref is None
    assert activity_calls == expected["activityCalls"]
    assert parent._step_checkpoint_capture_outcomes["node-1"] == expected[
        "captureOutcome"
    ]


async def test_omnigent_agent_profile_rerun_compiles_trusted_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:ec891001 at the workflow-to-AgentRun request boundary."""

    replay_id = "omnigent-agent-profile-rerun-compiler"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()
    parent_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["runId"],
        task_queue="mm.workflow.merge_automation",
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: parent_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)

    with pytest.raises(ValueError, match=manifest["escapedFailure"]):
        parent._compile_authored_omnigent_selection(
            manifest["workflowParameters"]["omnigent"],
            path="node[node-1].omnigent",
        )

    request = parent._build_agent_execution_request(
        node_inputs=manifest["nodeInputs"],
        node_id="node-1",
        tool_name="omnigent",
        workflow_parameters=manifest["workflowParameters"],
    )

    assert request.agent_kind == "external"
    assert request.agent_id == "omnigent"
    assert request.execution_profile_ref == expected["executionProfileRef"]
    assert request.parameters["omnigent"] == expected["omnigent"]


async def test_omnigent_lifecycle_retry_preserves_long_event_identities(
    tmp_path: Path,
) -> None:
    """Replay mm:96eb128d at the lifecycle journal persistence boundary."""

    replay_id = "omnigent-lifecycle-dedup-retry"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        store = OmnigentBridgeSessionStore(
            async_sessionmaker(engine, expire_on_commit=False)
        )
        request = AgentExecutionRequest.model_validate(manifest["request"])
        row = await store.get_or_create(
            request=request,
            endpoint_ref="pending",
            agent_id=None,
            agent_name=None,
            target_metadata={},
        )

        for identity in manifest["lifecycleEventIdentities"]:
            await store.record_lifecycle_event(
                request.idempotency_key,
                event_type="request_validated",
                event_identity=identity,
            )
            await store.record_lifecycle_event(
                request.idempotency_key,
                event_type="request_validated",
                event_identity=identity,
            )

        events = await store.list_events(row.bridge_session_id)
        keys = [event.deduplication_key for event in events]
        assert len(events) == expected["eventCount"]
        assert len(set(keys)) == expected["distinctDeduplicationKeyCount"]
        assert max(map(len, keys)) <= expected["maximumDeduplicationKeyLength"]
        assert [event.metadata_["eventIdentity"] for event in events] == manifest[
            "lifecycleEventIdentities"
        ]
    finally:
        await engine.dispose()


async def test_omnigent_auto_run_resolves_empty_skill_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:717ea339 at the parent-to-AgentRun skill handoff."""

    replay_id = "omnigent-auto-skill-projection"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["incidentRunId"],
        parent=None,
        search_attributes={},
    )

    async def resolve_skillset(*_args: object, **_kwargs: object) -> object:
        return manifest["resolvedSkillSet"]

    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        resolve_skillset,
    )
    parent = MoonMindRunWorkflow()
    parent._owner_id = manifest["ownerId"]
    parent._step_ledger_rows = [
        {"logicalStepId": manifest["logicalStepId"], "attempt": 1}
    ]

    resolved_ref = await parent._resolve_agent_node_skillset_ref(
        task_skills=None,
        node_inputs=manifest["nodeInputs"],
        node_id=manifest["logicalStepId"],
        existing_skillset_ref=None,
    )
    request = parent._build_agent_execution_request(
        node_inputs=manifest["nodeInputs"],
        node_id=manifest["logicalStepId"],
        tool_name="omnigent",
        resolved_skillset_ref=resolved_ref,
    )

    assert resolved_ref == expected["resolvedSkillsetRef"]
    assert request.resolved_skillset_ref == expected["resolvedSkillsetRef"]
    assert request.step_execution is not None
    assert (
        request.step_execution.resolved_skillset_ref
        == expected["resolvedSkillsetRef"]
    )


async def test_omnigent_egress_attestation_uses_docker_command_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:706d05e2 at the trusted Docker command boundary."""

    replay_id = "omnigent-egress-attestation-command"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())
    runtime._run = AsyncMock(return_value=(0, "{}", ""))

    async def attest(*, runner, profile, backend_ref):
        assert profile == OMNIGENT_EGRESS_PROFILE
        assert backend_ref == manifest["backendRef"]
        await runner(tuple(manifest["attestationSubcommand"]))
        return {"status": "passed"}

    monkeypatch.setattr(
        oauth_host_runtime_module,
        "attest_docker_egress",
        attest,
    )

    result = await runtime._attest_egress(
        {"networkRef": manifest["networkRef"]}
    )

    assert result == {"status": "passed"}
    runtime._run.assert_awaited_once_with(
        *expected["runtimeCommand"],
        check=False,
    )


async def test_omnigent_runtime_scripts_cross_remote_daemon_path_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:b2f61d86 at the worker-to-Docker bind boundary."""

    replay_id = "omnigent-runtime-scripts-daemon-path"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    source = tmp_path / "source-scripts"
    source.mkdir()
    for name in manifest["requiredScripts"]:
        script = source / name
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        script.chmod(0o755)
    worker_root = tmp_path / "worker-root"
    daemon_root = Path(expected["daemonRoot"]).resolve()
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(worker_root))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", str(daemon_root))
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=source,
        workspace_root=worker_root,
    )
    runtime._run = AsyncMock(
        return_value=(0, f"{daemon_root}\n", "")
    )

    inspected_daemon_root = await runtime._resolve_daemon_workspace_root()

    daemon_scripts = runtime._prepare_daemon_runtime_scripts(
        manifest["workspaceKey"],
        current_step_execution_id="workflow:run:step:execution:1",
        daemon_workspace_root=inspected_daemon_root,
    )

    assert daemon_scripts == Path(expected["daemonScriptsPath"]).resolve()
    relative = daemon_scripts.relative_to(daemon_root)
    worker_scripts = worker_root / relative
    assert worker_scripts != source
    assert all((worker_scripts / name).is_file() for name in manifest["requiredScripts"])
    assert (worker_scripts / "moonmind-execution.sh").is_file()
    runtime._run.assert_awaited_once_with(
        "docker",
        "volume",
        "inspect",
        "--format",
        "{{.Mountpoint}}",
        "agent_workspaces",
        check=False,
    )


async def test_agent_workspace_daemon_root_uses_compose_volume_identity() -> None:
    """Replay mm:cdf36806 at the Compose-to-Docker volume boundary."""

    replay_id = "agent-workspaces-daemon-root"
    load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    worker_environment = {
        key: value
        for key, value in (
            entry.split("=", 1)
            for entry in compose["services"]["temporal-worker-agent-runtime"][
                "environment"
            ]
            if "=" in entry
        )
    }

    assert (
        compose["volumes"]["agent_workspaces"]["name"]
        == expected["volumeNameTemplate"]
    )
    assert (
        worker_environment["MOONMIND_AGENT_WORKSPACES_VOLUME_NAME"]
        == expected["volumeNameTemplate"]
    )
    assert "WORKFLOW_WORKSPACE_DAEMON_ROOT" not in worker_environment


async def test_omnigent_host_entrypoint_arguments_follow_image_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:e96347f3 at the Docker options-to-entrypoint boundary."""

    replay_id = "omnigent-host-entrypoint-env-order"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    login_manifest = load_replay("omnigent-host-login-tools-path", "manifest.json")
    login_expected = load_replay(
        "omnigent-host-login-tools-path", "expected-outcome.json"
    )
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", manifest["hostImageRef"])
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_REF", manifest["hostImageRef"])
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
    runtime._run = AsyncMock(
        side_effect=[
            (1, "", "no such container"),
            (0, "", ""),
            (0, "container-id", ""),
        ]
    )
    binding = _oauth_binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    host_lease = _oauth_host_lease().model_copy(
        update={"container_name": "mm-host-replay"}
    )
    effective_launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex",
    )
    effective_launch["hostImageRef"] = manifest["hostImageRef"]

    await runtime._launch_on_demand(
        binding=binding,
        host_lease=host_lease,
        container_name="mm-host-replay",
        workspace_source=tmp_path,
        skill_projection=tmp_path / "skills",
        runtime_scripts=tmp_path,
        current_step_execution_id="workflow:run:node-1:execution:1",
        effective_launch=effective_launch,
        egress_attestation=_egress_attestation(),
    )

    command = runtime._run.await_args_list[-1].args
    image_index = command.index(manifest["hostImageRef"])
    assert command[image_index - 1] == expected["entrypoint"]
    assert command[image_index + 1 : image_index + 3] == (
        "-u",
        expected["firstUnsetVariable"],
    )
    assert command[-1] == expected["startScript"]
    assert login_manifest["hostImageRef"] == manifest["hostImageRef"]
    assert (
        "type=bind,"
        f"src={tmp_path / login_expected['sourceName']},"
        f"dst={login_expected['containerPath']},readonly"
    ) in command


async def test_omnigent_stock_host_catalog_resolves_lease_owned_host() -> None:
    """Replay mm:99f6a4a0 at the stock host-catalog boundary."""

    replay_id = "omnigent-stock-host-catalog-contract"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected["path"]
        return httpx.Response(
            200,
            json={
                "hosts": [
                    {
                        "host_id": expected["hostId"],
                        "name": manifest["containerName"],
                        "status": "online",
                        "configured_harnesses": {expected["harness"]: True},
                    }
                ]
            },
        )

    client = oauth_host_runtime_module.OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(handler),
    )
    runtime = OmnigentOAuthHostRuntime(client=client)
    binding = _oauth_binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    host_lease = _oauth_host_lease().model_copy(
        update={
            "container_name": manifest["containerName"],
            "omnigent_host_id": None,
        }
    )

    host = await runtime._resolve_exact_host(
        binding=binding,
        host_lease=host_lease,
    )

    assert host["host_id"] == expected["hostId"]
    assert runtime._ready_host_harnesses(host) == {expected["harness"]}


async def test_omnigent_host_registration_waits_for_catalog_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:963a26ba at the host-registration publication boundary."""

    replay_id = "omnigent-host-registration-publication"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    client = SimpleNamespace()
    client.list_hosts = AsyncMock(
        side_effect=[
            [
                {
                    "host_id": host_id,
                    "name": manifest["containerName"],
                    "status": "online",
                }
                for host_id in catalog
            ]
            for catalog in manifest["catalogSequence"]
        ]
    )
    runtime = OmnigentOAuthHostRuntime(client=client)
    binding = _oauth_binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    host_lease = _oauth_host_lease().model_copy(
        update={
            "container_name": manifest["containerName"],
            "omnigent_host_id": None,
        }
    )
    sleep = AsyncMock()
    monkeypatch.setattr(oauth_host_runtime_module.asyncio, "sleep", sleep)

    host = await runtime._resolve_exact_host(
        binding=binding,
        host_lease=host_lease,
    )

    assert host["host_id"] == expected["hostId"]
    assert client.list_hosts.await_count == expected["catalogReadCount"]
    assert sleep.await_count == expected["sleepCount"]
    sleep.assert_awaited_with(expected["sleepSeconds"])


async def test_omnigent_host_registration_covers_observed_long_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:e8ca7030 after two premature host teardowns."""

    replay_id = "omnigent-host-registration-long-publication"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    ready_host = {
        "host_id": expected["hostId"],
        "name": manifest["containerName"],
        "status": "online",
        "configured_harnesses": {"codex-native": True},
    }
    client = SimpleNamespace(
        list_hosts=AsyncMock(
            side_effect=[
                *([[]] * manifest["emptyCatalogReads"]),
                [ready_host],
            ]
        )
    )
    runtime = OmnigentOAuthHostRuntime(client=client)
    binding = _oauth_binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    host_lease = _oauth_host_lease().model_copy(
        update={
            "container_name": manifest["containerName"],
            "omnigent_host_id": None,
        }
    )
    sleep = AsyncMock()
    monkeypatch.setattr(oauth_host_runtime_module.asyncio, "sleep", sleep)

    host = await runtime._resolve_exact_host(
        binding=binding,
        host_lease=host_lease,
    )

    assert host["host_id"] == expected["hostId"]
    assert client.list_hosts.await_count == expected["catalogReadCount"]
    assert sleep.await_count == expected["sleepCount"]
    sleep.assert_awaited_with(expected["sleepSeconds"])


async def test_tool_output_only_omnigent_turn_continues_before_publication(
    tmp_path: Path,
) -> None:
    """Replay mm:651a14f6 at the session-terminal/publication handoff."""

    replay_id = "omnigent-tool-output-terminal-continuation"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    inspector = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(
            get_session=AsyncMock(
                return_value={
                    "status": manifest["sessionStatus"],
                    "items": manifest["items"],
                }
            )
        ),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    incomplete = await inspector.inspect_session_completion(
        expected["resumeSessionId"]
    )
    assert incomplete["terminalAssistantAfterWork"] is expected[
        "terminalAssistantAfterWork"
    ]
    assert incomplete["toolResultCount"] == expected["toolResultCount"]

    runner_calls: list[dict[str, object]] = []

    async def execute(_request, **kwargs):
        runner_calls.append(dict(kwargs))
        return AgentRunResult(
            summary="turn complete",
            metadata={"omnigentSessionId": expected["resumeSessionId"]},
        )

    completed = {
        **incomplete,
        "itemCount": incomplete["itemCount"] + 2,
        "assistantMessageCount": incomplete["assistantMessageCount"] + 1,
        "terminalAssistantAfterWork": True,
    }
    _ordered, _authority, metadata, result = (
        await _drive_authority_chain_coordinator(
            execute,
            completion_evidence=[incomplete, completed],
        )
    )

    assert result.failure_class is None
    assert metadata["push_status"] == expected["pushStatus"]
    assert metadata["repositoryContinuationCount"] == expected[
        "continuationCount"
    ]
    assert runner_calls[1]["resume_session_id"] == expected["resumeSessionId"]


async def test_unposted_terminal_bridge_reopens_for_temporal_activity_retry(
    tmp_path: Path,
) -> None:
    """Replay mm:651a14f6 at the failed-preflight Activity retry boundary."""

    replay_id = "omnigent-unposted-activity-retry"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(sessions)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=manifest["idempotencyKey"],
    )
    try:
        await store.get_or_create(
            request=request,
            endpoint_ref="pending",
            agent_id=None,
            agent_name=None,
            target_metadata={},
        )
        await store.mark_terminal(
            request.idempotency_key,
            status="failed",
            terminal_refs=manifest["terminalRefs"],
        )

        reopened = await store.get_or_create(
            request=request,
            endpoint_ref="pending",
            agent_id=None,
            agent_name=None,
            target_metadata={},
        )
    finally:
        await engine.dispose()

    assert reopened.status == expected["status"]
    assert reopened.first_message_state == expected["firstMessageState"]
    assert reopened.omnigent_session_id is expected["omnigentSessionId"]
    assert reopened.metadata_["unpostedAttemptHistory"][-1]["status"] == expected[
        "archivedAttemptStatus"
    ]


async def test_abandoned_omnigent_host_cleanup_releases_provider_capacity() -> None:
    """Replay mm:651a14f6 after cancellation killed its coordinator Activity."""

    replay_id = "omnigent-abandoned-provider-lease"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    now = datetime.now(timezone.utc)
    order: list[str] = []
    lease = SimpleNamespace(
        lease_id=manifest["hostLeaseRef"],
        provider_profile_id=manifest["providerProfileId"],
        provider_lease_id=manifest["providerLeaseRef"],
        binding_ref="binding-1",
        container_name="host-1",
        omnigent_session_id=None,
        last_heartbeat_at=now,
        expires_at=now.replace(year=now.year + 1),
        status="stopped",
    )

    async def stop_host(**_kwargs):
        order.append("host_stopped")

    async def release_provider(released):
        order.append("provider_released")
        assert released.lease_id == manifest["providerLeaseRef"]
        assert released.owner_id == manifest["providerLeaseRef"]

    async def mark_stopped(_lease_id):
        order.append("host_lease_stopped")
        lease.status = "stopped"
        return lease

    repository = SimpleNamespace(
        list_active_host_leases=AsyncMock(return_value=[]),
        list_terminal_host_leases_with_active_provider_capacity=AsyncMock(
            return_value=[lease]
        ),
        validate_binding=AsyncMock(
            return_value=SimpleNamespace(
                credential_mount_ref=SimpleNamespace(
                    auth_volume_ref=SimpleNamespace(runtime_id="codex_cli")
                )
            )
        ),
        mark_host_lease_stopped=AsyncMock(side_effect=mark_stopped),
    )
    runtime = SimpleNamespace(
        container_exists=AsyncMock(return_value=True),
        stop_host=AsyncMock(side_effect=stop_host),
        list_managed_containers=AsyncMock(return_value=[]),
    )
    run_store = SimpleNamespace(
        cleanup_required_host_lease_refs=AsyncMock(
            return_value={manifest["hostLeaseRef"]}
        )
    )

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=SimpleNamespace(),
        run_store=run_store,
        lease_client=SimpleNamespace(
            release_lease=AsyncMock(side_effect=release_provider)
        ),
    ).run()

    assert order == expected["releaseOrder"]
    assert result["actions"][-1]["providerLeaseReleased"] is True


async def test_terminal_activity_owner_is_reclaimed_after_late_slot_grant() -> None:
    """Replay the AcquireSlot update completing after its Activity timed out."""

    replay_id = "provider-profile-activity-grant-after-timeout"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._profiles[manifest["providerProfileId"]] = ProfileSlotState(
        profile_id=manifest["providerProfileId"],
        max_parallel_runs=1,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        is_default=True,
        current_leases=[manifest["providerLeaseRef"]],
        lease_metadata={
            manifest["providerLeaseRef"]: {
                "ownerIsWorkflow": False,
                "workflowId": manifest["terminalWorkflowId"],
            }
        },
    )

    holder_ids = manager._lease_holder_workflow_ids(include_activity_owned=True)
    reclaimed = manager._reclaim_terminal_leases(
        {
            manifest["terminalWorkflowId"]: {
                "running": False,
                "status": manifest["terminalStatus"],
            }
        },
        include_activity_owned=True,
    )

    assert holder_ids == [manifest["terminalWorkflowId"]]
    assert reclaimed is expected["reclaimed"]
    assert manager._profiles[manifest["providerProfileId"]].current_leases == []


async def test_completed_profile_manager_update_reopens_within_activity() -> None:
    """Replay the manager completing between ensure and accepted Update."""

    from temporalio.client import WorkflowUpdateFailedError
    from temporalio.exceptions import ApplicationError

    replay_id = "provider-profile-manager-completed-update"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    class Adapter:
        def __init__(self) -> None:
            self.ensure_count = 0
            self.update_count = 0

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            self.ensure_count += 1

        async def update_workflow(self, _workflow_id, update_name, payload):
            self.update_count += 1
            assert update_name == manifest["updateName"]
            assert payload["requester_workflow_id"] == manifest["ownerId"]
            if self.update_count == 1:
                raise WorkflowUpdateFailedError(
                    ApplicationError(
                        "manager completed before accepted Update completed",
                        type=manifest["failureType"],
                        non_retryable=True,
                    )
                )
            return {
                "profile_id": manifest["providerProfileId"],
                "lease_id": manifest["ownerId"],
            }

    adapter = Adapter()
    lease = await ProviderProfileLeaseClient(adapter).acquire_execution_lease(
        runtime_id=manifest["runtimeId"],
        profile_id=manifest["providerProfileId"],
        owner_id=manifest["ownerId"],
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )

    assert adapter.ensure_count == expected["managerEnsureCount"]
    assert adapter.update_count == expected["managerUpdateCount"]
    assert (lease.owner_id == manifest["ownerId"]) is expected[
        "ownerIdentityPreserved"
    ]
    assert bool(lease.lease_id) is expected["leaseGranted"]
    assert expected["activityAttemptConsumed"] is False


async def test_replayed_terminal_event_waits_for_current_marked_turn() -> None:
    """Replay mm:63c53791 continuation messages racing stale SSE terminal state."""

    replay_id = "omnigent-stale-terminal-before-continuation"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    assert _snapshot_contains_current_turn_progress(
        manifest["staleTerminalSnapshot"], marker=manifest["currentTurnMarker"]
    ) is expected["acceptStaleTerminal"]
    assert _snapshot_contains_current_turn_progress(
        manifest["progressedSnapshot"], marker=manifest["currentTurnMarker"]
    ) is True
    assert _snapshot_confirms_current_turn_terminal(
        manifest["progressedSnapshot"], marker=manifest["currentTurnMarker"]
    ) is expected["acceptProgressedTerminal"]
    assert _snapshot_confirms_current_turn_terminal(
        manifest["terminalSnapshot"], marker=manifest["currentTurnMarker"]
    ) is expected["acceptTerminalSnapshot"]

    class BusyNativeClient:
        def __init__(self) -> None:
            self.calls = 0
            self.snapshots = [
                manifest["assistantPreambleSnapshot"],
                *manifest["busyIdleSnapshots"],
            ]

        async def get_session(self, _session_id: str) -> dict[str, object]:
            index = min(self.calls, len(self.snapshots) - 1)
            self.calls += 1
            return self.snapshots[index]

    client = BusyNativeClient()
    response_ids = {
        item["response_id"]
        for snapshot in client.snapshots
        for item in snapshot["items"]
    }
    assert response_ids == {manifest["sharedNativeResponseId"]}
    status, snapshot = await _await_marked_turn_terminal(
        client=client,
        session_id="session-replay",
        marker=manifest["currentTurnMarker"],
        event_count=8,
        terminal_status="completed",
        interval_seconds=0.001,
        quiet_period_seconds=0.02,
        tool_only_quiet_period_seconds=0.02,
    )

    assert status == "completed"
    assert snapshot["items"][-1]["id"] == "output-2"
    assert client.calls >= expected["minimumBusySnapshotsObserved"]
    assert expected["requiresStableQuietPeriod"] is True
    assert expected["assistantPreambleRequiresStableQuietPeriod"] is True
    assert expected["unfinishedToolCallBlocksCompletion"] is True
    assert expected["toolOnlyQuietPeriodSeconds"] == 300


async def test_omnigent_server_is_reachable_from_isolated_host_network() -> None:
    """Replay mm:89e60946 at the host-to-server network boundary."""

    replay_id = "omnigent-host-server-network-reachability"
    load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    )

    assert set(compose["services"]["omnigent"]["networks"]) == set(
        expected["serverNetworks"]
    )
    assert set(
        compose["services"]["omnigent-host-codex"]["networks"]
    ) == set(expected["hostNetworks"])
    assert compose["networks"]["omnigent-egress-network"]["internal"] is True


async def test_omnigent_injected_client_preserves_execution_timeouts() -> None:
    """Replay mm:64c19951 at the Omnigent client transport boundary."""

    replay_id = "omnigent-injected-client-timeout-contract"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    observed: dict[str, dict[str, float | None]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed[request.url.path] = dict(request.extensions["timeout"])
        if request.url.path.endswith("/stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"data: [DONE]\n\n",
            )
        return httpx.Response(202, json={"queued": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as injected_client:
        client = oauth_host_runtime_module.OmnigentHttpClient(
            base_url="https://omnigent.test",
            timeout_seconds=manifest["requestTimeoutSeconds"],
            stream_timeout_seconds=None,
            client=injected_client,
        )
        await client.post_event(manifest["sessionId"], {"type": "message"})
        assert [event async for event in client.stream_events(manifest["sessionId"])] == []

    assert observed[f"/v1/sessions/{manifest['sessionId']}/events"] == expected[
        "requestTimeout"
    ]
    assert observed[f"/v1/sessions/{manifest['sessionId']}/stream"] == expected[
        "streamTimeout"
    ]


async def test_omnigent_stock_sse_event_catalog_is_normalized() -> None:
    """Replay mm:60a4ed2c against the complete stock SSE event catalog."""

    replay_id = "omnigent-stock-sse-event-contract"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey="stock-sse-replay",
    )

    observed: dict[str, str] = {}
    sequence = 0
    for normalized_status, event_types in expected["eventTypesByStatus"].items():
        for event_type in event_types:
            sequence += 1
            result = build_omnigent_bridge_event(
                payload={"type": event_type},
                sequence=sequence,
                request=request,
                omnigent_session_id=manifest["omnigentSessionId"],
            )
            observed[event_type] = result.event["normalizedStatus"]
            assert result.diagnostic is None

    for case in expected["sessionStatusCases"]:
        sequence += 1
        result = build_omnigent_bridge_event(
            payload={"type": "session.status", "status": case["input"]},
            sequence=sequence,
            request=request,
            omnigent_session_id=manifest["omnigentSessionId"],
        )
        assert result.event["normalizedStatus"] == case["normalizedStatus"]

    assert len(observed) == expected["stockEventTypeCountWithoutSessionStatus"]


async def test_omnigent_on_demand_runner_inherits_enforced_proxy_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:933a5f44 at the host-to-runner environment boundary."""

    replay_id = "omnigent-runner-proxy-passthrough"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", manifest["hostImageRef"])
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_REF", manifest["hostImageRef"])
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
    runtime._run = AsyncMock(
        side_effect=[(1, "", "no such container"), (0, "", ""), (0, "", "")]
    )
    binding = _oauth_binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    host_lease = _oauth_host_lease().model_copy(
        update={
            "container_name": manifest["containerName"],
            "omnigent_host_id": None,
        }
    )

    await runtime._launch_on_demand(
        binding=binding,
        host_lease=host_lease,
        container_name=manifest["containerName"],
        workspace_source=tmp_path,
        skill_projection=tmp_path / "skills",
        runtime_scripts=tmp_path,
        current_step_execution_id="workflow:run:node-1:execution:1",
        github_token="fixture-token",
        effective_launch=compile_effective_launch(
            profile_ref="omnigent-codex@1",
            policy_ref="codex-on-demand@1",
            provider_profile_id="codex",
        ),
        egress_attestation=_egress_attestation(),
    )

    launch_command = runtime._run.await_args_list[-1].args
    passthrough = next(
        value.partition("=")[2]
        for value in launch_command
        if value.startswith("OMNIGENT_RUNNER_ENV_PASSTHROUGH=")
    )
    assert passthrough.split(",") == expected["passthroughNames"]


async def test_omnigent_codex_remote_bridge_has_loopback_only_proxy_bypass() -> None:
    """Replay mm:b99b4a69 at the Codex TUI-to-app-server boundary."""

    replay_id = "omnigent-codex-remote-loopback"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    proxy_environment = dict(
        value.split("=", 1) for value in omnigent_proxy_env()
    )

    assert manifest["codexRemoteUrl"].startswith("ws://127.0.0.1:")
    assert proxy_environment["NO_PROXY"] == expected["noProxy"]
    assert proxy_environment["no_proxy"] == expected["noProxy"]
    assert set(proxy_environment["NO_PROXY"].split(",")) == set(
        expected["loopbackHosts"]
    )
    assert manifest["omnigentServerHost"] not in proxy_environment["NO_PROXY"]


async def test_omnigent_workspace_owner_matches_isolated_host_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:cbb14c76 at workspace-to-host identity handoff."""

    replay_id = "omnigent-workspace-runtime-ownership"
    load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "run" / "repo"
    workspace.mkdir(parents=True)
    tracked = workspace / "tracked.txt"
    tracked.write_text("tracked", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "outside-link"
    link.symlink_to(outside)
    observed: list[tuple[Path, int, int, bool]] = []

    def record_chown(path, uid, gid, *, follow_symlinks=True):
        observed.append((Path(path), uid, gid, follow_symlinks))

    monkeypatch.setattr(oauth_host_runtime_module.os, "chown", record_chown)
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        workspace_root=workspace_root,
    )
    runtime._align_workspace_ownership(
        workspace,
        runtime_uid=expected["runtimeUid"],
        runtime_gid=expected["runtimeGid"],
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))
    await runtime._exec_check("mm-omnigent-host-replay")

    observed_paths = {path for path, _uid, _gid, _follow in observed}
    assert {workspace.resolve(), tracked, link} <= observed_paths
    assert outside not in observed_paths
    assert all(
        (uid, gid, follow)
        == (expected["runtimeUid"], expected["runtimeGid"], False)
        for _path, uid, gid, follow in observed
    )
    assert list(runtime._run.await_args_list[-1].args) == expected["gitPreflight"]


async def test_omnigent_hybrid_checkpoint_keeps_session_and_workspace_planes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:595b687c at post-execution checkpoint capture."""

    replay_id = "omnigent-hybrid-checkpoint-planes"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["incidentRunId"],
        task_queue="mm.workflow.merge_automation",
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    parent = MoonMindRunWorkflow()
    now = datetime(2026, 8, 5, 20, 11, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": "node-1", "inputs": {"title": "Implement"}}],
        dependency_map={"node-1": []},
        updated_at=now,
    )
    parent._mark_step_running("node-1", updated_at=now, summary="Implementing")
    parent._record_step_workspace_capture_input(
        "node-1",
        manifest["initialInputs"],
        initialize_omnigent_capture=True,
    )
    parent._record_step_workspace_capture_input("node-1", manifest["resultOutputs"])
    captured: list[dict[str, object]] = []

    async def fake_execute_activity(activity, payload, **_kwargs):
        captured.append({"activity": activity, "payload": payload})
        if activity == expected["captureActivity"]:
            return {
                "status": "captured",
                "workspace": {
                    "kind": payload["kind"],
                    "archiveRef": "artifact://workspace/archive",
                    "manifestRef": "artifact://workspace/manifest",
                    "archiveDigest": "sha256:" + ("d" * 64),
                    "workspaceIdentityDigest": "sha256:" + ("c" * 64),
                },
                "diagnosticRefs": [],
            }
        return {"checkpointRef": "artifact://checkpoint/after_execution"}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    checkpoint_ref = await parent._record_canonical_step_checkpoint(
        "node-1",
        boundary="after_execution",
        updated_at=now,
    )

    assert checkpoint_ref == "artifact://checkpoint/after_execution"
    assert captured[0]["activity"] == expected["captureActivity"]
    assert captured[0]["payload"]["kind"] == expected["workspaceCheckpointKind"]
    assert captured[0]["payload"]["externalStateRef"] == manifest["externalStateRef"]
    assert captured[1]["activity"] == expected["checkpointCreateActivity"]


async def test_sandbox_checkpoint_worker_resolves_omnigent_workspace_volume() -> None:
    """Replay mm:fbbf6deb at the agent-runtime-to-sandbox handoff."""

    replay_id = "omnigent-sandbox-checkpoint-workspace-root"
    load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    sandbox_worker = compose["services"]["temporal-worker-sandbox"]
    environment = dict(
        entry.split("=", 1)
        for entry in sandbox_worker["environment"]
        if "=" in entry
    )

    assert environment["WORKFLOW_WORKSPACE_ROOT"] == expected["workspaceRoot"]
    assert expected["workspaceVolumeMount"] in sandbox_worker["volumes"]


async def test_sandbox_checkpoint_git_trusts_resolved_omnigent_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:ea264977 at the sandbox-to-Git ownership boundary."""

    replay_id = "omnigent-sandbox-checkpoint-git-ownership"
    load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / "temporal_sandbox" / "workspace" / "repo"
    workspace.mkdir(parents=True)
    (workspace / ".git").mkdir()
    resolved_workspace = str(workspace.resolve())
    safe_prefix = [
        "git",
        "-c",
        f"safe.directory={resolved_workspace}",
        "-C",
        resolved_workspace,
    ]
    commands: list[list[str]] = []

    async def enforce_safe_directory(command, **_kwargs):
        normalized = [str(part) for part in command]
        commands.append(normalized)
        if normalized[: len(safe_prefix)] != safe_prefix:
            raise RuntimeError("fatal: detected dubious ownership")
        operation = normalized[len(safe_prefix) :]
        if operation[:2] == ["rev-parse", "HEAD"]:
            return activity_runtime_module.CmdRes(b"abc123\n")
        if operation[0] == "status":
            return activity_runtime_module.CmdRes(b"")
        raise AssertionError(f"unexpected git command: {operation}")

    monkeypatch.setattr(
        activity_runtime_module, "_run_command", enforce_safe_directory
    )
    activities = TemporalSandboxActivities(
        workspace_root=tmp_path,
        artifact_store=InMemoryArtifactStore(),
    )
    result = await activities.workspace_capture_checkpoint(
        {
            "identity": {
                "workflowId": "source",
                "runId": "source-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            "boundary": "after_execution",
            "kind": "worktree_archive",
            "workspacePath": resolved_workspace,
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "omnigent-sandbox-checkpoint-git-ownership",
            "baseCommit": "abc123",
        }
    )

    assert result["status"] == expected["checkpointStatus"]
    assert len(commands) == expected["gitCommandCount"]
    assert all(command[: len(safe_prefix)] == safe_prefix for command in commands)


async def test_invalid_required_omnigent_checkpoint_blocks_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:ea264977 at the checkpoint-to-publication gate."""

    replay_id = "omnigent-required-checkpoint-finalization"
    load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()
    now = datetime(2026, 8, 5, 21, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _id: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="workflow-1", run_id="run-1"),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: now)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    parent._mark_step_running("implement", updated_at=now, summary="Implementing")
    parent._step_workspace_capture_inputs["implement"] = {
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": "workspace-1",
            "relativePath": "repo",
        },
        "criticality": "required",
    }
    parent._step_checkpoint_capture_outcomes["implement"] = {
        "status": "invalid",
        "failureCode": expected["failureCode"],
        "summary": "Git rejected the repository ownership boundary.",
        "capabilityCriticality": "required",
    }
    parent._update_memo = lambda: None  # type: ignore[method-assign]

    async def missing_checkpoint(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        parent, "_record_canonical_step_checkpoint", missing_checkpoint
    )
    await parent._finalize_after_execution_checkpoint("implement", updated_at=now)

    outcome = parent.get_step_ledger()["steps"][0]["finalizationOutcome"]
    assert outcome["status"] == expected["finalizationStatus"]
    assert outcome["failureCode"] == expected["failureCode"]
    assert parent._publish_status == expected["publishStatus"]


async def test_active_omnigent_execution_renews_host_lease_until_terminal() -> None:
    """Replay mm:f2400165 at the execution-to-janitor authority boundary."""

    replay_id = "omnigent-active-host-lease-heartbeat"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    heartbeat_observed = asyncio.Event()

    async def heartbeat_host_lease(lease_id: str, *, ttl_seconds: int):
        assert lease_id == manifest["hostLeaseRef"]
        assert ttl_seconds == expected["leaseTtlSeconds"]
        heartbeat_observed.set()
        return SimpleNamespace(lease_id=lease_id)

    async def execution() -> AgentRunResult:
        await heartbeat_observed.wait()
        return AgentRunResult(summary="completed")

    hosts = SimpleNamespace(
        heartbeat_host_lease=AsyncMock(side_effect=heartbeat_host_lease)
    )
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=AsyncMock(),
        lease_client=SimpleNamespace(),
        host_repository=hosts,
        host_runtime=SimpleNamespace(),
        run_store=SimpleNamespace(),
        execution_runner=AsyncMock(),
        artifact_gateway=SimpleNamespace(),
    )

    result = await coordinator._execute_with_host_lease_heartbeat(
        execution(),
        host_lease_ref=manifest["hostLeaseRef"],
        ttl_seconds=expected["leaseTtlSeconds"],
    )

    assert result.summary == expected["terminalSummary"]
    hosts.heartbeat_host_lease.assert_awaited_once()


async def test_codex_session_record_uses_step_workflow_checkpoint_authority(
    tmp_path: Path,
) -> None:
    """Replay mm:5fe90658 through adapter persistence and checkpoint capture."""
    replay_id = "codex-session-checkpoint-identity"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    step_execution = AgentRuntimeStepExecutionLaunch.model_validate(
        manifest["stepExecution"]
    )

    run_root = tmp_path / manifest["incidentWorkflowId"]
    workspace = run_root / "repo"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.name", "MoonMind Test"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=workspace,
        check=True,
    )
    (workspace / "result.txt").write_text("agent completed\n", encoding="utf-8")
    subprocess.run(["git", "add", "result.txt"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "agent result"], cwd=workspace, check=True
    )

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    adapter = CodexSessionAdapter.__new__(CodexSessionAdapter)
    adapter._run_store = run_store
    adapter._runtime_id = manifest["runtime"]
    adapter._workflow_id = manifest["agentRunWorkflowId"]
    adapter._task_workflow_id = manifest["incidentWorkflowId"]
    now = datetime.now(timezone.utc)
    adapter._persist_managed_run_record(
        run_id="session-turn-1",
        agent_id=manifest["runtime"],
        managed_run_id=manifest["incidentWorkflowId"],
        binding=_binding(),
        workspace_path=str(workspace),
        locator={
            "sessionId": f"sess:{manifest['incidentWorkflowId']}:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-replay",
            "threadId": "thread-replay",
        },
        active_turn_id=None,
        result={"summary": "completed"},
        status="completed",
        started_at=now,
        finished_at=now,
        step_execution=step_execution,
    )

    record = run_store.load(manifest["incidentWorkflowId"])
    assert record is not None
    assert record.workflow_id == expected["persistedWorkflowId"]
    assert record.session_id is not None

    activities = TemporalAgentRuntimeActivities(
        run_store=run_store,
        artifact_service=object(),
        client_adapter=object(),
    )

    async def put(payload: bytes, _content_type: str, kind: str) -> str:
        return f"artifact://{kind}/{hashlib.sha256(payload).hexdigest()}"

    activities._put_managed_checkpoint_artifact = put
    capabilities = resolve_runtime_execution_capabilities(manifest["runtime"])
    capture = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": step_execution.workflow_id,
                "runId": step_execution.run_id,
                "logicalStepId": step_execution.logical_step_id,
                "executionOrdinal": step_execution.execution_ordinal,
            },
            "boundary": "after_execution",
            "checkpointKind": "worktree_archive",
            "workspaceLocator": manifest["workspaceLocator"],
            "expectedRuntimeId": manifest["runtime"],
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "artifactNamespace": "step-checkpoints/node-1",
            "idempotencyKey": f"{step_execution.step_execution_id}:checkpoint",
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )

    assert capture["status"] == expected["checkpointStatus"]
    assert capture["workspace"]["kind"] == "worktree_archive"


async def test_resolved_pr_resolver_contract_owns_durable_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay resolver PR 2189's missing terminal-contract launch payload."""

    replay_id = "pr-resolver-resolved-terminal-contract"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent_info = SimpleNamespace(
        workflow_id=manifest["parentWorkflowId"],
        run_id=manifest["parentRunId"],
    )
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["incidentRunId"],
        parent=parent_info,
        search_attributes={},
    )

    async def resolve_skillset(*_args: object, **_kwargs: object) -> object:
        return manifest["resolvedSkillSet"]

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        resolve_skillset,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch: True,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: workflow_info,
    )
    parent = MoonMindRunWorkflow()
    parent._owner_id = "owner-replay"
    resolved_ref = await parent._resolve_agent_node_skillset_ref(
        task_skills=None,
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        existing_skillset_ref=None,
    )
    request = parent._build_agent_execution_request(
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        tool_name=manifest["planNodeInputs"]["targetRuntime"],
        resolved_skillset_ref=resolved_ref,
        workflow_parameters={"mergeGate": manifest["mergeGate"]},
    )

    assert request.terminal_contract is not None
    assert request.terminal_contract.contract_id == expected["terminalContractId"]
    assert (
        request.terminal_contract.relative_path
        == expected["terminalContractPath"]
    )
    assert (
        request.terminal_contract.expected_schema_version
        == expected["terminalContractSchemaVersion"]
    )
    assert request.terminal_continuation_authority is not None
    assert (
        request.terminal_continuation_authority.owner_workflow_type
        == expected["continuationOwnerWorkflowType"]
    )
    assert expected["continuationAction"] in (
        request.terminal_continuation_authority.allowed_actions
    )

    async def read_existing_skillset(*_args: object, **_kwargs: object) -> object:
        return manifest["resolvedSkillSet"]

    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        read_existing_skillset,
    )
    existing_parent = MoonMindRunWorkflow()
    existing_parent._owner_id = "owner-replay-existing"
    existing_ref = await existing_parent._resolve_agent_node_skillset_ref(
        task_skills=None,
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        existing_skillset_ref=manifest["existingResolvedSkillsetRef"],
    )
    existing_request = existing_parent._build_agent_execution_request(
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        tool_name=manifest["planNodeInputs"]["targetRuntime"],
        resolved_skillset_ref=existing_ref,
        workflow_parameters={"mergeGate": manifest["mergeGate"]},
    )

    assert existing_ref == manifest["existingResolvedSkillsetRef"]
    assert existing_request.terminal_contract is not None
    assert (
        existing_request.terminal_contract.contract_id
        == expected["terminalContractId"]
    )
    assert existing_request.terminal_continuation_authority is not None
    assert (
        existing_request.terminal_continuation_authority.owner_workflow_type
        == expected["continuationOwnerWorkflowType"]
    )


async def test_retry_before_execution_captures_terminal_prior_workspace(
    tmp_path: Path,
) -> None:
    """Replay resolver PR 2189's retry checkpoint authority handoff."""

    replay_id = "managed-checkpoint-retry-baseline"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / manifest["incidentWorkflowId"] / "repo"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "resolver-result.txt").write_text(
        "first execution requested durable gate continuation\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=MoonMind Test", "-c",
            "user.email=test@example.invalid", "commit", "-qm",
            "checkpoint replay",
        ],
        cwd=workspace,
        check=True,
    )

    now = datetime.now(timezone.utc)
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId=manifest["incidentWorkflowId"],
            workflowId=f"{manifest['incidentWorkflowId']}:agent:node-1",
            ownerRunId=manifest["incidentRunId"],
            logicalStepId=manifest["logicalStepId"],
            executionOrdinal=manifest["completedExecutionOrdinal"],
            agentId=manifest["runtime"],
            runtimeId=manifest["runtime"],
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(workspace),
            sessionId=f"sess:{manifest['incidentWorkflowId']}:codex_cli",
            sessionEpoch=1,
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=run_store,
        artifact_service=object(),
        client_adapter=object(),
    )

    async def put(payload: bytes, _content_type: str, kind: str) -> str:
        return f"artifact://{kind}/{hashlib.sha256(payload).hexdigest()}"

    activities._put_managed_checkpoint_artifact = put
    capabilities = resolve_runtime_execution_capabilities(manifest["runtime"])
    capture = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": manifest["incidentWorkflowId"],
                "runId": manifest["incidentRunId"],
                "logicalStepId": manifest["logicalStepId"],
                "executionOrdinal": manifest["retryExecutionOrdinal"],
            },
            "boundary": manifest["checkpointBoundary"],
            "checkpointKind": "worktree_archive",
            "workspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": manifest["runtime"],
                "agentRunId": manifest["incidentWorkflowId"],
                "relativePath": "repo",
            },
            "expectedRuntimeId": manifest["runtime"],
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "artifactNamespace": "step-checkpoints/node-1",
            "idempotencyKey": (
                f"{manifest['incidentWorkflowId']}:{manifest['incidentRunId']}:"
                f"{manifest['logicalStepId']}:execution:"
                f"{manifest['retryExecutionOrdinal']}:checkpoint:"
                "before_execution:capture"
            ),
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )

    assert capture["status"] == expected["checkpointStatus"]
    assert capture["workspace"]["kind"] == expected["checkpointKind"]


async def test_codex_oauth_failure_preserves_primary_error_and_managed_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:28dae38e through the child-to-parent authority handoff."""
    replay_id = "codex-oauth-checkpoint-masking"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    classified = classify_provider_failure(manifest["providerLog"])

    assert classified is not None
    assert classified.failure_class == expected["failureClass"]
    assert classified.provider_error_code == expected["providerErrorCode"]
    assert classified.retry_recommendation == expected["retryRecommendation"]

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=f"{manifest['incidentWorkflowId']}:agent:node-1",
        run_id="replay-run",
        search_attributes={},
        parent=None,
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch: True)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=f"{manifest['incidentWorkflowId']}:replay",
        managedSession={
            "workflowId": f"{manifest['incidentWorkflowId']}:session:codex_cli",
            "agentRunId": manifest["incidentWorkflowId"],
            "sessionId": f"sess:{manifest['incidentWorkflowId']}:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        },
    )
    result = MoonMindAgentRun()._enrich_result_metadata(
        request=request,
        result=AgentRunResult(
            summary=manifest["providerLog"],
            failureClass=classified.failure_class,
            providerErrorCode=classified.provider_error_code,
            retryRecommendation=classified.retry_recommendation,
            metadata={"workspacePath": manifest["legacyWorkspacePath"]},
        ),
    )

    assert result is not None
    assert result.metadata["workspaceLocator"] == expected["workspaceLocator"]
    assert "workspacePath" not in result.metadata

    parent_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id="replay-parent-run",
        task_queue="mm.workflow",
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: parent_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)

    activity_calls: list[str] = []

    async def managed_checkpoint_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        activity_calls.append(activity_type)
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return {
                "status": "captured",
                "workspace": {
                    "kind": "worktree_archive",
                    "baseCommit": "abc123",
                    "archiveRef": "artifact://managed/archive",
                    "archiveDigest": "sha256:" + ("a" * 64),
                    "manifestRef": "artifact://managed/manifest",
                    "manifestDigest": "sha256:" + ("b" * 64),
                    "includesUntracked": True,
                    "includesIgnoredFiles": False,
                },
                "diagnosticRefs": ["artifact://managed/manifest"],
                "idempotencyKey": payload["idempotencyKey"],
            }
        if activity_type == "step_checkpoint.create":
            return {
                "checkpointRef": "artifact://checkpoint/after_execution",
                "checkpointId": payload["idempotencyKey"],
            }
        raise AssertionError(f"unexpected activity: {activity_type}")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        managed_checkpoint_activity,
    )
    parent = MoonMindRunWorkflow()
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": "node-1", "inputs": {"title": "Replay"}}],
        dependency_map={"node-1": []},
        updated_at=now,
    )
    parent._mark_step_running("node-1", updated_at=now, summary="Running")
    parent._record_step_workspace_capture_input("node-1", result.metadata)

    checkpoint_ref = await parent._record_canonical_step_checkpoint(
        "node-1", boundary="after_execution", updated_at=now
    )

    assert checkpoint_ref == "artifact://checkpoint/after_execution"
    assert activity_calls == [
        "agent_runtime.capture_workspace_checkpoint",
        "step_checkpoint.create",
    ]


async def test_checkpoint_capture_heartbeat_backpressure_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:c2723c5c through the managed checkpoint activity boundary."""

    replay_id = "checkpoint-heartbeat-backpressure"
    manifest = load_replay(replay_id, "manifest.json")
    workspace_manifest = load_replay(replay_id, "workspace-manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / manifest["incidentWorkflowId"] / "repo"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    for artifact in workspace_manifest["artifacts"]:
        (workspace / artifact["path"]).write_text(
            artifact["content"], encoding="utf-8"
        )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=MoonMind Test", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "checkpoint replay",
        ],
        cwd=workspace,
        check=True,
    )

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    now = datetime.now(timezone.utc)
    run_store.save(
        ManagedRunRecord(
            runId=manifest["incidentWorkflowId"],
            workflowId=manifest["incidentWorkflowId"],
            ownerRunId=manifest["incidentRunId"],
            logicalStepId=manifest["logicalStepId"],
            executionOrdinal=1,
            agentId=manifest["runtime"],
            runtimeId=manifest["runtime"],
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(workspace),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=run_store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, kind: str) -> str:
        return f"artifact://{kind}/{hashlib.sha256(payload).hexdigest()}"

    activities._put_managed_checkpoint_artifact = put
    heartbeat_queue: asyncio.Queue[object] = asyncio.Queue(
        maxsize=manifest["heartbeatQueueCapacity"]
    )
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity, "in_activity", lambda: True
    )
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: heartbeat_queue.put_nowait(payload),
    )
    monkeypatch.setattr(
        activity_runtime_module,
        "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS",
        manifest["heartbeatIntervalSeconds"],
    )
    capabilities = resolve_runtime_execution_capabilities(manifest["runtime"])

    capture = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": manifest["incidentWorkflowId"],
                "runId": manifest["incidentRunId"],
                "logicalStepId": manifest["logicalStepId"],
                "executionOrdinal": 1,
            },
            "boundary": "before_publication",
            "checkpointKind": "worktree_archive",
            "workspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": manifest["runtime"],
                "agentRunId": manifest["incidentWorkflowId"],
                "relativePath": "repo",
            },
            "expectedRuntimeId": manifest["runtime"],
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "artifactNamespace": "step-checkpoints/assessment",
            "idempotencyKey": "checkpoint-heartbeat-backpressure:capture",
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )

    assert capture["status"] == expected["checkpointStatus"]
    assert heartbeat_queue.qsize() <= expected["maxQueuedHeartbeats"]


async def test_checkpoint_multipart_failure_replay_preserves_terminal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:2ca7d450 without allowing a transient summary to win."""

    replay_id = "checkpoint-multipart-finalization-summary"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": manifest["logicalStepId"], "inputs": {}}],
        dependency_map={manifest["logicalStepId"]: []},
        updated_at=now,
    )
    row = parent._step_ledger_row_for(manifest["logicalStepId"])
    assert row is not None
    row["finalizationOutcome"] = manifest["finalizationOutcome"]
    parent._publish_status = manifest["publishStatus"]
    parent._publish_reason = manifest["publishReason"]
    parent._summary = manifest["transientSummary"]

    status, message, publish_failure = parent._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == expected["status"]
    assert message == expected["message"]
    assert publish_failure is expected["publishFailure"]
    assert message != expected["forbiddenSummary"]

    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: now)
    summary = await _finalize_and_capture_summary(
        monkeypatch,
        parent,
        parameters={"publishMode": "pr"},
        status=status,
        error=message,
    )
    assert summary["finishOutcome"]["reason"] == expected["message"]
    assert summary["publish"]["reason"] == expected["publishReason"]


async def test_codex_system_error_waits_for_delayed_oauth_failure_log(
    tmp_path: Path,
) -> None:
    """Replay mm:32a5549d through the real managed-session runtime boundary."""

    replay_id = "codex-oauth-log-settle-race"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    request = launch_request(tmp_path)
    transcript_path = Path(request.codex_home_path) / manifest["rolloutRelativePath"]
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        omit_turns_on_read=True,
        thread_status_type=manifest["threadStatusType"],
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[manifest["terminalRolloutEvent"]],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    log_path = Path(request.codex_home_path) / "logs_1.sqlite"
    with sqlite3.connect(log_path) as connection:
        connection.execute(
            "CREATE TABLE logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER, "
            "feedback_log_body TEXT"
            ")"
        )

    def commit_provider_failure_after_terminal_event() -> None:
        time.sleep(manifest["providerLogDelaySeconds"])
        with sqlite3.connect(log_path) as connection:
            connection.execute(
                "INSERT INTO logs (ts, feedback_log_body) VALUES (?, ?)",
                (int(time.time()), manifest["providerLog"]),
            )

    writer = threading.Thread(
        target=commit_provider_failure_after_terminal_event,
        daemon=True,
    )
    writer.start()
    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )
    writer.join(timeout=2)

    assert not writer.is_alive()
    assert response.status == "failed"
    assert response.metadata["failureClass"] == expected["runtimeFailureClass"]
    assert response.metadata["reason"] == expected["reason"]
    assert "retryRecommendedAction" not in response.metadata
    classified = classify_provider_failure(response.metadata["reason"])
    assert classified is not None
    assert classified.failure_class == expected["failureClass"]
    assert classified.provider_error_code == expected["providerErrorCode"]
    assert classified.retry_recommendation == expected["retryRecommendation"]


async def test_codex_stale_observer_cannot_rollback_cleared_session(
    tmp_path: Path,
) -> None:
    """Replay mm:1b5eacdb at the direct managed-session state boundary."""

    replay_id = "managed-session-stale-state-write"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    request = launch_request(tmp_path)
    script = write_fake_app_server(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id=manifest["locatorBeforeClear"]["containerId"],
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    stale_observer_state = runtime._load_state()

    runtime.clear_session(
        CodexManagedSessionClearRequest(
            **manifest["clearRequest"],
        )
    )
    stale_observer_state.last_control_action = "session_status"

    with pytest.raises(RuntimeError, match=expected["staleWriteError"]):
        runtime._save_state(stale_observer_state)

    authoritative_state = runtime._load_state()
    assert authoritative_state.session_epoch == expected["sessionEpoch"]
    assert authoritative_state.logical_thread_id == expected["threadId"]
    assert authoritative_state.state_revision > stale_observer_state.state_revision


async def test_checkpoint_finalization_fault_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    workspace_root = tmp_path / "workspaces"
    repo = workspace_root / "temporal_sandbox" / "run-3145" / "repo"
    repo.mkdir(parents=True)
    activities = TemporalSandboxActivities(workspace_root=workspace_root)

    agent_execution_calls = 0
    durable_execution_result: dict[str, object] | None = None

    async def execute_agent_once() -> dict[str, object]:
        """Primary agent execution is exactly-once and durable across retries."""
        nonlocal agent_execution_calls, durable_execution_result
        if durable_execution_result is None:
            agent_execution_calls += 1
            durable_execution_result = load_replay(replay_id, "execution-result.json")
        return durable_execution_result

    original_capture = activities._capture_workspace_evidence
    fault = FinalizationFaultInjector()

    async def fail_once(model: object, workspace: Path):
        # The retried finalization path must reuse the durable primary execution
        # result, never re-run the agent. If a regression re-executed the agent on
        # each finalization attempt, ``agent_execution_calls`` would exceed one.
        execution = await execute_agent_once()
        assert execution["status"] == "completed"
        return await fault.invoke(original_capture, model, workspace)

    monkeypatch.setattr(activities, "_capture_workspace_evidence", fail_once)
    payload = {
        "identity": {
            "workflowId": "wf-reliability-3145",
            "runId": "run-3145",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "kind": "worktree_archive",
        "workspacePath": str(repo),
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "reliability-3145-after-execution",
    }
    first = await activities.workspace_capture_checkpoint(payload)
    assert first["status"] == "invalid"
    assert durable_execution_result["status"] == "completed"
    second = await activities.workspace_capture_checkpoint(payload)
    assert second["status"] == "captured"
    assert durable_execution_result["status"] == "completed"
    assert fault.calls == 2
    assert agent_execution_calls == 1


async def test_remediation_loop_attempts_inherit_the_runs_resolved_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:dab3b32b at the loop-materialization to AgentRun dispatch boundary."""

    replay_id = "remediation-loop-runtime-inheritance"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["incidentRunId"],
        parent=None,
        search_attributes={},
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: workflow_info,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch: True,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime(2026, 7, 24, 19, 52, tzinfo=timezone.utc),
    )

    parent = MoonMindRunWorkflow()
    parent._owner_id = "owner-replay"
    parent._initialize_remediation_loop_controller(
        ordered_nodes=[manifest["controllerPlanNode"]]
    )
    remediation, verification = parent._materialize_remediation_attempt(
        ordinal=expected["attemptOrdinal"]
    )

    assert [remediation["id"], verification["id"]] == (
        expected["materializedNodeIds"]
    )
    for node in (remediation, verification):
        assert node["tool"]["name"] == expected["toolName"]
        request = parent._build_agent_execution_request(
            node_inputs=dict(node["inputs"]),
            node_id=str(node["id"]),
            tool_name=str(node["tool"]["name"]),
            workflow_parameters=manifest["workflowParameters"],
        )
        # AgentRun only enters external adapter dispatch for agentKind 'external'.
        assert request.agent_kind == expected["agentKind"]
        assert request.agent_id == expected["agentId"]
        assert request.execution_profile_ref == expected["executionProfileRef"]
        assert request.instruction_ref == node["inputs"]["instructions"]
        assert request.parameters["model"] == expected["model"]
        assert request.parameters["effort"] == expected["effort"]

    # The escaped incident routed the sentinel into external adapter resolution,
    # which no provider can satisfy. Keep that boundary failing loudly.
    with pytest.raises(ValueError, match=expected["rejectedDispatchError"]):
        await agent_run_module.resolve_adapter_metadata(expected["rejectedAgentId"])


async def test_remediation_attempt_receives_authoritative_verifier_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:0d128884 at verifier-to-remediator admission."""

    replay_id = "remediation-verifier-evidence-contract"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["replayRunId"],
        parent=None,
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        == RUN_REMEDIATION_EXPLICIT_EVIDENCE_INPUTS_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    parent = MoonMindRunWorkflow()
    parent._initialize_remediation_loop_controller(
        ordered_nodes=[manifest["controllerPlanNode"]]
    )
    parent._step_ledger_rows = []
    parent._write_json_artifact = AsyncMock(
        return_value="artifact://decision/attempt-6"
    )
    ordered_nodes: list[dict[str, object]] = []

    admitted = await parent._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref=manifest["gateResultRef"],
        remaining_work_ref=manifest["remainingWorkRef"],
    )

    assert admitted is True
    remediation_inputs = ordered_nodes[0]["inputs"]
    assert isinstance(remediation_inputs, dict)
    assert remediation_inputs["selectedSkill"] == expected["selectedSkill"]
    assert remediation_inputs["gateResultRef"] == expected["gateResultRef"]
    assert remediation_inputs["remainingWorkRef"] == expected["remainingWorkRef"]
    assert f"- gateResultRef: {expected['gateResultRef']}" in (
        remediation_inputs["instructions"]
    )
    request = parent._build_agent_execution_request(
        node_inputs=remediation_inputs,
        node_id=str(ordered_nodes[0]["id"]),
        tool_name=str(ordered_nodes[0]["tool"]["name"]),
    )
    assert request.parameters["gateResultRef"] == expected["gateResultRef"]
    assert request.parameters["remainingWorkRef"] == expected["remainingWorkRef"]


async def test_checkpointless_remediation_keeps_the_verified_repository_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:6ca2d8a6 at verifier-to-remediation dispatch."""

    replay_id = "headless-remediation-workspace-continuity"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["incidentRunId"],
        parent=None,
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            RUN_WORKFLOW_OWNED_REMEDIATION_HEAD_PATCH,
            RUN_WORKFLOW_HEADLESS_REMEDIATION_PATCH,
            RUN_HEADLESS_REMEDIATION_VERIFIED_WORKSPACE_PATCH,
        },
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    parent = MoonMindRunWorkflow()
    parent._initialize_remediation_loop_controller(
        ordered_nodes=[manifest["controllerPlanNode"]]
    )
    parent._step_ledger_rows = []
    parent._write_json_artifact = AsyncMock(
        return_value="artifact://decision/verified-workspace"
    )
    source = manifest["sourceVerifier"]
    workspace_spec = parent._verified_headless_remediation_workspace_spec(
        node_inputs=source["nodeInputs"],
        outputs=source["outputs"],
    )
    ordered_nodes: list[dict[str, object]] = []

    admitted = await parent._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref=source["gateResultRef"],
        remaining_work_ref=source["gateResultRef"],
        logical_step_id=source["logicalStepId"],
        headless_workspace_spec=workspace_spec,
    )

    assert admitted is True
    assert len(ordered_nodes) == 2
    for node, expected_workspace in zip(
        ordered_nodes,
        (
            expected["remediationWorkspaceSpec"],
            expected["verificationWorkspaceSpec"],
        ),
        strict=True,
    ):
        request = parent._build_agent_execution_request(
            node_inputs=dict(node["inputs"]),
            node_id=str(node["id"]),
            tool_name=str(node["tool"]["name"]),
            workflow_parameters=manifest["workflowParameters"],
        )
        assert request.workspace_spec == expected_workspace
        assert request.workspace_spec != manifest["escapedWorkspaceSpec"]


async def test_instructionless_remediation_verifier_is_rejected_before_dispatch() -> None:
    """Replay mm:35d1ad7b at remediation-loop controller admission."""

    replay_id = "remediation-loop-missing-verifier-instructions"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()

    with pytest.raises(ValueError, match=expected["error"]):
        parent._initialize_remediation_loop_controller(
            ordered_nodes=[manifest["controllerPlanNode"]]
        )


async def test_instructionless_inflight_remediation_loop_remains_replayable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admission invariant must not rewrite an already-recorded history."""

    manifest = load_replay(
        "remediation-loop-missing-verifier-instructions",
        "manifest.json",
    )
    parent = MoonMindRunWorkflow()
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(run_id="inflight-replay-run"),
    )

    parent._initialize_remediation_loop_controller(
        ordered_nodes=[manifest["controllerPlanNode"]],
        require_agent_instructions=False,
    )

    assert parent._remediation_loop_spec is not None
    assert parent._remediation_loop_spec.loop_id == "issue-implementation-remediation"


async def test_omnigent_pr_resolver_runtime_authority_replay(
    tmp_path: Path,
) -> None:
    """Replay mm:e09594b0 across Codex auth and terminal evidence handoffs."""

    replay_id = "omnigent-pr-resolver-runtime-authority"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace_id = manifest["workspaceLocator"]["workspaceId"]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    result_path = workspace / manifest["terminalContract"]["relativePath"]
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "pr-resolver-result.v1",
                "executionRef": manifest["stepExecutionId"],
                "mergeAutomationDisposition": "already_merged",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    publish_path = workspace / "artifacts" / "publish_result.json"
    publish_path.parent.mkdir(parents=True)
    publish_path.write_text(
        json.dumps(
            {
                "schemaVersion": "moonmind.publish.auto.v1",
                "mode": "auto",
                "owner": "agent",
                "skillId": "pr-resolver",
                "executionRef": manifest["stepExecutionId"],
                "status": "verified",
                "action": "merge",
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "feature",
                "localHead": "abc123",
                "remoteBranchHead": None,
                "remoteVerified": True,
                "pushed": False,
                "merged": True,
                "prUrl": (
                    "https://github.com/MoonLadderStudios/MoonMind/pull/1"
                ),
                "blockedReason": None,
                "verificationCommands": ["gh pr view 1"],
            }
        ),
        encoding="utf-8",
    )
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id=manifest["incidentWorkflowId"],
            step_execution_id=manifest["stepExecutionId"],
            relative_path="repo",
        )
    )

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex_openai_oauth",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=manifest["idempotencyKey"],
        stepExecution={
            "workflowId": manifest["incidentWorkflowId"],
            "runId": manifest["incidentRunId"],
            "logicalStepId": "node-1",
            "executionOrdinal": 2,
            "stepExecutionId": manifest["stepExecutionId"],
            "runtimeContextPolicy": "fresh_agent_run",
        },
        workspaceSpec={"workspaceLocator": manifest["workspaceLocator"]},
        terminalContract=manifest["terminalContract"],
        parameters={
            "requiredCapabilities": ["git", "gh"],
            "omnigent": {
                "agent": {"agentName": CODEX_STOCK_AGENT_NAME},
                "session": {
                    "hostType": "external",
                    "hostId": "host-replay",
                    "workspace": manifest["hostWorkspaceAlias"],
                },
            },
        },
    )
    selection = build_omnigent_selection(request)
    create_payload = build_omnigent_session_create_payload(
        request=request,
        selection=selection,
        target=OmnigentResolvedTarget(
            agent_id="ag-codex-replay",
            source="agent_id",
        ),
    )
    assert create_payload["terminal_launch_args"] == expected[
        "terminalLaunchArgs"
    ]
    assert all(
        "token=" not in value.lower()
        for value in create_payload["terminal_launch_args"]
    )
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=REPO_ROOT / "services" / "omnigent" / "scripts",
        workspace_root=tmp_path,
    )
    runtime_scripts = runtime._prepare_runtime_scripts(
        manifest["idempotencyKey"],
        current_step_execution_id=manifest["stepExecutionId"],
    )
    assert expected["stepExecutionIdentitySource"] == (
        "/etc/profile.d/moonmind-execution.sh"
    )
    assert manifest["stepExecutionId"] in (
        runtime_scripts / "moonmind-execution.sh"
    ).read_text(encoding="utf-8")

    activities = TemporalAgentRuntimeActivities(
        workspace_root=tmp_path,
        client_adapter=object(),
    )
    agent_run = MoonMindAgentRun()

    async def execute_activity(
        name: str, payload: dict[str, object], **_kwargs: object
    ) -> dict[str, object]:
        assert name == "agent_runtime.evaluate_terminal_evidence"
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(
            payload
        )
        return evaluated.model_dump(mode="json", by_alias=True)

    agent_run._execute_routed_activity = execute_activity  # type: ignore[method-assign]
    evaluated = await agent_run._evaluate_terminal_contract(
        request=request,
        result=AgentRunResult(
            summary="PR is already merged.",
            metadata={"workspacePath": manifest["hostWorkspaceAlias"]},
        ),
    )

    assert evaluated.failure_class is None
    assert evaluated.metadata["terminalContractSatisfied"] is True
    assert evaluated.metadata["terminalContractOutcome"] == expected[
        "terminalContractOutcome"
    ]
    assert evaluated.metadata["terminalContractEvidencePath"] == expected[
        "terminalContractEvidencePath"
    ]


async def test_omnigent_pr_resolver_publish_evidence_handoff_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:a5460547 across AgentRun-to-parent publication authority."""

    replay_id = "omnigent-pr-resolver-publish-evidence-handoff"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / "repo"
    result_path = workspace / manifest["terminalEvidencePath"]
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "executionRef": manifest["stepExecutionId"],
                "mergeAutomationDisposition": manifest[
                    "mergeAutomationDisposition"
                ],
                "status": "merged",
            }
        ),
        encoding="utf-8",
    )
    publish_payload = {
        "schemaVersion": "moonmind.publish.auto.v1",
        "mode": "auto",
        "owner": "agent",
        "skillId": "pr-resolver",
        "executionRef": manifest["stepExecutionId"],
        "status": "verified",
        "action": "merge",
        "repository": "MoonLadderStudios/MoonMind",
        "branch": "feature",
        "localHead": "abc1234",
        "remoteBranchHead": None,
        "remoteVerified": True,
        "pushed": False,
        "merged": True,
        "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/3616",
        "blockedReason": None,
        "verificationCommands": ["gh pr view 3616"],
    }
    publish_path = workspace / manifest["publishEvidencePath"]
    publish_path.parent.mkdir(parents=True)
    publish_path.write_text(json.dumps(publish_payload), encoding="utf-8")

    artifact_service = SimpleNamespace(
        create=AsyncMock(
            side_effect=[
                (SimpleNamespace(artifact_id="art-pr-terminal-evidence"), None),
                (
                    SimpleNamespace(artifact_id=expected["publishEvidenceRef"]),
                    None,
                ),
            ]
        ),
        write_complete=AsyncMock(
            side_effect=[
                SimpleNamespace(artifact_id="art-pr-terminal-evidence"),
                SimpleNamespace(artifact_id=expected["publishEvidenceRef"]),
            ]
        ),
    )
    activities = TemporalAgentRuntimeActivities(artifact_service=artifact_service)
    agent_result = await activities.agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(workspace),
            "terminalContract": {
                "contractId": "pr_resolver_terminal.v1",
                "relativePath": manifest["terminalEvidencePath"],
                "expectedSchemaVersion": "pr-resolver-result.v1",
                "executionRef": manifest["stepExecutionId"],
            },
            "result": {"summary": "PR was already merged."},
        }
    )
    assert agent_result.metadata["terminalContractSatisfied"] is expected[
        "terminalContractSatisfied"
    ]
    assert agent_result.metadata["publishEvidence"] == expected[
        "publishEvidenceRef"
    ]

    parent = MoonMindRunWorkflow()
    parent._owner_type = "user"
    parent._owner_id = "pr-resolver-publish-replay"
    mapped_result = parent._map_agent_run_result(agent_result)
    assert mapped_result["outputs"]["publishEvidence"] == expected[
        "publishEvidenceRef"
    ]

    async def read_publish_evidence(
        activity_type: str,
        payload: object,
        **_kwargs: object,
    ) -> bytes:
        assert activity_type == "artifact.read"
        assert getattr(payload, "artifact_ref", None) == expected[
            "publishEvidenceRef"
        ]
        return json.dumps(publish_payload).encode("utf-8")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: True,
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        read_publish_evidence,
    )
    await parent._record_publish_result_from_execution(
        parameters={"publishMode": "auto"},
        execution_result=mapped_result,
    )

    assert (parent._publish_status, parent._publish_reason) == (
        expected["publishStatus"],
        expected["publishReason"],
    )
    assert parent._publish_context["evidenceRef"] == expected[
        "publishEvidenceRef"
    ]


async def test_omnigent_canceled_host_rerun_waits_for_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:c702174c after the immediately preceding run was canceled."""

    replay_id = "omnigent-canceled-host-rerun-admission"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    binding = _oauth_binding().model_copy(
        update={"provider_profile_id": manifest["providerProfileId"]}
    )
    resolved_lease = _oauth_host_lease().model_copy(update={"status": "allocating"})
    hosts = SimpleNamespace(
        create_or_get_host_lease=AsyncMock(
            side_effect=[
                OmnigentOAuthHostError(
                    "prior canceled host is still active",
                    code=HOST_PROFILE_BUSY_ERROR_CODE,
                ),
                resolved_lease,
            ]
        )
    )
    emit = AsyncMock()
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=SimpleNamespace(),
        host_repository=hosts,
        host_runtime=SimpleNamespace(),
        run_store=SimpleNamespace(),
        execution_runner=AsyncMock(),
        artifact_gateway=object(),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.profile_bound_execution.HOST_PROFILE_BUSY_POLL_SECONDS",
        0.0,
    )

    admitted = await coordinator._create_host_lease_after_profile_idle(
        binding=binding,
        provider_lease=SimpleNamespace(lease_id="provider-lease-rerun"),
        workflow_id=manifest["incidentWorkflowId"],
        step_execution_id="step-rerun",
        idempotency_key="rerun-admission",
        emit=emit,
    )

    assert admitted == resolved_lease
    assert hosts.create_or_get_host_lease.await_count == 2
    assert emit.await_args.kwargs["code"] == expected["busyCode"]
    assert (
        emit.await_args.kwargs["remediation_action"]
        == expected["remediationAction"]
    )


async def test_omnigent_required_capability_authority_reaches_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:8a8955a6 at the Run-to-provider capability handoff."""

    replay_id = "omnigent-capability-authority-handoff"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id=manifest["incidentWorkflowId"],
            run_id=manifest["incidentRunId"],
            parent=None,
        ),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id == RUN_AGENT_REQUIRED_CAPABILITIES_PROPAGATION_PATCH
        ),
    )
    request = MoonMindRunWorkflow()._build_agent_execution_request(
        node_inputs={
            "runtime": {"mode": "omnigent"},
            "omnigent": {
                "agent": {"agentName": manifest["agentName"]},
                "session": {
                    "hostType": "external",
                    "hostId": "host-replay",
                    "workspace": "/workspaces/run",
                },
            },
        },
        node_id="node-1",
        tool_name="omnigent",
        workflow_parameters={
            "requiredCapabilities": manifest["requiredCapabilities"]
        },
    )

    assert request.parameters["requiredCapabilities"] == manifest[
        "requiredCapabilities"
    ]
    selection = build_omnigent_selection(request)
    payload = build_omnigent_session_create_payload(
        request=request,
        selection=selection,
        target=OmnigentResolvedTarget(
            agent_id="ag-codex-replay",
            source="agent_id",
        ),
    )
    assert payload["terminal_launch_args"] == expected["terminalLaunchArgs"]


async def test_omnigent_tool_bundle_uses_deployment_owned_named_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:d4a7b625 at the worker-to-system-Docker boundary."""

    replay_id = "omnigent-tool-bundle-deployment-boundary"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    monkeypatch.setenv("OMNIGENT_GH_VERSION", "2.76.2")
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        image=expected["probeImage"],
    )
    runtime._run = AsyncMock(return_value=(0, "gh version 2.76.2 (replay)\n", ""))

    await runtime._initialize_required_tools()

    command = runtime._run.await_args.args
    assert manifest["failureCode"] == "CODEX_OAUTH_LOGIN_STATUS_FAILED"
    assert command[:2] == ("docker", "run")
    assert "compose" not in command
    assert (
        f"{expected['toolVolume']}:/opt/moonmind-tools:ro"
        in command
    )
