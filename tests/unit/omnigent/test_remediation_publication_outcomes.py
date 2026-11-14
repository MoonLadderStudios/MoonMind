from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities import (
    omnigent_session_activities as activities,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


def _remediation_request(workspace_root, status):
    step_execution_id = "workflow-1:run-1:repair:execution:1"
    workspace_id = hashlib.sha256(
        f"workflow-1:{step_execution_id}".encode()
    ).hexdigest()[:24]
    locator = {"kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"}
    record = SandboxWorkspaceRecord(
        workspace_id, "workflow-1", step_execution_id, "repo"
    )
    SandboxWorkspaceRecordStore(workspace_root).ensure(record)
    workspace = workspace_root / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    if status != "missing":
        (workspace / "outcome.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "moonmind.remediate-workflow-result.v1",
                    "executionRef": step_execution_id,
                    "status": status,
                    "summary": (
                        "Required context was not delivered."
                        if status != "succeeded"
                        else "Diagnosed a transient failure; retry the interrupted step."
                    ),
                }
            )
        )
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow-1",
        idempotencyKey="step-1",
        workspaceSpec={
            "workspaceLocator": locator,
            "repository": "owner/repo",
            "startingBranch": "main",
        },
        parameters={"publishMode": "branch", "repository": "owner/repo"},
        stepExecution={
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "repair",
            "executionOrdinal": 1,
            "stepExecutionId": step_execution_id,
            "runtimeContextPolicy": "fresh_agent_run",
        },
        terminalContract={
            "contractId": "remediate_workflow_terminal.v1",
            "relativePath": "outcome.json",
            "expectedSchemaVersion": "moonmind.remediate-workflow-result.v1",
            "executionRef": step_execution_id,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["blocked", "failed", "missing", "succeeded", "provider_failed"]
)
@pytest.mark.parametrize("prior_publication", [False, True])
async def test_canonical_session_applies_remediation_verdict_before_publication(
    tmp_path,
    monkeypatch,
    status,
    prior_publication,
):
    from moonmind.omnigent import control_plane

    agent_request = _remediation_request(tmp_path, status)
    session = SimpleNamespace(
        terminal_state="failed" if status == "provider_failed" else "completed",
        terminal_evidence_ref=None,
        metadata={"harvestedEvidenceRef": "art_harvest"},
        moonmind_workflow_id="workflow-1",
        step_execution_id=agent_request.step_execution.step_execution_id,
        provider_session_ref="provider-session",
        provider_event_cursor=5,
        chat_binding_id="chat",
        revision=1,
    )
    if prior_publication:
        session.metadata["publicationEvidenceRef"] = "art_prior_publication"

    async def bind_runtime_authority(*args, metadata_patch, **kwargs):
        session.metadata.update(metadata_patch)
        session.revision += 1
        return session

    sessions = SimpleNamespace(
        get=AsyncMock(return_value=session),
        bind_runtime_authority=AsyncMock(side_effect=bind_runtime_authority),
        attach_terminal_evidence=AsyncMock(),
    )
    repos = SimpleNamespace(
        sessions=sessions,
        turn_attempts=SimpleNamespace(
            list_for_session=AsyncMock(return_value=[]),
        ),
    )

    class Store:
        @asynccontextmanager
        async def transaction(self):
            yield repos

    monkeypatch.setattr(control_plane, "OmnigentControlPlaneStore", lambda _: Store())
    monkeypatch.setattr(
        activities, "_claim_command", AsyncMock(return_value=(None, True))
    )
    monkeypatch.setattr(
        activities, "_load_intent_request", AsyncMock(return_value=agent_request)
    )
    monkeypatch.setattr(
        activities, "_session_execution_authority_metadata", AsyncMock(return_value={})
    )
    monkeypatch.setattr(activities, "_settle_command", AsyncMock(return_value={}))
    monkeypatch.setattr(
        "moonmind.omnigent.profile_bound_execution.OmnigentProfileBoundExecutionCoordinator._github_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        activities,
        "_omnigent_client_context",
        AsyncMock(
            return_value=(SimpleNamespace(aclose=AsyncMock()), SimpleNamespace()),
        ),
    )
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime.publish_workspace = AsyncMock(return_value={"push_status": "pushed"})
    monkeypatch.setattr(
        "moonmind.omnigent.oauth_host_runtime.OmnigentOAuthHostRuntime",
        lambda **_: runtime,
    )
    written = {}
    artifacts = {"art_prior_publication": {"publication": {"push_status": "pushed"}}}

    async def write(*, name, artifact_type, payload):
        written[artifact_type] = payload
        ref = "art_" + artifact_type.replace(".", "_")
        artifacts[ref] = payload
        return ref

    monkeypatch.setattr(activities, "_write_json_artifact", write)
    monkeypatch.setattr(
        activities,
        "_read_json_artifact",
        AsyncMock(
            side_effect=lambda ref, **kwargs: artifacts[ref],
        ),
    )
    await activities.omnigent_publish_workspace_activity(
        {
            "sessionId": "session-1",
            "commandId": "command-1",
            "compiledExecutionIntentRef": "art_intent",
            "expectedRevision": 1,
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "fencingGeneration": 1,
        }
    )
    terminal = written["omnigent.session_terminal"]["terminalResult"]
    if status == "provider_failed":
        assert terminal["status"] == "execution_failed"
        assert terminal["result"]["summary"] == "Omnigent session failed"
        assert "omnigent.session_remediation_outcome" not in written
        return
    if status == "succeeded":
        if prior_publication:
            runtime.publish_workspace.assert_not_awaited()
        else:
            runtime.publish_workspace.assert_awaited_once()
        assert terminal["status"] == "completed"
        assert terminal["result"]["objectiveOutcome"] == "succeeded"
    else:
        runtime.publish_workspace.assert_not_awaited()
        assert terminal["status"] == "execution_failed"
        assert terminal["result"]["objectiveOutcome"] == "failed"
        assert terminal["result"]["publicationState"] == (
            "published" if prior_publication else "not_published"
        )
        assert "art_harvest" in terminal["result"]["outputRefs"]
    # A lost final acknowledgement reconciles captured evidence and confirmed
    # effects without reading a reclaimed workspace or publishing again.
    first_result = terminal["result"]
    publication_calls = runtime.publish_workspace.await_count
    runtime.inspect_request_terminal = AsyncMock(
        side_effect=AssertionError("workspace reclaimed")
    )
    await activities.omnigent_publish_workspace_activity(
        {
            "sessionId": "session-1",
            "commandId": "command-1",
            "compiledExecutionIntentRef": "art_intent",
            "expectedRevision": session.revision,
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "fencingGeneration": 1,
        }
    )
    runtime.inspect_request_terminal.assert_not_awaited()
    assert runtime.publish_workspace.await_count == publication_calls
    assert (
        written["omnigent.session_terminal"]["terminalResult"]["result"] == first_result
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_contract", [False, True])
async def test_terminal_contract_binding_uses_pinned_selected_skill_snapshot(
    tmp_path,
    monkeypatch,
    declared_contract,
):
    request = _remediation_request(tmp_path, "blocked")
    contract = request.terminal_contract
    request = request.model_copy(
        update={
            "terminal_contract": None,
            "resolved_skillset_ref": "art_skills",
            "parameters": {**request.parameters, "selectedSkill": "remediate-workflow"},
        }
    )
    snapshot = {
        "snapshot_id": "snapshot-1",
        "resolved_at": "2026-01-01T00:00:00Z",
        "skills": [
            {
                "skill_name": "remediate-workflow",
                "provenance": {"source_kind": "built_in"},
                "terminal_contract": (
                    {
                        "contract_id": contract.contract_id,
                        "relative_path": contract.relative_path,
                        "expected_schema_version": contract.expected_schema_version,
                    }
                    if declared_contract
                    else None
                ),
            }
        ],
    }
    read = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(activities, "_read_json_artifact", read)
    bound = await activities._bind_remediation_terminal_contract(request)
    assert bound.terminal_contract == (contract if declared_contract else None)
    if declared_contract:
        read.reset_mock()
        assert await activities._bind_remediation_terminal_contract(bound) == bound
        read.assert_not_awaited()
