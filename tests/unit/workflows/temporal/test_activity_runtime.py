"""Unit tests for Temporal activity-family runtime helpers."""

from __future__ import annotations

import asyncio
import json
import re
import stat
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import activity as temporal_activity
from temporalio import exceptions as temporal_exceptions

from api_service.db.models import Base
from moonmind.config.settings import (
    settings,
)
from moonmind.jules.runtime import JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    ManagedRunRecord,
)
from moonmind.schemas.managed_session_models import CodexManagedSessionLocator
from moonmind.schemas.jules_models import JulesTaskResponse
from moonmind.schemas.workload_models import (
    ValidatedWorkloadRequest,
    WorkloadResult,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_plan_contracts import SkillResult
from moonmind.workflows.skills.skill_registry import (
    create_registry_snapshot,
    parse_skill_registry,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    DEPLOYMENT_FLEET,
    INTEGRATIONS_FLEET,
    SANDBOX_FLEET,
    SANDBOX_TASK_QUEUE,
    TemporalActivityCatalog,
    TemporalActivityDefinition,
    TemporalActivityRetries,
    TemporalActivityTimeouts,
    TemporalWorkerFleet,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    SandboxCommandResult,
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
    TemporalCheckpointActivities,
    TemporalIntegrationActivities,
    TemporalManifestActivities,
    TemporalPlanActivities,
    TemporalProposalActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
    _default_registry_skill_payload,
    _default_skill_registry_payload,
    build_activity_bindings,
    build_activity_execution_context,
    build_activity_invocation_envelope,
    build_compact_activity_result,
    build_observability_summary,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactNotFoundError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactValidationError,
    build_artifact_ref,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore


@pytest.mark.asyncio
async def test_step_checkpoint_activity_constructs_complete_omnigent_identity() -> None:
    store = InMemoryArtifactStore()
    external = store.put_bytes(
        json.dumps(
            {
                "omnigentSessionId": "session-1",
                "firstMessage": {
                    "digest": "sha256:" + "1" * 64,
                    "responseIdentifiers": {"itemId": "message-1"},
                },
                "lastCommittedBridgeEventCursor": "event-9",
            },
            sort_keys=True,
        ).encode(),
        content_type="application/json",
    )
    external_ref = "artifact://omnigent-test/external-state"
    store._data[external_ref] = store.get_bytes(external.artifact_ref)
    manifest = store.put_bytes(b'{"capture":"complete"}', content_type="application/json")
    manifest_ref = "artifact://omnigent-test/capture-manifest"
    store._data[manifest_ref] = store.get_bytes(manifest.artifact_ref)
    activities = TemporalCheckpointActivities(artifact_store=store)

    result = await activities.step_checkpoint_create(
        {
            "identity": {
                "workflowId": "wf-3509",
                "runId": "run-3509",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            "boundary": "after_execution",
            "taskInputSnapshotRef": "artifact://task/input",
            "planRef": "artifact://plan/current",
            "workspace": {
                "kind": "worktree_archive",
                "baseCommit": "abc123",
                "headCommit": "def456",
                "archiveRef": "artifact://workspace/archive",
                "manifestRef": "artifact://workspace/manifest",
                "archiveDigest": "sha256:" + "2" * 64,
                "archiveBytes": 10,
                "createdAt": "2026-08-02T00:00:00Z",
            },
            "omnigentCheckpointCapture": {
                "providerProfileId": "codex",
                "credentialRef": "credential://provider-profile/codex/generation/3",
                "credentialGeneration": 3,
                "providerLeaseRef": "provider-lease-1",
                "hostBindingRef": "binding-1",
                "hostLeaseRef": "host-lease-1",
                "endpointRef": "endpoint-1",
                "omnigentHostId": "host-1",
                "bridgeSessionId": "bridge-1",
                "effectiveLaunchRef": "omnigent-launch:sha256:" + "3" * 64,
                "executionProfileRef": "profile://codex",
                "launchPolicyRef": "policy://default",
                "policyId": "omnigent-codex",
                "policyVersion": 1,
                "policyRef": "omnigent-codex@1",
                "policyDigest": "sha256:" + "4" * 64,
                "policySnapshotRef": "omnigent-policy:sha256:" + "5" * 64,
                "policyValidation": {"valid": True},
                "externalStateRef": external_ref,
                "captureManifestRef": manifest_ref,
                "terminalRef": "artifact://terminal/result",
                "diagnosticsRef": "artifact://diagnostics/result",
                "compiledExecutionIntentRef": "artifact://compiled/intent",
                "compiledExecutionIntentDigest": "sha256:" + "6" * 64,
                "idempotencyKey": "idem-3509",
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "clean-workspace",
                    "relativePath": "repo",
                },
                "instructionRefs": ["artifact://instructions/current"],
                "sourceBranch": "main",
                "publicationState": "none",
            },
            "createdAt": "2026-08-02T00:00:00Z",
            "idempotencyKey": "wf-3509:checkpoint:after_execution",
        }
    )

    checkpoint = json.loads(store.get_bytes(result["checkpointRef"]))
    identity = checkpoint["omnigentCheckpoint"]
    assert identity["schemaVersion"] == "v2"
    assert identity["externalStateRef"] == external_ref
    assert identity["externalStateDigest"].startswith("sha256:")
    assert identity["workspaceCheckpointRef"] == "artifact://workspace/archive"
    assert identity["compiledExecutionIntentRef"] == "artifact://compiled/intent"
    assert identity["compiledExecutionIntentDigest"] == "sha256:" + "6" * 64
    # Immutable policy-authority evidence stamped from the trusted launch flows
    # through to the persisted checkpoint so it stays cold-restore eligible.
    assert identity["policyId"] == "omnigent-codex"
    assert identity["policyVersion"] == 1
    assert identity["policyRef"] == "omnigent-codex@1"
    assert identity["policyDigest"] == "sha256:" + "4" * 64
    assert identity["policySnapshotRef"] == "omnigent-policy:sha256:" + "5" * 64
    assert identity["policyValidation"] == {"valid": True}
    assert identity["validation"] == {
        "valid": True,
        "liveReattachAvailable": True,
        "workspaceColdRestoreAvailable": True,
        "branchCreationAvailable": True,
        "reasons": [],
        "capacityBlocked": False,
        "readinessBlocked": False,
    }


@pytest.mark.asyncio
async def test_direct_codex_bridge_append_is_idempotent_and_preserves_provenance() -> None:
    appended: list[dict[str, Any]] = []

    class FakeStore:
        async def list_events(self, _bridge_session_id: str) -> list[Any]:
            return [
                SimpleNamespace(
                    event_type="session.started",
                    text_preview=None,
                    artifact_ref=None,
                    metadata_={
                        "moonmind": {
                            "directManagedSessionId": "direct-session-1",
                            "sessionEpoch": 2,
                            "turnId": None,
                        }
                    },
                )
            ]

        async def append_events(
            self, _bridge_session_id: str, events: list[dict[str, Any]]
        ) -> None:
            appended.extend(events)

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        correlationId="workflow-3367",
        idempotencyKey="issue-3367-idempotency",
        parameters={"instructions": "test"},
    )
    locator = CodexManagedSessionLocator(
        sessionId="direct-session-1",
        sessionEpoch=2,
        containerId="container-1",
        threadId="thread-2",
    )
    runtime = object.__new__(TemporalAgentRuntimeActivities)

    result = await runtime._append_direct_codex_bridge_events(
        store=FakeStore(),
        row=SimpleNamespace(bridge_session_id="bridge-1"),
        request=request,
        locator=locator,
        event_payloads=[
            {"type": "session.started", "status": "running"},
            {
                "type": "session.item.resource_published",
                "status": "running",
                "artifactRef": "artifact:summary",
            },
        ],
        compatibility_profile="moonmind.codex_direct_compat.v1",
    )

    assert result["eventCount"] == 1
    assert [event["eventType"] for event in appended] == [
        "session.item.resource_published"
    ]
    assert appended[0]["metadata"]["moonmind"] == {
        "workflowChatVisible": True,
        "source": "codex_direct_compat",
        "compatibilityProfile": "moonmind.codex_direct_compat.v1",
        "directManagedSessionId": "direct-session-1",
        "sessionEpoch": 2,
        "turnId": None,
        "sourceEventId": None,
        "sourceOutcome": None,
    }


@pytest.mark.asyncio
async def test_direct_codex_bridge_dedup_keeps_same_text_in_distinct_turns() -> None:
    appended: list[dict[str, Any]] = []

    class FakeStore:
        async def list_events(self, _bridge_session_id: str) -> list[Any]:
            return [
                SimpleNamespace(
                    event_type="response.output",
                    text_preview="same",
                    artifact_ref=None,
                    metadata_={"moonmind": {"directManagedSessionId": "session", "sessionEpoch": 1, "turnId": "turn-1"}},
                )
            ]

        async def append_events(self, _bridge_session_id: str, events: list[dict[str, Any]]) -> None:
            appended.extend(events)

    request = AgentExecutionRequest(agentKind="managed", agentId="codex", correlationId="wf", idempotencyKey="idem")
    locator = CodexManagedSessionLocator(sessionId="session", sessionEpoch=1, containerId="container", threadId="thread")
    runtime = object.__new__(TemporalAgentRuntimeActivities)
    result = await runtime._append_direct_codex_bridge_events(
        store=FakeStore(), row=SimpleNamespace(bridge_session_id="bridge"), request=request,
        locator=locator,
        event_payloads=[{"type": "response.output", "status": "running", "text": "same", "data": {"turnId": "turn-2"}}],
        compatibility_profile="moonmind.codex_direct_compat.v1",
    )

    assert result["eventCount"] == 1
    assert appended[0]["metadata"]["moonmind"]["turnId"] == "turn-2"


@pytest.mark.asyncio
async def test_direct_codex_bridge_retry_after_partial_commit_appends_only_tail() -> None:
    committed: list[Any] = []

    class FakeStore:
        async def list_events(self, _bridge_session_id: str) -> list[Any]:
            return list(committed)

        async def append_events(self, _bridge_session_id: str, events: list[dict[str, Any]]) -> None:
            committed.extend(
                SimpleNamespace(
                    event_type=event["eventType"],
                    text_preview=event.get("textPreview"),
                    artifact_ref=event.get("artifactRef"),
                    metadata_=event["metadata"],
                )
                for event in events
            )

    request = AgentExecutionRequest(agentKind="managed", agentId="codex", correlationId="wf", idempotencyKey="idem")
    locator = CodexManagedSessionLocator(sessionId="session", sessionEpoch=4, containerId="container", threadId="thread")
    runtime = object.__new__(TemporalAgentRuntimeActivities)
    events = [
        {"type": "response.output.delta", "status": "running", "text": "same", "data": {"turnId": "turn", "sourceEventId": "position-1"}},
        {"type": "response.output.delta", "status": "running", "text": "same", "data": {"turnId": "turn", "sourceEventId": "position-2"}},
        {"type": "response.completed", "status": "completed", "data": {"turnId": "turn", "sourceEventId": "terminal-1"}},
    ]

    first = await runtime._append_direct_codex_bridge_events(
        store=FakeStore(), row=SimpleNamespace(bridge_session_id="bridge"), request=request,
        locator=locator, event_payloads=events[:2], compatibility_profile="moonmind.codex_direct_compat.v1",
    )
    retry = await runtime._append_direct_codex_bridge_events(
        store=FakeStore(), row=SimpleNamespace(bridge_session_id="bridge"), request=request,
        locator=locator, event_payloads=events, compatibility_profile="moonmind.codex_direct_compat.v1",
    )

    assert first["eventCount"] == 2
    assert retry["eventCount"] == 1
    assert [event.metadata_["moonmind"]["sourceEventId"] for event in committed] == [
        "position-1", "position-2", "terminal-1"
    ]


def test_direct_codex_active_observations_use_canonical_classes_and_source_ids() -> None:
    locator = CodexManagedSessionLocator(
        sessionId="direct-session-3418",
        sessionEpoch=3,
        containerId="container-1",
        threadId="thread-3",
    )

    events = TemporalAgentRuntimeActivities._direct_codex_active_event_payloads(
        observations=[
            {
                "kind": "assistant_message_delta",
                "turnId": "turn-7",
                "text": "bounded delta",
                "metadata": {"sourceEventId": "codex-event-1"},
            },
            {
                "kind": "tool_call_started",
                "turnId": "turn-7",
                "metadata": {
                    "sourceEventId": "codex-event-2",
                    "toolName": "shell",
                },
            },
        ],
        source_metadata={
            "source": "codex_direct_compat",
            "directManagedSessionId": "direct-session-3418",
            "sessionEpoch": 3,
        },
        locator=locator,
        turn_id="turn-7",
    )

    assert [event["type"] for event in events] == [
        "response.output.delta",
        "session.item.tool.started",
    ]
    assert [event["eventId"] for event in events] == [
        "codex-event-1",
        "codex-event-2",
    ]
    assert all(
        event["data"]["directManagedSessionId"] == "direct-session-3418"
        for event in events
    )


def test_direct_codex_active_intervention_requires_authoritative_evidence() -> None:
    locator = CodexManagedSessionLocator(
        sessionId="direct-session-3418",
        sessionEpoch=3,
        containerId="container-1",
        threadId="thread-3",
    )

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="requires authoritative intervention evidence",
    ):
        TemporalAgentRuntimeActivities._direct_codex_active_event_payloads(
            observations=[{"kind": "approval_requested", "metadata": {}}],
            source_metadata={"source": "codex_direct_compat"},
            locator=locator,
            turn_id="turn-7",
        )


def test_direct_codex_active_observations_cover_lifecycle_and_intervention_outcomes(
) -> None:
    locator = CodexManagedSessionLocator(
        sessionId="direct-session-3418",
        sessionEpoch=4,
        containerId="container-1",
        threadId="thread-4",
    )
    authority = {
        "actorId": "operator-1",
        "idempotencyKey": "control-1",
        "expectedSessionId": locator.session_id,
        "expectedSessionEpoch": locator.session_epoch,
        "expectedTurnId": "turn-8",
        "outcome": "delivery_unknown",
        "auditRef": "artifact://audit/control-1",
    }

    events = TemporalAgentRuntimeActivities._direct_codex_active_event_payloads(
        observations=[
            {"kind": "turn_completed", "metadata": {"sourceEventId": "done-1"}},
            {"kind": "intervention_delivery_unknown", "metadata": authority},
            {"kind": "turn_canceled", "metadata": {"sourceEventId": "cancel-1"}},
            {"kind": "turn_timed_out", "metadata": {"sourceEventId": "timeout-1"}},
            {
                "kind": "continuity_published",
                "metadata": {"artifactRef": "artifact://continuity/1"},
            },
            {
                "kind": "cleanup_failed",
                "metadata": {
                    "sourceEventId": "cleanup-1",
                    "failureReason": "sidecar unavailable",
                },
            },
        ],
        source_metadata={"source": "codex_direct_compat"},
        locator=locator,
        turn_id="turn-8",
    )

    assert [event["type"] for event in events] == [
        "session.item.turn.completed",
        "session.item.control.delivery_unknown",
        "session.item.terminal.canceled",
        "session.item.terminal.timed_out",
        "session.item.resource_published",
        "session.item.cleanup.failed",
    ]
    assert events[1]["artifactRef"] == "artifact://audit/control-1"
    assert events[4]["artifactRef"] == "artifact://continuity/1"
    assert events[1]["metadata"]["actorId"] == "operator-1"


def test_direct_codex_active_intervention_rejects_wrong_turn_authority() -> None:
    locator = CodexManagedSessionLocator(
        sessionId="direct-session-3418",
        sessionEpoch=4,
        containerId="container-1",
        threadId="thread-4",
    )

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="does not match the active turn",
    ):
        TemporalAgentRuntimeActivities._direct_codex_active_event_payloads(
            observations=[
                {
                    "kind": "intervention_completed",
                    "metadata": {
                        "actorId": "operator-1",
                        "idempotencyKey": "control-1",
                        "expectedSessionId": locator.session_id,
                        "expectedSessionEpoch": locator.session_epoch,
                        "expectedTurnId": "stale-turn",
                        "outcome": "completed",
                        "auditRef": "artifact://audit/control-1",
                    },
                },
            ],
            source_metadata={"source": "codex_direct_compat"},
            locator=locator,
            turn_id="turn-8",
        )


def test_direct_codex_dual_write_compares_independently_persisted_streams() -> None:
    def event(kind: str, *, status: str = "running", artifact: str | None = None, text: str | None = None) -> Any:
        return SimpleNamespace(
            event_type=kind,
            normalized_status=status,
            artifact_ref=artifact,
            text_preview=text,
        )

    reference = [
        event("response.output", text="answer"),
        event("session.item.control.completed", artifact="artifact://control/1"),
        event("session.item.resource_published", artifact="artifact://resource/1"),
        event("response.completed", status="completed"),
    ]
    direct = [
        event("session.item.control.completed", artifact="artifact://control/wrong"),
        event("response.output", text="answer"),
        event("response.output", text="answer"),
        event("response.completed", status="failed"),
    ]

    comparison = TemporalAgentRuntimeActivities._compare_bridge_event_streams(
        direct_events=direct,
        comparison_events=reference,
    )

    assert comparison["comparisonAvailable"] is True
    assert comparison["matched"] is False
    assert comparison["missingEventClasses"] == ["session.item.resource_published"]
    assert comparison["unexpectedEventClasses"] == []
    assert comparison["droppedEventCount"] == 1
    assert comparison["duplicateEventCount"] == 1
    assert comparison["semanticMismatchCount"] >= 1
    reordered = TemporalAgentRuntimeActivities._compare_bridge_event_streams(
        direct_events=list(reversed(reference)),
        comparison_events=reference,
    )
    assert reordered["reordered"] is True


@pytest.mark.asyncio
async def test_post_merge_github_completion_applies_done_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.temporal import story_output_tools

    captured: dict[str, Any] = {}

    async def fake_update_github_issue_status(inputs, _context=None):
        captured.update(inputs)
        return SimpleNamespace(
            status="COMPLETED",
            outputs={
                "confirmedState": "closed",
                "confirmedLabels": ["status: done"],
                "appliedActions": ["patch_issue"],
            },
        )

    monkeypatch.setattr(
        story_output_tools,
        "update_github_issue_status",
        fake_update_github_issue_status,
    )

    result = await TemporalIntegrationActivities.merge_automation_complete_post_merge_github(
        object(),
        {
            "postMergeGithub": {
                "enabled": True,
                "required": True,
                "repository": "MoonLadderStudios/MoonMind",
                "issueNumber": 3143,
            }
        },
    )

    assert captured == {
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": 3143,
        "mode": "done",
    }
    assert result["status"] == "succeeded"
    assert result["confirmedLabels"] == ["status: done"]
from moonmind.workflows.temporal.report_artifacts import validate_report_bundle_result
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workloads.registry import RunnerProfileRegistry


pytestmark = [pytest.mark.asyncio]


async def test_prepare_managed_codex_turn_adds_moonspec_verify_artifact_hint() -> None:
    prepared = TemporalAgentRuntimeActivities._prepare_managed_codex_turn_text(
        "Run moonspec-verify.",
        parameters={
            "metadata": {"moonmind": {"selectedSkill": "moonspec-verify"}},
            "verify_artifact_path": "var/artifacts/moonspec-verify/verify-final.json",
        },
    )

    assert "MoonSpec verification output contract:" in prepared
    assert "var/artifacts/moonspec-verify/verify-final.json" in prepared
    assert "complete structured verifier JSON" in prepared
    # The hint must enumerate the enforced vocabularies so the verifier agent
    # cannot drift into non-canonical values the gate would fail closed on.
    assert '"FULLY_IMPLEMENTED"' in prepared
    assert '"BLOCKED"' in prepared
    assert '"advance"' in prepared
    assert '"reattempt_current_step"' in prepared
    assert '`FULLY_IMPLEMENTED`, set `recommendedNextAction` to "advance"' in prepared
    assert "workflow runtime owns routing" in prepared
    assert "read-only verifier must not ask its own rerun" in prepared
    assert 'Use "reattempt_current_step" only when rerunning this verifier' in prepared
    assert "raw diagnostic" in prepared
    assert "map-entry" in prepared
    assert "missing map assets" in prepared
    assert "non-blocking limitations" in prepared


async def test_prepare_managed_codex_turn_appends_vocab_when_path_already_present() -> None:
    path = "var/artifacts/moonspec-verify/verify-final.json"
    prepared = TemporalAgentRuntimeActivities._prepare_managed_codex_turn_text(
        f"Run moonspec-verify and write JSON to `{path}`.",
        parameters={
            "metadata": {"moonmind": {"selectedSkill": "moonspec-verify"}},
            "verify_artifact_path": path,
        },
    )

    assert "MoonSpec verification output contract:" in prepared
    assert prepared.count("complete structured verifier JSON") == 0
    assert '"FULLY_IMPLEMENTED"' in prepared
    assert '"advance"' in prepared
    assert "workflow-specific destination" in prepared
    assert "external-service checks as advisory" in prepared


async def test_codex_skill_payload_rejects_auto_publish_mode() -> None:
    from moonmind.agents.codex_worker.handlers import (
        CodexSkillPayload,
        CodexWorkerHandlerError,
    )

    with pytest.raises(CodexWorkerHandlerError, match="codex_skill publishMode"):
        CodexSkillPayload.from_payload(
            {
                "skillId": "fix-ci",
                "inputs": {
                    "repo": "MoonLadderStudios/MoonMind",
                    "publishMode": "auto",
                },
            }
        )


async def test_checkpoint_activity_runtime_bindings_are_registered() -> None:
    catalog = TemporalActivityCatalog(
        activities=(
            TemporalActivityDefinition(
                "step_checkpoint.create",
                "step_checkpoint",
                "artifacts",
                ARTIFACTS_TASK_QUEUE,
                ARTIFACTS_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "step_checkpoint.validate",
                "step_checkpoint",
                "artifacts",
                ARTIFACTS_TASK_QUEUE,
                ARTIFACTS_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "workspace.capture_checkpoint",
                "workspace",
                "sandbox",
                SANDBOX_TASK_QUEUE,
                SANDBOX_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "workspace.apply_policy",
                "workspace",
                "sandbox",
                SANDBOX_TASK_QUEUE,
                SANDBOX_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "workspace.classify_git_effect",
                "workspace",
                "sandbox",
                SANDBOX_TASK_QUEUE,
                SANDBOX_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
        ),
        fleets=(
            TemporalWorkerFleet(
                ARTIFACTS_FLEET,
                (ARTIFACTS_TASK_QUEUE,),
                ("artifacts",),
                ("artifact_store",),
                "test",
                ("step_checkpoint.create", "step_checkpoint.validate"),
            ),
            TemporalWorkerFleet(
                SANDBOX_FLEET,
                (SANDBOX_TASK_QUEUE,),
                ("sandbox",),
                ("workspace",),
                "test",
                (
                    "workspace.capture_checkpoint",
                    "workspace.apply_policy",
                    "workspace.classify_git_effect",
                ),
            ),
        ),
    )
    class _ArtifactImplementation:
        async def __getattr__(self, _name: str):
            raise AttributeError(f"unexpected dynamic lookup: {_name}")

        async def artifact_create(self):
            pass

        async def artifact_write_complete(self):
            pass

        async def artifact_publish_report_bundle(self):
            pass

        async def artifact_read(self):
            pass

        async def execution_dependency_status_snapshot(self):
            pass

        async def execution_record_terminal_state(self):
            pass

        async def artifact_list_for_execution(self):
            pass

        async def artifact_compute_preview(self):
            pass

        async def artifact_link(self):
            pass

        async def artifact_pin(self):
            pass

        async def artifact_unpin(self):
            pass

        async def artifact_lifecycle_sweep(self):
            pass

        async def step_checkpoint_create(self):
            pass

        async def step_checkpoint_validate(self):
            pass

    artifacts = _ArtifactImplementation()
    sandbox = TemporalSandboxActivities(
        artifact_store=InMemoryArtifactStore(),
        workspace_root=Path("/tmp/moonmind-test-workspaces"),
    )

    bindings = {
        binding.activity_type: binding
        for binding in build_activity_bindings(
            catalog,
            artifact_activities=artifacts,
            sandbox_activities=sandbox,
            fleets=(ARTIFACTS_FLEET, SANDBOX_FLEET),
        )
    }

    assert bindings["step_checkpoint.create"].fleet == ARTIFACTS_FLEET
    assert bindings["step_checkpoint.validate"].fleet == ARTIFACTS_FLEET
    assert bindings["workspace.capture_checkpoint"].fleet == SANDBOX_FLEET
    assert bindings["workspace.apply_policy"].fleet == SANDBOX_FLEET
    assert bindings["workspace.classify_git_effect"].fleet == SANDBOX_FLEET

@asynccontextmanager
async def temporal_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_activity_runtime.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield session_maker
    finally:
        await engine.dispose()

def _registry_payload() -> dict:
    return {
        "skills": [
            {
                "name": "repo.run_tests",
                "description": "Run tests",
                "inputs": {
                    "schema": {
                        "type": "object",
                        "required": ["repo_ref"],
                        "properties": {"repo_ref": {"type": "string"}},
                    }
                },
                "outputs": {
                    "schema": {
                        "type": "object",
                        "required": ["ok"],
                        "properties": {"ok": {"type": "boolean"}},
                    }
                },
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 30,
                        "schedule_to_close_seconds": 120,
                    },
                    "retries": {"max_attempts": 2},
                },
            }
        ]
    }

def _plan_payload(*, registry_artifact_id: str, registry_digest: str) -> dict:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Fix tests",
            "created_at": "2026-03-05T00:00:00Z",
            "registry_snapshot": {
                "digest": registry_digest,
                "artifact_ref": registry_artifact_id,
            },
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": [
            {
                "id": "n1",
                "skill": {"name": "repo.run_tests"},
                "inputs": {"repo_ref": "git:org/repo#main"},
            }
        ],
        "edges": [],
    }

class _FakeJulesClient:
    def __init__(
        self,
        *,
        create_status: str = "pending",
        get_status: str = "completed",
        get_pull_request_url: str | None = None,
    ) -> None:
        self.created: list[object] = []
        self.lookups: list[object] = []
        self.closed = False
        self._create_status = create_status
        self._get_status = get_status
        self._get_pull_request_url = get_pull_request_url

    async def create_task(self, request):
        self.created.append(request)
        return JulesTaskResponse(
            task_id="task-001",
            status=self._create_status,
            url="https://jules.test/task-001",
        )

    async def get_task(self, request):
        self.lookups.append(request)
        outputs = []
        if self._get_pull_request_url is not None:
            outputs = [{"pullRequest": {"url": self._get_pull_request_url}}]
        return JulesTaskResponse(
            task_id=request.task_id,
            status=self._get_status,
            url="https://jules.test/task-001",
            outputs=outputs,
        )

    async def aclose(self) -> None:
        self.closed = True

async def test_artifact_activity_create_returns_ref_and_upload_descriptor(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            artifact_ref, upload = await activities.artifact_create(
                principal="user-1",
                content_type="text/plain",
            )

            assert artifact_ref.artifact_id.startswith("art_")
            assert upload.mode == "single_put"

async def test_artifact_activity_create_maps_legacy_name_to_metadata(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            artifact_ref, _upload = await activities.artifact_create(
                principal="user-1",
                content_type="application/json",
                name="reports/run_summary.json",
                metadata_json={"artifact_kind": "summary"},
            )
            artifact, _links, _pinned, _policy = await service.get_metadata(
                artifact_id=artifact_ref.artifact_id,
                principal="user-1",
            )

            assert artifact.metadata_json["name"] == "reports/run_summary.json"
            assert artifact.metadata_json["artifact_kind"] == "summary"

async def test_artifact_create_binding_accepts_legacy_name_payload(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    artifact_activities=activities,
                    manifest_activities=TemporalManifestActivities(
                        artifact_service=service,
                    ),
                    proposal_activities=TemporalProposalActivities(artifact_service=service),
                    agent_skills_activities=AgentSkillsActivities(),
                    fleets=(ARTIFACTS_FLEET,),
                )
            }

            artifact_ref, _upload = await bindings["artifact.create"].handler(
                {
                    "principal": "user-1",
                    "content_type": "application/json",
                    "name": "reports/run_summary.json",
                    "metadata_json": {"artifact_kind": "summary"},
                }
            )
            artifact, _links, _pinned, _policy = await service.get_metadata(
                artifact_id=artifact_ref.artifact_id,
                principal="user-1",
            )

            assert artifact.metadata_json["name"] == "reports/run_summary.json"
            assert artifact.metadata_json["artifact_kind"] == "summary"

async def test_artifact_publish_report_bundle_binding_routes_to_artifacts_queue(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    artifact_activities=activities,
                    manifest_activities=TemporalManifestActivities(
                        artifact_service=service,
                    ),
                    proposal_activities=TemporalProposalActivities(
                        artifact_service=service
                    ),
                    agent_skills_activities=AgentSkillsActivities(),
                    fleets=(ARTIFACTS_FLEET,),
                )
            }

            binding = bindings["artifact.publish_report_bundle"]
            assert (
                catalog.resolve_activity(
                    "artifact.publish_report_bundle"
                ).retries.max_attempts
                == 1
            )
            result = await binding.handler(
                {
                    "principal": "workflow-producer",
                    "namespace": "moonmind",
                    "workflow_id": "wf-report",
                    "run_id": "run-report",
                    "report_type": "unit_test_report",
                    "report_scope": "final",
                    "primary": {
                        "payload": "# Final report",
                        "content_type": "text/markdown",
                    },
                }
            )

            assert binding.task_queue == "mm.activity.artifacts"
            assert result["report_bundle_v"] == 1
            assert result["primary_report_ref"]["artifact_id"].startswith("art_")

async def test_plan_validate_accepts_temporal_registry_artifact_ids(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            registry_ref = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            registry_artifact, _upload = registry_ref
            registry_completed = await service.write_complete(
                artifact_id=registry_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(_registry_payload()) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            planner = TemporalPlanActivities(artifact_service=service)
            registry_payload = _registry_payload()

            snapshot = create_registry_snapshot(
                skills=parse_skill_registry(registry_payload),
                artifact_store=InMemoryArtifactStore(),
            )
            plan_payload = _plan_payload(
                registry_artifact_id=registry_completed.artifact_id,
                registry_digest=snapshot.digest,
            )
            plan_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=plan_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(plan_payload) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            validated_ref = await planner.plan_validate(
                plan_ref=plan_artifact.artifact_id,
                registry_snapshot_ref=registry_completed.artifact_id,
                principal="user-1",
            )
            _artifact, payload = await service.read(
                artifact_id=validated_ref.artifact_id,
                principal="user-1",
            )

            assert b'"plan_version": "1.0"' in payload

async def test_plan_generate_rejects_placeholder_registry_refs(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            def _placeholder_planner(_inputs, _parameters, _snapshot):
                return {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Bad placeholder plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:dummy",
                            "artifact_ref": "art:sha256:dummy",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "n1",
                            "skill": {"name": "code"},
                            "inputs": {
                                "instructions": "Do work",
                                "runtime": {"mode": "codex"},
                            },
                        }
                    ],
                    "edges": [],
                }

            planner = TemporalPlanActivities(
                artifact_service=service,
                planner=_placeholder_planner,
            )
            with pytest.raises(
                TemporalActivityRuntimeError,
                match="placeholder ref\\(s\\) matching '\\*:sha256:dummy'",
            ):
                await planner.plan_generate(
                    principal="user-1",
                    parameters={
                        "repository": "moonladder/moonmind",
                        "task": {
                            "tool": {"type": "skill", "name": "code"},
                        },
                    },
                )

async def test_plan_generate_legacy_payload_replay(tmp_path: Path):
    """
    Simulates a workflow replay where an older dict-based payload arrives
    at the plan.generate activity, ensuring dual-read parses to PlanGenerateInput.
    """
    from unittest.mock import AsyncMock, patch
    from moonmind.schemas.temporal_activity_models import ArtifactRefModel
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            def _dummy_planner(_inputs, _parameters, _snapshot):
                return {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Dual-Read Replay Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "n1",
                            "skill": {"name": "dummy"},
                            "inputs": {"arg": "val"}
                        }
                    ],
                    "edges": [],
                }
            
            # Setup mock registry wrapper
            with patch("moonmind.workflows.temporal.activity_runtime._temporal_snapshot_from_payload") as mock_snapshot:
                from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot
                mock_snapshot.return_value = ToolRegistrySnapshot(
                    digest="reg:sha256:test1234",
                    artifact_ref="art:sha256:test1234",
                    skills=()
                )

                planner = TemporalPlanActivities(
                    artifact_service=service,
                    planner=_dummy_planner,
                )
                
                # The exact dict layout historically emitted by workflow.execute_activity
                legacy_payload = {
                    "principal": "user-replay",
                    "inputs_ref": None,
                    "parameters": {"strategy": "default"},
                    "idempotency_key": "replay-test"
                }
                
                # Should deserialize correctly matching `PlanGenerateInput` fallback
                result = await planner.plan_generate(legacy_payload) # type: ignore
                assert result.plan_ref.artifact_ref_v == 1

async def test_default_skill_registry_payload_excludes_auto_when_explicit_skill_selected():
    """When an explicit skill is selected, 'auto' (the placeholder) must not appear in the registry."""
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                }
            }
        }
    )
    skills = payload.get("skills")
    assert isinstance(skills, list)
    keyset = {
        str(item.get("name"))
        for item in skills
        if isinstance(item, dict)
    }
    # 'auto' is a placeholder and must not be in the registry when explicit skills are present.
    assert "auto" not in keyset
    assert "pr-resolver" in keyset
    assert all("version" not in item for item in skills if isinstance(item, dict))

async def test_default_skill_registry_payload_auto_placeholder_filtered():
    """When 'auto' is the only (placeholder) skill, it must not appear in the registry."""
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "skill": {
                    "name": "auto",
                }
            }
        }
    )
    skills = payload.get("skills")
    assert isinstance(skills, list)
    keyset = {
        str(item.get("name"))
        for item in skills
        if isinstance(item, dict)
    }
    # 'auto' is a placeholder and must not appear in the registry at all.
    assert "auto" not in keyset
    assert all("version" not in item for item in skills if isinstance(item, dict))

@pytest.mark.parametrize(
    "skill_name",
    ["jira-issue-creator", "jira-issue-updater", "jira-pr-verify", "jira-verify"],
)
async def test_default_skill_registry_payload_excludes_agent_only_jira_skill(
    skill_name: str,
):
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "tool": {
                    "type": "skill",
                    "name": skill_name,
                }
            }
        }
    )
    skills = payload.get("skills")
    assert skills == []

async def test_default_skill_registry_payload_uses_generic_container_job_definition():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "container.run_job",
                        }
                    },
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    tools = {item["name"]: item for item in skills}
    assert set(tools) == {"container.run_job"}
    assert tools["container.run_job"]["requirements"]["capabilities"] == [
        "docker_workload"
    ]
    assert (
        tools["container.run_job"]["executor"]["activity_type"]
        == "mm.tool.execute"
    )

async def test_default_skill_registry_payload_includes_input_sourced_tool_steps():
    payload = _default_skill_registry_payload(
        parameters={"workflow": {"tool": {"name": "auto"}}},
        inputs={
            "workflow": {
                "steps": [
                    {
                        "type": "tool",
                        "tool": {
                            "id": "jira.get_issue",
                            "inputs": {"issueKey": "MM-579"},
                        },
                    }
                ]
            }
        },
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert [item["name"] for item in skills] == ["jira.get_issue"]
    assert all("version" not in item for item in skills if isinstance(item, dict))


async def test_default_skill_registry_payload_uses_curated_deployment_tool_definition():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": DEPLOYMENT_UPDATE_TOOL_NAME,
                        }
                    }
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert len(skills) == 1
    definition = skills[0]

    assert definition["name"] == DEPLOYMENT_UPDATE_TOOL_NAME
    assert "version" not in definition
    assert definition["type"] == "skill"
    assert definition["executor"] == {
        "activity_type": "mm.tool.execute",
        "selector": {"mode": "by_capability"},
    }
    assert definition["requirements"]["capabilities"] == [
        "deployment_control",
        "docker_admin",
    ]
    assert definition["security"]["allowed_roles"] == ["admin"]
    assert definition["security"]["opsRuntime"]["kind"] == "MoonMindOpsRuntime"
    assert definition["security"]["opsRuntime"]["exposedToManagedAgents"] is False

    input_schema = definition["inputs"]["schema"]
    assert input_schema["required"] == ["stack", "image"]
    assert input_schema["additionalProperties"] is False
    assert input_schema["properties"]["image"]["additionalProperties"] is False

    parsed = parse_skill_registry(payload)
    assert [tool.name for tool in parsed] == [
        DEPLOYMENT_UPDATE_TOOL_NAME
    ]
    assert parsed[0].required_capabilities == (
        "deployment_control",
        "docker_admin",
    )
    route = build_default_activity_catalog().resolve_skill(parsed[0])
    assert route.fleet == DEPLOYMENT_FLEET
    assert route.task_queue == "mm.activity.deployment"

async def test_default_skill_registry_payload_routes_jira_preset_brief_to_integrations():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "jira.load_preset_brief",
                        }
                    }
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert len(skills) == 1
    definition = skills[0]

    assert definition["name"] == "jira.load_preset_brief"
    assert definition["requirements"]["capabilities"] == ["integration:jira"]
    assert definition["policies"]["timeouts"] == {
        "start_to_close_seconds": 60,
        "schedule_to_close_seconds": 120,
    }

    parsed = parse_skill_registry(payload)
    assert parsed[0].required_capabilities == ("integration:jira",)
    route = build_default_activity_catalog().resolve_skill(parsed[0])
    assert route.fleet == INTEGRATIONS_FLEET
    assert route.task_queue == "mm.activity.integrations"

async def test_default_skill_registry_payload_routes_jira_status_update_to_integrations():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "jira.update_issue_status",
                        }
                    }
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert len(skills) == 1
    definition = skills[0]

    assert definition["name"] == "jira.update_issue_status"
    assert definition["requirements"]["capabilities"] == ["integration:jira"]
    assert definition["policies"]["timeouts"] == {
        "start_to_close_seconds": 60,
        "schedule_to_close_seconds": 120,
    }

    parsed = parse_skill_registry(payload)
    assert parsed[0].required_capabilities == ("integration:jira",)
    route = build_default_activity_catalog().resolve_skill(parsed[0])
    assert route.fleet == INTEGRATIONS_FLEET
    assert route.task_queue == "mm.activity.integrations"


async def test_managed_runtime_cleanup_binding_is_registered_on_agent_runtime_fleet():
    bindings = build_activity_bindings(
        build_default_activity_catalog(),
        agent_runtime_activities=TemporalAgentRuntimeActivities(),
        agent_skills_activities=AgentSkillsActivities(),
        fleets=[AGENT_RUNTIME_FLEET],
    )

    binding = next(
        item
        for item in bindings
        if item.activity_type == "agent_runtime.cleanup_managed_runtime_files"
    )

    assert binding.fleet == AGENT_RUNTIME_FLEET
    assert binding.task_queue == "mm.activity.agent_runtime"
    assert binding.handler.__temporal_activity_definition.name == (
        "agent_runtime.cleanup_managed_runtime_files"
    )
    route = build_default_activity_catalog().resolve_activity(
        "agent_runtime.cleanup_managed_runtime_files"
    )
    assert route.timeouts.start_to_close_seconds == 1800























































































async def test_plan_generate_accepts_auto_placeholder_without_registry_entries(
    tmp_path: Path,
):
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            planner = TemporalPlanActivities(
                artifact_service=service,
                planner=_build_runtime_planner(),
            )

            result = await planner.plan_generate(
                principal="user-1",
                parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "claude",
                    "model": "MiniMax-M2.7",
                    "instructions": "Move the pagination control next to next/prev buttons.",
                    "task": {
                        "tool": {"type": "skill", "name": "auto"},
                        "skill": {"name": "auto"},
                        "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                        "instructions": "Move the pagination control next to next/prev buttons.",
                    },
                },
            )

            _artifact, payload = await service.read(
                artifact_id=result.plan_ref.artifact_id,
                principal="user-1",
            )
            plan_payload = json.loads(payload.decode("utf-8"))
            registry_ref = plan_payload["metadata"]["registry_snapshot"]["artifact_ref"]

            _registry_artifact, registry_payload_raw = await service.read(
                artifact_id=registry_ref,
                principal="user-1",
            )
            registry_payload = json.loads(registry_payload_raw.decode("utf-8"))

            assert plan_payload["nodes"][0]["tool"]["type"] == "agent_runtime"
            assert registry_payload == {"skills": []}

async def test_plan_generate_fallback_registry_includes_input_artifact_tool_steps(
    tmp_path: Path,
):
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            inputs_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=inputs_artifact.artifact_id,
                principal="user-1",
                payload=(
                    json.dumps(
                        {
                            "workflow": {
                                "instructions": "Fetch the Jira issue.",
                                "runtime": {"mode": "codex_cli"},
                                "steps": [
                                    {
                                        "id": "fetch-issue",
                                        "type": "tool",
                                        "instructions": "Fetch MM-579.",
                                        "tool": {
                                            "id": "jira.get_issue",
                                            "inputs": {"issueKey": "MM-579"},
                                        },
                                    }
                                ],
                            }
                        }
                    )
                    + "\n"
                ).encode("utf-8"),
                content_type="application/json",
            )

            planner = TemporalPlanActivities(
                artifact_service=service,
                planner=_build_runtime_planner(),
            )
            result = await planner.plan_generate(
                principal="user-1",
                inputs_ref=inputs_artifact.artifact_id,
                parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                    "workflow": {
                        "tool": {"type": "skill", "name": "auto"}
                    },
                },
            )

            _artifact, plan_payload_raw = await service.read(
                artifact_id=result.plan_ref.artifact_id,
                principal="user-1",
            )
            plan_payload = json.loads(plan_payload_raw.decode("utf-8"))
            registry_ref = plan_payload["metadata"]["registry_snapshot"]["artifact_ref"]
            _registry_artifact, registry_payload_raw = await service.read(
                artifact_id=registry_ref,
                principal="user-1",
            )
            registry_payload = json.loads(registry_payload_raw.decode("utf-8"))

            assert plan_payload["nodes"][0]["tool"] == {
                "type": "skill",
                "name": "jira.get_issue",
            }
            assert [item["name"] for item in registry_payload["skills"]] == [
                "jira.get_issue"
            ]


async def test_default_registry_payload_uses_extended_timeouts_for_pr_resolver():
    payload = _default_registry_skill_payload(name="pr-resolver")
    policies = payload.get("policies", {})
    timeouts = policies.get("timeouts", {})
    assert timeouts.get("start_to_close_seconds") == 7200
    assert timeouts.get("schedule_to_close_seconds") == 7500

async def test_skill_execute_loads_registry_snapshot_from_temporal_artifact(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            registry_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=registry_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(_registry_payload()) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            dispatcher = SkillActivityDispatcher()
            dispatcher.register_skill(
                skill_name="repo.run_tests",
                handler=lambda inputs, _context: SkillResult(
                    status="COMPLETED",
                    outputs={"ok": inputs["repo_ref"].endswith("#main")},
                    progress={"percent": 100},
                ),
            )

            activities = TemporalSkillActivities(dispatcher=dispatcher)
            result = await activities.mm_skill_execute(
                invocation_payload={
                    "id": "n1",
                    "skill": {"name": "repo.run_tests"},
                    "inputs": {"repo_ref": "git:org/repo#main"},
                },
                registry_snapshot_ref=registry_artifact.artifact_id,
                artifact_service=service,
                principal="user-1",
            )

            assert result.status == "COMPLETED"
            assert result.outputs["ok"] is True

async def test_skill_execute_uses_bound_artifact_service_when_not_passed(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            registry_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=registry_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(_registry_payload()) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            dispatcher = SkillActivityDispatcher()
            dispatcher.register_skill(
                skill_name="repo.run_tests",
                handler=lambda inputs, _context: SkillResult(
                    status="COMPLETED",
                    outputs={"ok": inputs["repo_ref"].endswith("#main")},
                ),
            )

            activities = TemporalSkillActivities(
                dispatcher=dispatcher,
                artifact_service=service,
            )
            result = await activities.mm_skill_execute(
                invocation_payload={
                    "id": "n1",
                    "skill": {"name": "repo.run_tests"},
                    "inputs": {"repo_ref": "git:org/repo#main"},
                },
                registry_snapshot_ref=registry_artifact.artifact_id,
                principal="user-1",
            )

            assert result.status == "COMPLETED"
            assert result.outputs["ok"] is True

async def test_artifact_read_invalid_ref_failures_surface_cleanly(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            with pytest.raises(
                TemporalArtifactValidationError,
                match="artifact_id is required",
            ):
                await activities.artifact_read(
                    {"artifact_ref": {"artifactId": "  "}, "principal": "user-1"}
                )

            with pytest.raises(TemporalArtifactNotFoundError):
                await activities.artifact_read(
                    {"artifact_ref": "art:sha256:dummy", "principal": "user-1"}
                )

async def test_sandbox_run_command_writes_diagnostics_artifact(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            workspace_root = tmp_path / "workspaces"
            workspace = workspace_root / "temporal_sandbox" / "unit-run-command"
            workspace.mkdir(parents=True)
            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=workspace_root,
            )

            result = await activities.sandbox_run_command(
                workspace_ref=workspace,
                cmd="printf 'hello sandbox'",
                principal="user-1",
                execution_ref=ExecutionRef(
                    namespace="moonmind",
                    workflow_id="wf-1",
                    run_id="run-1",
                    link_type="output.logs",
                ),
            )
            assert result.exit_code == 0
            assert result.diagnostics_ref is not None

            _artifact, payload = await service.read(
                artifact_id=result.diagnostics_ref.artifact_id,
                principal="user-1",
            )
            assert b"hello sandbox" in payload

async def test_sandbox_rejects_workspace_outside_sandbox_root(tmp_path: Path):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    outside_workspace = tmp_path / "outside"
    outside_workspace.mkdir()

    with pytest.raises(TemporalActivityRuntimeError, match="escapes sandbox root"):
        await activities.sandbox_run_command(
            workspace_ref=outside_workspace,
            cmd=("pwd",),
        )


async def test_checkpoint_capture_rejects_managed_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    managed_workspace_root = tmp_path / "agent_jobs"
    outside_workspace = managed_workspace_root / "managed-run-1" / "repo"
    outside_workspace.mkdir(parents=True)
    activities = TemporalSandboxActivities(workspace_root=workspace_root)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="escapes sandbox root",
    ):
        await activities.workspace_capture_checkpoint(
            {
                "identity": {
                    "workflowId": "workflow-1",
                    "runId": "run-1",
                    "logicalStepId": "implement",
                    "executionOrdinal": 1,
                },
                "boundary": "after_execution",
                "kind": "git_patch",
                "workspacePath": str(outside_workspace),
                "artifactNamespace": "checkpoint",
                "idempotencyKey": "workflow-1:checkpoint:outside",
                "baseCommit": "abc123",
            }
        )


async def test_sandbox_checkout_rejects_local_path_outside_workspace_root(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    source = tmp_path / "repo"
    source.mkdir()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="must be under workspace_root",
    ):
        await activities.sandbox_checkout_repo(
            repo_ref=source,
            idempotency_key="checkout-outside",
        )

async def test_sandbox_run_command_allows_allowlisted_file_change(tmp_path: Path):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "allowlisted"
    workspace.mkdir(parents=True)
    target = workspace / "allowed.txt"
    target.write_text("before\n", encoding="utf-8")

    result = await activities.sandbox_run_command(
        workspace_ref=workspace,
        cmd=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('allowed.txt').write_text('after\\n')",
        ),
        allowed_file_paths=("allowed.txt",),
    )

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "after\n"

async def test_sandbox_run_command_allows_directory_allowlisted_file_change(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "allowlisted-dir"
    workspace.mkdir(parents=True)
    target = workspace / "allowed" / "nested.txt"
    target.parent.mkdir()
    target.write_text("before\n", encoding="utf-8")

    result = await activities.sandbox_run_command(
        workspace_ref=workspace,
        cmd=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('allowed/nested.txt').write_text('after\\n')",
        ),
        allowed_file_paths=("allowed",),
    )

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "after\n"

async def test_sandbox_run_command_rejects_file_change_outside_allowlist(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "blocked"
    workspace.mkdir(parents=True)
    allowed = workspace / "allowed.txt"
    blocked = workspace / "blocked.txt"
    allowed.write_text("allowed\n", encoding="utf-8")
    blocked.write_text("before\n", encoding="utf-8")

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="modified files outside the allowlist: blocked.txt",
    ):
        await activities.sandbox_run_command(
            workspace_ref=workspace,
            cmd=(
                sys.executable,
                "-c",
                "from pathlib import Path; Path('blocked.txt').write_text('after\\n')",
            ),
            allowed_file_paths=("allowed.txt",),
        )

    assert blocked.read_text(encoding="utf-8") == "before\n"

async def test_sandbox_run_command_rejects_permission_change_outside_allowlist(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "blocked-mode"
    workspace.mkdir(parents=True)
    allowed = workspace / "allowed.txt"
    blocked = workspace / "blocked.sh"
    allowed.write_text("allowed\n", encoding="utf-8")
    blocked.write_text("#!/bin/sh\n", encoding="utf-8")
    blocked.chmod(0o644)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="modified files outside the allowlist: blocked.sh",
    ):
        await activities.sandbox_run_command(
            workspace_ref=workspace,
            cmd=(
                sys.executable,
                "-c",
                "from pathlib import Path; Path('blocked.sh').chmod(0o755)",
            ),
            allowed_file_paths=("allowed.txt",),
        )

    assert stat.S_IMODE(blocked.stat().st_mode) == 0o644

async def test_sandbox_apply_patch_enforces_file_allowlist(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            workspace = tmp_path / "workspaces" / "temporal_sandbox" / "patch-blocked"
            workspace.mkdir(parents=True)
            (workspace / "blocked.txt").write_text("before\n", encoding="utf-8")

            patch_artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )
            await service.write_complete(
                artifact_id=patch_artifact.artifact_id,
                principal="user-1",
                payload=(
                    "--- blocked.txt\n+++ blocked.txt\n@@ -1 +1 @@\n-before\n+after\n"
                ).encode("utf-8"),
                content_type="text/plain",
            )

            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=tmp_path / "workspaces",
            )

            with pytest.raises(
                TemporalActivityRuntimeError,
                match="modified files outside the allowlist: blocked.txt",
            ):
                await activities.sandbox_apply_patch(
                    workspace_ref=workspace,
                    patch_ref=patch_artifact.artifact_id,
                    principal="user-1",
                    allowed_file_paths=("allowed.txt",),
                )

            assert (workspace / "blocked.txt").read_text(encoding="utf-8") == "before\n"

async def test_sandbox_checkout_repo_clones_github_slug_and_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    recorded_commands: list[list[str]] = []

    async def _fake_run_command(request, /, **_kwargs):
        cmd = [str(token) for token in request.get("cmd", [])]
        recorded_commands.append(cmd)
        if cmd[:2] == ["git", "clone"] and len(cmd) >= 4:
            Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        return SandboxCommandResult(
            exit_code=0,
            command=tuple(cmd),
            duration_ms=1,
            stdout_tail="ok",
            stderr_tail="",
            diagnostics_ref=None,
        )

    monkeypatch.setattr(activities, "sandbox_run_command", _fake_run_command)

    workspace = await activities.sandbox_checkout_repo(
        repo_ref="MoonLadderStudios/MoonMind",
        idempotency_key="checkout-remote",
        checkout_revision="main",
    )

    assert Path(workspace).exists()
    assert recorded_commands[0][:3] == [
        "git",
        "clone",
        "https://github.com/MoonLadderStudios/MoonMind.git",
    ]
    assert recorded_commands[1] == ["git", "checkout", "main"]

async def test_shared_envelope_helpers_build_compact_runtime_contracts():
    invocation = build_activity_invocation_envelope(
        correlation_id="corr-1",
        idempotency_key="idem-1",
        input_refs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V8"],
        parameters={"phase": "run"},
    )
    result = build_compact_activity_result(
        output_refs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"],
        summary={"status": "ok"},
        metrics={"tokens": 12},
        diagnostics_ref="art_01HJ4M3Y7RM4C5S2P3Q8G6T7VA",
    )
    context = build_activity_execution_context(
        workflow_id="wf-1",
        run_id="run-1",
        activity_id="act-1",
        attempt=2,
        task_queue="mm.activity.sandbox",
    )
    summary = build_observability_summary(
        context=context,
        activity_type="sandbox.run_command",
        correlation_id=invocation.correlation_id,
        idempotency_key="idem-1",
        outcome="completed",
        diagnostics_ref=result.diagnostics_ref,
        metrics_dimensions={"fleet": "sandbox"},
    )

    assert invocation.to_payload()["idempotency_key"] == "idem-1"
    assert result.to_payload()["output_refs"] == ["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"]
    assert summary.activity_type == "sandbox.run_command"
    assert summary.idempotency_key_hash != "idem-1"

async def test_sandbox_checkout_apply_patch_and_run_tests(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            repo = tmp_path / "workspaces" / "repo"
            repo.mkdir(parents=True)
            (repo / "sample.txt").write_text("hello\n", encoding="utf-8")
            (repo / "tools").mkdir()
            (repo / "tools" / "test_unit.sh").write_text(
                "#!/usr/bin/env sh\nprintf 'tests ok'\n",
                encoding="utf-8",
            )
            (repo / "tools" / "test_unit.sh").chmod(0o755)

            patch_artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )
            await service.write_complete(
                artifact_id=patch_artifact.artifact_id,
                principal="user-1",
                payload=(
                    "--- sample.txt\n+++ sample.txt\n@@ -1 +1 @@\n-hello\n+patched\n"
                ).encode("utf-8"),
                content_type="text/plain",
            )

            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=tmp_path / "workspaces",
            )
            workspace = await activities.sandbox_checkout_repo(
                repo_ref=repo,
                idempotency_key="checkout-1",
            )
            assert Path(workspace).exists()

            patched_workspace = await activities.sandbox_apply_patch(
                workspace_ref=workspace,
                patch_ref=patch_artifact.artifact_id,
                principal="user-1",
            )
            assert (
                Path(patched_workspace, "sample.txt").read_text(encoding="utf-8")
                == "patched\n"
            )

            report_ref = await activities.sandbox_run_tests(
                workspace_ref=patched_workspace,
                principal="user-1",
            )
            _artifact, payload = await service.read(
                artifact_id=report_ref.artifact_id,
                principal="user-1",
            )
            assert b'"exit_code": 0' in payload

async def test_build_activity_bindings_filters_to_requested_fleet(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                plan_activities=TemporalPlanActivities(artifact_service=service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=SkillActivityDispatcher()
                ),
                sandbox_activities=TemporalSandboxActivities(artifact_service=service),
                integration_activities=TemporalIntegrationActivities(
                    artifact_service=service,
                    client_factory=_FakeJulesClient,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )

            assert bindings
            assert {binding.fleet for binding in bindings} == {ARTIFACTS_FLEET}
            assert "mm.skill.execute" in {binding.activity_type for binding in bindings}
            assert "artifact.lifecycle_sweep" in {
                binding.activity_type for binding in bindings
            }
            assert "execution.record_terminal_state" in {
                binding.activity_type for binding in bindings
            }
            assert any(
                binding.handler.__name__ == "artifact_lifecycle_sweep"
                for binding in bindings
            )
            assert any(
                binding.handler.__name__ == "execution_record_terminal_state"
                for binding in bindings
            )

async def test_build_activity_bindings_resolves_memory_integration_handlers(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_FakeJulesClient,
                    ),
                    fleets=(INTEGRATIONS_FLEET,),
                )
            }

            source = {
                "workflowId": "wf-1",
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            }
            decision_result = await bindings["memory.evaluate_proposals"].handler(
                {
                    "proposal_refs": ["artifact://memory/proposal-1"],
                    "source": source,
                    "terminal_disposition": "accepted",
                    "publication_gate": {"passed": True},
                    "requested_target": "memory://run",
                    "policy_decision": "accept_for_run_context",
                }
            )
            application_result = await bindings["memory.apply_policy"].handler(
                {
                    "proposal_ref": "artifact://memory/proposal-1",
                    "decision_ref": "artifact://memory/decision-1",
                    "source": source,
                    "target": "repo://AGENTS.md",
                    "decision": "approve_repo_application",
                    "gate_status": {"terminalDisposition": "accepted"},
                }
            )

            assert decision_result["decisionRefs"] == ["artifact://memory/decision-1"]
            assert application_result["outcome"] == "blocked"
            assert (
                application_result["failureReason"]
                == "applied_repo_memory_result_requires_accepted_gates"
            )


async def test_build_activity_bindings_resolves_omnigent_execute_handler(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_FakeJulesClient,
                    ),
                    fleets=(INTEGRATIONS_FLEET,),
                )
            }

            assert "integration.omnigent.execute" in bindings
            assert (
                bindings["integration.omnigent.execute"].handler.__name__
                == "integration_omnigent_execute"
            )

async def test_build_activity_bindings_artifact_read_accepts_request_mapping(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            stored = await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b'{"ok": true}',
                content_type="application/json",
            )
            catalog = build_default_activity_catalog()

            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            artifact_read_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "artifact.read"
            )

            payload = await artifact_read_handler(
                {
                    "artifact_ref": build_artifact_ref(stored),
                    "principal": "user-1",
                }
            )

            assert payload == b'{"ok": true}'

async def test_build_activity_bindings_artifact_handlers_preserve_typed_request_signature(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    manifest_activities=TemporalManifestActivities(
                        artifact_service=service,
                    ),
                    proposal_activities=TemporalProposalActivities(artifact_service=service),
                    agent_skills_activities=AgentSkillsActivities(),
                    fleets=(ARTIFACTS_FLEET,),
                )
            }

            from typing import get_type_hints
            from moonmind.schemas.temporal_activity_models import (
                ArtifactReadInput,
                ArtifactWriteCompleteInput,
            )

            read_handler_hints = get_type_hints(bindings["artifact.read"].handler)
            write_handler_hints = get_type_hints(
                bindings["artifact.write_complete"].handler
            )

            annotation_globals = dict(TemporalArtifactActivities.artifact_read.__globals__)
            annotation_globals.update({
                "ArtifactReadInput": ArtifactReadInput,
                "ArtifactWriteCompleteInput": ArtifactWriteCompleteInput,
            })

            read_method_hints = get_type_hints(
                TemporalArtifactActivities.artifact_read, globalns=annotation_globals
            )
            write_method_hints = get_type_hints(
                TemporalArtifactActivities.artifact_write_complete, globalns=annotation_globals
            )

            assert read_handler_hints["request"] == read_method_hints["request"]
            assert read_handler_hints.get("return") == read_method_hints.get("return")

            assert write_handler_hints["request"] == write_method_hints["request"]
            assert write_handler_hints.get("return") == write_method_hints.get("return")

async def test_build_activity_bindings_artifact_read_accepts_serialized_ref_mapping(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            stored = await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b'{"ok": true}',
                content_type="application/json",
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            artifact_read_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "artifact.read"
            )
            serialized_ref = {
                "artifact_id": stored.artifact_id,
                "artifact_ref_v": 1,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
                "content_type": stored.content_type,
                "encryption": stored.encryption,
            }

            payload = await artifact_read_handler(
                {
                    "artifact_ref": serialized_ref,
                    "principal": "user-1",
                }
            )

            assert payload == b'{"ok": true}'

async def test_build_activity_bindings_artifact_write_complete_accepts_legacy_payload_mapping(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/octet-stream",
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            artifact_write_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "artifact.write_complete"
            )

            stored_ref = await artifact_write_handler(
                {
                    "artifact_id": artifact.artifact_id,
                    "principal": "user-1",
                    "payload": list(b'{"ok": true}'),
                    "content_type": "application/json",
                }
            )
            _stored_artifact, payload = await service.read(
                artifact_id=artifact.artifact_id,
                principal="user-1",
            )

            assert stored_ref.artifact_id == artifact.artifact_id
            assert payload == b'{"ok": true}'

async def test_build_activity_bindings_injected_skill_handler_uses_request_mapping(
    tmp_path: Path,
):
    class _KeywordOnlySkillActivities:
        async def mm_skill_execute(
            self,
            *,
            invocation_payload: Mapping[str, object],
            principal: str,
        ) -> dict[str, object]:
            return {
                "invocationId": invocation_payload.get("id"),
                "principal": principal,
            }

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                skill_activities=_KeywordOnlySkillActivities(),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            skill_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "mm.skill.execute"
            )

            result = await skill_handler(
                {
                    "invocation_payload": {"id": "node-1"},
                    "principal": "user-1",
                }
            )

            assert result["invocationId"] == "node-1"
            assert result["principal"] == "user-1"

async def test_build_activity_bindings_mm_tool_execute_handler_supports_keyword_payload(
    tmp_path: Path,
):
    dispatcher = SkillActivityDispatcher()
    captured_context: dict[str, object] = {}

    def _run_tests_handler(
        inputs: Mapping[str, object],
        context: Mapping[str, object] | None,
    ) -> SkillResult:
        captured_context.update(dict(context or {}))
        return SkillResult(
            status="COMPLETED",
            outputs={"ok": str(inputs["repo_ref"]).endswith("#main")},
        )

    dispatcher.register_skill(
        skill_name="repo.run_tests",
        handler=_run_tests_handler,
    )
    snapshot = create_registry_snapshot(
        skills=parse_skill_registry(_registry_payload()),
        artifact_store=InMemoryArtifactStore(),
    )

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=dispatcher,
                    artifact_service=service,
                ),
                sandbox_activities=TemporalSandboxActivities(
                    artifact_service=service,
                    workspace_root=tmp_path,
                ),
                fleets=(SANDBOX_FLEET,),
            )
            tool_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "mm.tool.execute"
            )

            result = await tool_handler(
                {
                    "invocation_payload": {
                        "id": "n1",
                        "skill": {"name": "repo.run_tests"},
                        "inputs": {"repo_ref": "git:org/repo#main"},
                    },
                    "registry_snapshot": snapshot,
                    "context": {"workflow_id": "wf-1"},
                    "idempotency_key": "wf-1_n1_execute",
                }
            )

            assert result.status == "COMPLETED"
            assert result.outputs["ok"] is True
            assert captured_context["workflow_id"] == "wf-1"
            assert captured_context["idempotency_key"] == "wf-1_n1_execute"


async def test_mm_tool_execute_preserves_tool_failure_envelope() -> None:
    dispatcher = SkillActivityDispatcher()

    def _failing_handler(
        _inputs: dict[str, object],
        _context: dict[str, object] | None,
    ) -> SkillResult:
        leaked_token = "ghp_toolfailuretoken1234567890abcd"
        raise ToolFailure(
            error_code="DEPLOYMENT_RUNNER_UNSAFE",
            message=(
                "Deployment update would recreate the worker container "
                f"with token {leaked_token}."
            ),
            retryable=False,
            details={
                "failureClass": "runner_self_recreation_unsafe",
                "service": "temporal-worker-agent-runtime",
                "token": leaked_token,
            },
        )

    dispatcher.register_skill(
        skill_name="repo.run_tests",
        handler=_failing_handler,
    )
    snapshot = create_registry_snapshot(
        skills=parse_skill_registry(_registry_payload()),
        artifact_store=InMemoryArtifactStore(),
    )
    activities = TemporalSkillActivities(dispatcher=dispatcher)

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.mm_tool_execute(
            invocation_payload={
                "id": "deploy",
                "tool": {
                    "type": "skill",
                    "name": "repo.run_tests",
                },
                "inputs": {"repo_ref": "git:org/repo#main"},
            },
            registry_snapshot=snapshot,
        )

    assert exc_info.value.type == "DEPLOYMENT_RUNNER_UNSAFE"
    assert exc_info.value.non_retryable is True
    assert "Deployment update would recreate" in str(exc_info.value)
    assert "ghp_toolfailuretoken1234567890abcd" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
    assert exc_info.value.details[0] == {
        "error_code": "DEPLOYMENT_RUNNER_UNSAFE",
        "message": (
            "Deployment update would recreate the worker container "
            "with token [REDACTED]."
        ),
        "retryable": False,
        "details": {
            "failureClass": "runner_self_recreation_unsafe",
            "service": "temporal-worker-agent-runtime",
            "token": "[REDACTED]",
        },
    }


async def test_build_activity_bindings_does_not_mutate_sandbox_method_signatures(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            dispatcher = SkillActivityDispatcher()
            sandbox_activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=tmp_path,
            )
            build_activity_bindings(
                build_default_activity_catalog(),
                artifact_activities=TemporalArtifactActivities(service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=dispatcher,
                    artifact_service=service,
                ),
                sandbox_activities=sandbox_activities,
                fleets=(SANDBOX_FLEET,),
            )

            workspace = tmp_path / "temporal_sandbox" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            result = await sandbox_activities.sandbox_run_command(
                workspace_ref=workspace,
                cmd=("bash", "-lc", "true"),
                principal="user-1",
            )

            assert result.exit_code == 0

async def test_sandbox_run_command_env_allows_unsetting_parent_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MM_TEMP_ENV_UNSET_TEST", "present")
    sandbox_activities = TemporalSandboxActivities(workspace_root=tmp_path)
    workspace = tmp_path / "temporal_sandbox" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    result = await sandbox_activities.sandbox_run_command(
        workspace_ref=workspace,
        cmd=("bash", "-lc", '[ -z "${MM_TEMP_ENV_UNSET_TEST+x}" ]'),
        principal="user-1",
        env={"MM_TEMP_ENV_UNSET_TEST": None},
    )

    assert result.exit_code == 0

async def test_build_activity_bindings_requires_selected_family_implementation(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            with pytest.raises(
                TemporalActivityRuntimeError,
                match="sandbox implementation",
            ):
                build_activity_bindings(
                    catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    fleets=(SANDBOX_FLEET,),
                )

async def test_build_activity_bindings_resolves_agent_runtime_fleet(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            bindings = build_activity_bindings(
                catalog,
                agent_runtime_activities=TemporalAgentRuntimeActivities(
                    artifact_service=service,
                ),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(AGENT_RUNTIME_FLEET,),
            )

            assert bindings
            assert {binding.fleet for binding in bindings} == {AGENT_RUNTIME_FLEET}
            bound_types = {binding.activity_type for binding in bindings}
            assert "agent_runtime.build_launch_context" in bound_types
            assert "agent_runtime.launch_session" in bound_types
            assert "agent_runtime.publish_artifacts" in bound_types
            assert "agent_runtime.session_status" in bound_types
            assert "agent_runtime.prepare_turn_instructions" in bound_types
            assert "agent_runtime.send_turn" in bound_types
            assert "agent_runtime.steer_turn" in bound_types
            assert "agent_runtime.interrupt_turn" in bound_types
            assert "agent_runtime.clear_session" in bound_types
            assert "agent_runtime.terminate_session" in bound_types
            assert "agent_runtime.fetch_session_summary" in bound_types
            assert "agent_runtime.publish_session_artifacts" in bound_types
            assert "agent_runtime.cleanup_managed_runtime_files" in bound_types
            assert "agent_runtime.status" in bound_types
            assert "agent_runtime.fetch_result" in bound_types
            assert "agent_runtime.cancel" in bound_types
            assert "agent_skill.resolve" in bound_types
            assert "agent_skill.materialize" in bound_types
            assert "agent_skill.build_prompt_index" in bound_types
            assert "agent_skill.query_on_demand" in bound_types
            assert "agent_skill.request_on_demand" in bound_types


async def test_prepare_managed_codex_turn_text_hides_on_demand_command_names_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "skills_on_demand_enabled", False)

    result = TemporalAgentRuntimeActivities._prepare_managed_codex_turn_text(
        "Use the selected skill.",
        parameters={
            "metadata": {
                "moonmind": {
                    "selectedSkill": "moonspec-implement",
                }
            }
        },
        skill_materialization_metadata=None,
    )

    assert "Skills On Demand is disabled for this run." in result
    assert "moonmind.skills.query" not in result
    assert "moonmind.skills.request" not in result


async def test_agent_runtime_publish_artifacts_publishes_explicit_report_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(artifact_service=service)

            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:node-1",
                    workflow_run_id="child-run-1",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "operator_summary": "# Integration test report\n\nAll tests passed.",
                        "moonmind": {
                            "reportOutput": {
                                "enabled": True,
                                "required": True,
                                "reportType": "integration_test_report",
                                "primaryPath": "reports/final-report",
                                "executionRef": {
                                    "namespace": "default",
                                    "workflow_id": "parent-wf",
                                    "run_id": "parent-run-1",
                                },
                            }
                        },
                    },
                )
            )

            assert result is not None
            assert result.metadata["primaryReportRef"].startswith("art_")
            assert result.metadata["reportBundle"]["report_bundle_v"] == 1

            reports = await service.list_for_execution(
                namespace="default",
                workflow_id="parent-wf",
                run_id="parent-run-1",
                principal="system:agent_runtime",
                link_type="report.primary",
                latest_only=True,
            )
            assert len(reports) == 1
            assert reports[0].metadata_json["report_type"] == "integration_test_report"
            assert reports[0].metadata_json["report_scope"] == "final"
            assert reports[0].metadata_json["is_final_report"] is True
            assert reports[0].metadata_json["name"] == "final-report.md"
            _artifact, path = await service.read_path(
                artifact_id=reports[0].artifact_id,
                principal="system:agent_runtime",
            )
            body = path.read_bytes()
            assert body.decode("utf-8") == "# Integration test report\n\nAll tests passed.\n"


async def test_agent_runtime_publish_artifacts_publishes_moonspec_verify_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            verify_path = workspace / "var/artifacts/moonspec-verify/final.json"
            verify_path.parent.mkdir(parents=True)
            large_evidence = "verified evidence " * 2000
            verify_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "verdict": "FULLY_IMPLEMENTED",
                        "recommendedNextAction": "advance",
                        "recoverableInCurrentRuntime": True,
                        "remainingWork": [],
                        "requirementCoverage": [
                            {
                                "requirement": "large verification evidence",
                                "evidence": large_evidence,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="verify-run-1",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities,
                "execution_notify_completion",
                _skip_notify,
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:verify",
                    workflow_run_id="child-run-verify",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "agentRunId": "verify-run-1",
                        "verify_artifact_path": (
                            "var/artifacts/moonspec-verify/final.json"
                        ),
                    },
                )
            )

            assert isinstance(result, AgentRunResult)
            assert result.metadata["gateResultRef"].startswith("art_")
            assert result.metadata["moonSpecVerifyArtifactRef"] == (
                result.metadata["gateResultRef"]
            )
            assert result.metadata["moonSpecVerify"]["verdict"] == "FULLY_IMPLEMENTED"
            assert result.metadata["moonSpecVerify"]["gateResultRef"] == (
                result.metadata["gateResultRef"]
            )
            assert "contractViolations" not in result.metadata["moonSpecVerify"]
            assert "requirementCoverage" not in result.metadata["moonSpecVerify"]
            AgentRunResult(**result.model_dump(mode="json", by_alias=True))

            _artifact, artifact_path = await service.read_path(
                artifact_id=result.metadata["gateResultRef"],
                principal="system:agent_runtime",
            )
            persisted_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert persisted_payload["requirementCoverage"][0]["evidence"] == (
                large_evidence
            )


async def test_agent_runtime_publish_artifacts_publishes_remediation_attempt_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            attempt_path = workspace / "reports/remediation_attempt-1.json"
            attempt_path.parent.mkdir(parents=True)
            attempt_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "v1",
                        "attempt": 1,
                        "maxAttempts": 6,
                        "inputVerificationRef": {"artifact_id": "art_verify_0"},
                        "knownGaps": [
                            {
                                "gapId": "gap-1",
                                "source": "verification_report",
                                "status": "addressed",
                                "reason": "covered by focused test",
                            }
                        ],
                        "changedFiles": ["moonmind/workflows/temporal/activity_runtime.py"],
                        "targetedChecks": [
                            {
                                "command": "pytest tests/unit/workflows/temporal/test_activity_runtime.py",
                                "result": "pass",
                            }
                        ],
                        "nextVerificationRequired": True,
                    }
                ),
                encoding="utf-8",
            )
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="remediate-run-1",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities,
                "execution_notify_completion",
                _skip_notify,
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:remediate",
                    workflow_run_id="child-run-remediate",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "agentRunId": "remediate-run-1",
                        "moonmind": {
                            "stepLedger": {
                                "logicalStepId": "remediate-1",
                                "attempt": 1,
                                "scope": "step",
                            },
                            "remediationCadence": {
                                "cadence": "attempt_scoped_remediation_verification",
                                "role": "moonspec-remediation",
                                "attempt": 1,
                                "maxAttempts": 6,
                                "attemptArtifactPath": "reports/remediation_attempt-1.json",
                                "latestVerificationPath": "artifacts/jira-implement-verify.json",
                            },
                        },
                    },
                )
            )

            assert isinstance(result, AgentRunResult)
            attempt_ref = result.metadata["remediationAttemptArtifactRef"]
            artifact, artifact_path = await service.read_path(
                artifact_id=attempt_ref,
                principal="system:agent_runtime",
            )
            persisted_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert artifact.metadata_json["artifact_type"] == "remediation.attempt"
            assert artifact.metadata_json["name"] == "reports/remediation_attempt-1.json"
            assert persisted_payload["knownGaps"][0]["gapId"] == "gap-1"
            assert persisted_payload["targetedChecks"][0]["result"] == "pass"
            assert persisted_payload["nextVerificationRequired"] is True


async def test_remaining_work_digest_is_independent_of_entry_order() -> None:
    first = {
        "requirement": "artifact linkage",
        "gapType": "implementation",
    }
    second = {
        "requirement": "workflow routing",
        "gapType": "behavior",
    }

    assert activity_runtime_module._unordered_json_list_digest(
        [first, second]
    ) == activity_runtime_module._unordered_json_list_digest([second, first])
    assert activity_runtime_module._unordered_json_list_digest(
        [first, second]
    ) != activity_runtime_module._unordered_json_list_digest(
        [first, {**second, "gapType": "contract"}]
    )


async def test_agent_runtime_publish_artifacts_links_remediation_verification_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            verify_path = workspace / "var/artifacts/moonspec-verify/final.json"
            verify_path.parent.mkdir(parents=True)
            verify_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "moonspec-verify.issue_brief.v1",
                        "moonSpecVerdict": "ADDITIONAL_WORK_NEEDED",
                        "recommendedNextAction": "reattempt_current_step",
                        "recoverableInCurrentRuntime": True,
                        "remainingWork": [
                            {
                                "requirement": "artifact linkage",
                                "gapType": "implementation",
                                "remainingWork": "link verification to attempt",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="verify-run-2",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities,
                "execution_notify_completion",
                _skip_notify,
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:verify",
                    workflow_run_id="child-run-verify",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "agentRunId": "verify-run-2",
                        "verify_artifact_path": (
                            "var/artifacts/moonspec-verify/final.json"
                        ),
                        "moonmind": {
                            "stepLedger": {
                                "logicalStepId": "verify-1",
                                "attempt": 1,
                                "scope": "step",
                            },
                            "remediationCadence": {
                                "cadence": "attempt_scoped_remediation_verification",
                                "role": "moonspec-verification-gate",
                                "attempt": 1,
                                "maxAttempts": 6,
                                "attemptArtifactPath": "reports/remediation_attempt-1.json",
                                "verificationArtifactPath": "reports/remediation_verification-1.json",
                            },
                        },
                    },
                )
            )

            assert isinstance(result, AgentRunResult)
            verification_ref = result.metadata["remediationVerificationArtifactRef"]
            assert result.metadata["gateResultRef"] == verification_ref
            assert result.metadata["moonSpecVerifyArtifactRef"] == verification_ref
            assert result.metadata["sourceMoonSpecVerifyArtifactRef"] != verification_ref
            assert result.metadata["moonSpecVerify"]["remainingWorkRef"] == (
                result.metadata["sourceMoonSpecVerifyArtifactRef"]
            )
            evidence = result.metadata["moonSpecVerify"]["validatedRefs"]
            assert evidence["progressEvidenceSchemaVersion"] == (
                "remediation-progress-evidence/v1"
            )
            assert evidence["authoritativeEvidenceDigest"].startswith("sha256:")
            artifact, artifact_path = await service.read_path(
                artifact_id=verification_ref,
                principal="system:agent_runtime",
            )
            persisted_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert artifact.metadata_json["artifact_type"] == "remediation.verification"
            assert artifact.metadata_json["verifiesAttempt"] == 1
            assert persisted_payload["verifiesAttempt"] == 1
            assert persisted_payload["verdict"] == "ADDITIONAL_WORK_NEEDED"
            assert persisted_payload["inputRemediationAttemptRef"] == {
                "artifact_type": "remediation.attempt",
                "name": "reports/remediation_attempt-1.json",
            }
            assert persisted_payload["verifierEvidenceRefs"][
                "moonSpecVerifyArtifactRef"
            ] == result.metadata["sourceMoonSpecVerifyArtifactRef"]
            assert persisted_payload["remainingGaps"][0]["requirement"] == (
                "artifact linkage"
            )
            assert persisted_payload["moonSpecVerify"]["remainingWorkRef"] == (
                result.metadata["sourceMoonSpecVerifyArtifactRef"]
            )


async def test_agent_runtime_publish_artifacts_canonicalizes_moonspec_next_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            verify_path = workspace / "var/artifacts/moonspec-verify/final.json"
            verify_path.parent.mkdir(parents=True)
            # Regression fixture mirroring the observed drift: an approving
            # verdict paired with a non-canonical recommendedNextAction.
            verify_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "moonspec-verify.issue_brief.v1",
                        "verdict": "FULLY_IMPLEMENTED",
                        "recommendedNextAction": "create_pull_request",
                        "recoverableInCurrentRuntime": True,
                        "remainingWork": [],
                    }
                ),
                encoding="utf-8",
            )
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="verify-run-2",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities,
                "execution_notify_completion",
                _skip_notify,
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:verify",
                    workflow_run_id="child-run-verify-2",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "agentRunId": "verify-run-2",
                        "verify_artifact_path": (
                            "var/artifacts/moonspec-verify/final.json"
                        ),
                    },
                )
            )

            verify_payload = result.metadata["moonSpecVerify"]
            assert verify_payload["recommendedNextAction"] == "advance"
            assert (
                verify_payload["rawRecommendedNextAction"]
                == "create_pull_request"
            )
            assert "contractViolations" not in verify_payload
            AgentRunResult(**result.model_dump(mode="json", by_alias=True))


async def test_agent_runtime_publish_artifacts_uses_last_assistant_text_for_report_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(artifact_service=service)

            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:node-1",
                    workflow_run_id="child-run-1",
                ),
            )

            await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed with status completed",
                    metadata={
                        "lastAssistantText": (
                            "# Docker Compose Update System Report\n\n"
                            "The implementation is missing report handoff coverage."
                        ),
                        "moonmind": {
                            "reportOutput": {
                                "enabled": True,
                                "required": True,
                                "reportType": "agent_run_report",
                                "primaryPath": "exports/final-answer.txt",
                                "executionRef": {
                                    "namespace": "default",
                                    "workflow_id": "parent-wf",
                                    "run_id": "parent-run-1",
                                },
                            }
                        },
                    },
                )
            )

            reports = await service.list_for_execution(
                namespace="default",
                workflow_id="parent-wf",
                run_id="parent-run-1",
                principal="system:agent_runtime",
                link_type="report.primary",
                latest_only=True,
            )

            assert len(reports) == 1
            assert reports[0].metadata_json["name"] == "final-answer.txt"
            _artifact, path = await service.read_path(
                artifact_id=reports[0].artifact_id,
                principal="system:agent_runtime",
            )
            rendered = path.read_text(encoding="utf-8")
            assert rendered.startswith("# Docker Compose Update System Report")
            assert "missing report handoff coverage" in rendered
            assert "Completed with status completed" not in rendered

async def test_agent_runtime_publish_artifacts_fails_required_report_on_publish_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingReportService:
        def __init__(self, wrapped: TemporalArtifactService) -> None:
            self._wrapped = wrapped

        async def create(self, **kwargs: Any) -> Any:
            return await self._wrapped.create(**kwargs)

        async def write_complete(self, **kwargs: Any) -> Any:
            return await self._wrapped.write_complete(**kwargs)

        async def publish_report_bundle(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("report publication failed")

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=_FailingReportService(service)  # type: ignore[arg-type]
            )

            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:node-1",
                    workflow_run_id="child-run-1",
                ),
            )

            with pytest.raises(RuntimeError, match="report publication failed"):
                await activities.agent_runtime_publish_artifacts(
                    AgentRunResult(
                        summary="Completed.",
                        metadata={
                            "moonmind": {
                                "reportOutput": {
                                    "enabled": True,
                                    "required": True,
                                    "reportType": "integration_test_report",
                                    "executionRef": {
                                        "namespace": "default",
                                        "workflow_id": "parent-wf",
                                        "run_id": "parent-run-1",
                                    },
                                }
                            }
                        },
                    )
                )

async def test_agent_runtime_send_turn_retries_transient_failures(
    tmp_path: Path,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker():
            catalog = build_default_activity_catalog()

            send_turn = catalog.resolve_activity("agent_runtime.send_turn")

            assert send_turn.retries.max_attempts == 5
            assert send_turn.retries.max_interval_seconds == 600
            assert (
                "CodexPermanentTurnError"
                in send_turn.retries.non_retryable_error_codes
            )
            assert send_turn.timeouts.start_to_close_seconds == 3600
            assert send_turn.timeouts.schedule_to_close_seconds == 3900
            assert send_turn.timeouts.heartbeat_timeout_seconds == 30


async def test_agent_runtime_publish_artifacts_publishes_assessment_verdict_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            assessment_path = (
                workspace / "artifacts/jira-implement-assessment.json"
            )
            assessment_path.parent.mkdir(parents=True)
            assessment_path.write_text(
                json.dumps(
                    {
                        "issue_provider": "jira",
                        "issue_ref": "MM-1139",
                        "verdict": "NOT_IMPLEMENTED",
                        "mode": "main",
                        "summary": "not implemented",
                        "requirements": [],
                    }
                ),
                encoding="utf-8",
            )
            brief_path = workspace / "artifacts/jira-implement-brief.json"
            brief_path.write_text(
                json.dumps(
                    {
                        "issue_provider": "jira",
                        "issue_ref": "MM-1139",
                        "requirements": ["Persist approval decisions."],
                        "constraints": "",
                    }
                ),
                encoding="utf-8",
            )
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="assess-run-1",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities, "execution_notify_completion", _skip_notify
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:assess",
                    workflow_run_id="child-run-assess",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "agentRunId": "assess-run-1",
                        "parentWorkflowId": "parent-wf",
                        "parentRunId": "parent-run",
                        "assessment_artifact_path": (
                            "artifacts/jira-implement-assessment.json"
                        ),
                        "brief_artifact_path": (
                            "artifacts/jira-implement-brief.json"
                        ),
                    },
                )
            )

            assert isinstance(result, AgentRunResult)
            assert result.metadata["assessmentArtifactRef"].startswith("art_")
            assert result.metadata["assessmentVerdict"] == "NOT_IMPLEMENTED"
            assert result.metadata["briefArtifactRef"].startswith("art_")

            # The published artifact carries the full structured verdict payload,
            # readable by ref (the bridge-compatible channel) without a shared FS.
            _artifact, artifact_path = await service.read_path(
                artifact_id=result.metadata["assessmentArtifactRef"],
                principal="system:agent_runtime",
            )
            persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert persisted["verdict"] == "NOT_IMPLEMENTED"
            assert persisted["issue_ref"] == "MM-1139"
            _artifact, links, _pinned, _policy = await service.get_metadata(
                artifact_id=result.metadata["assessmentArtifactRef"],
                principal="system:agent_runtime",
            )
            assert any(
                link.workflow_id == "parent-wf"
                and link.run_id == "parent-run"
                and link.link_type == "input.assessment_handoff"
                for link in links
            )
            _artifact, brief_artifact_path = await service.read_path(
                artifact_id=result.metadata["briefArtifactRef"],
                principal="system:agent_runtime",
            )
            persisted_brief = json.loads(
                brief_artifact_path.read_text(encoding="utf-8")
            )
            assert persisted_brief["issue_ref"] == "MM-1139"
            _artifact, brief_links, _pinned, _policy = await service.get_metadata(
                artifact_id=result.metadata["briefArtifactRef"],
                principal="system:agent_runtime",
            )
            assert any(
                link.workflow_id == "parent-wf"
                and link.run_id == "parent-run"
                and link.link_type == "input.issue_brief_handoff"
                for link in brief_links
            )

            brief_path.write_text("not-json", encoding="utf-8")
            with pytest.raises(
                TemporalActivityRuntimeError,
                match="issue brief artifact could not be read as JSON",
            ):
                await activities.agent_runtime_publish_artifacts(
                    AgentRunResult(
                        summary="Completed with malformed required brief.",
                        metadata={
                            "agentRunId": "assess-run-1",
                            "brief_artifact_path": (
                                "artifacts/jira-implement-brief.json"
                            ),
                        },
                    )
                )


async def test_agent_runtime_publish_artifacts_resolves_omnigent_sandbox_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote runtimes publish portable outputs through their sandbox locator."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace_root = tmp_path / "agent_workspaces"
            workspace_id = "sandbox-assess-1"
            workflow_id = "parent-wf"
            step_execution_id = "parent-wf:run-1:step-2:execution:1"
            workspace = (
                workspace_root / "temporal_sandbox" / workspace_id / "repo"
            )
            assessment_path = (
                workspace / "artifacts/github-issue-implement-assessment.json"
            )
            assessment_path.parent.mkdir(parents=True)
            assessment_path.write_text(
                json.dumps(
                    {
                        "issue_provider": "github",
                        "issue_ref": "MoonLadderStudios/MoonMind#3620",
                        "verdict": "PARTIALLY_IMPLEMENTED",
                        "mode": "main",
                        "summary": "remaining approval lifecycle gaps",
                        "requirements": [],
                    }
                ),
                encoding="utf-8",
            )
            SandboxWorkspaceRecordStore(workspace_root).ensure(
                SandboxWorkspaceRecord(
                    workspace_id=workspace_id,
                    workflow_id=workflow_id,
                    step_execution_id=step_execution_id,
                    relative_path="repo",
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                workspace_root=workspace_root,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities, "execution_notify_completion", _skip_notify
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:step-2",
                    workflow_run_id="child-run-assess",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Omnigent session completed",
                    metadata={
                        "correlationId": workflow_id,
                        "idempotencyKey": f"{step_execution_id}:agent_execute",
                        "workspaceLocator": {
                            "kind": "sandbox",
                            "workspaceId": workspace_id,
                            "relativePath": "repo",
                        },
                        "assessment_artifact_path": (
                            "artifacts/github-issue-implement-assessment.json"
                        ),
                    },
                )
            )

            assert isinstance(result, AgentRunResult)
            assert result.metadata["assessmentArtifactRef"].startswith("art_")
            assert result.metadata["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"
            _artifact, artifact_path = await service.read_path(
                artifact_id=result.metadata["assessmentArtifactRef"],
                principal="system:agent_runtime",
            )
            persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert persisted["issue_ref"] == "MoonLadderStudios/MoonMind#3620"


async def test_agent_runtime_publish_artifacts_requires_declared_assessment_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful assessment cannot advance without its controlling verdict."""

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="assess-run-missing",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities, "execution_notify_completion", _skip_notify
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:assess",
                    workflow_run_id="child-run-assess-missing",
                ),
            )

            with pytest.raises(
                TemporalActivityRuntimeError,
                match="Declared assessment verdict artifact was not produced",
            ):
                await activities.agent_runtime_publish_artifacts(
                    AgentRunResult(
                        summary="Completed.",
                        metadata={
                            "agentRunId": "assess-run-missing",
                            "assessment_artifact_path": (
                                "artifacts/github-issue-implement-assessment.json"
                            ),
                        },
                    )
                )


async def test_agent_runtime_publish_artifacts_skips_assessment_without_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Agent steps that do not declare an assessment path must not surface a ref.
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(artifact_service=service)

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities, "execution_notify_completion", _skip_notify
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:other",
                    workflow_run_id="child-run-other",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(summary="Completed.", metadata={"agentRunId": "x"})
            )

            assert "assessmentArtifactRef" not in (result.metadata or {})
            assert "assessmentVerdict" not in (result.metadata or {})
