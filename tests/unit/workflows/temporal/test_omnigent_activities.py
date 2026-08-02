import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent import execute as omnigent_execute_module
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities import (
    omnigent_activities as omnigent_activities_module,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _checkpoint_recovery_from_request,
    _resolve_live_recovery_authority,
    omnigent_execute_activity,
)


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_omnigent_execute_activity_delegates(mock_run):
    expected_result = AgentRunResult(summary="done", output_refs=[])
    mock_run.return_value = expected_result

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="123",
        idempotencyKey="key",
    )

    env = ActivityEnvironment()
    result = await env.run(omnigent_execute_activity, req)

    assert result == expected_result
    mock_run.assert_called_once()
    called_req = mock_run.call_args.args[0]
    assert called_req == req
    assert isinstance(mock_run.call_args.kwargs["artifact_gateway"], LocalOmnigentArtifactGateway)
    assert isinstance(mock_run.call_args.kwargs["run_store"], OmnigentBridgeSessionStore)


def test_omnigent_execution_path_does_not_use_managed_github_broker() -> None:
    """Omnigent is an external-agent adapter, not a managed runtime launcher."""

    source = "\n".join(
        [
            inspect.getsource(omnigent_activities_module),
            inspect.getsource(omnigent_execute_module),
        ]
    )

    for disallowed in (
        "github_auth_broker",
        "GitHubAuthBroker",
        "build_github_socket_path",
        "render_gh_wrapper_script",
        "render_git_credential_helper_script",
        "GITHUB_TOKEN",
    ):
        assert disallowed not in source


def test_checkpoint_recovery_request_builds_validated_candidate_workspace() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="recovery-workflow",
        idempotencyKey="recovery-step",
        parameters={
            "checkpointRecovery": {
                "omnigentCheckpoint": checkpoint.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                )
            }
        },
    )

    parsed = _checkpoint_recovery_from_request(request)

    assert parsed is not None
    parsed_checkpoint, candidate = parsed
    assert parsed_checkpoint == checkpoint
    assert candidate.loop_id == (
        f"{checkpoint.workflow_id}:{checkpoint.logical_step_id}"
    )
    assert candidate.head_ref == checkpoint.head_ref
    assert candidate.checkpoint_ref == checkpoint.workspace_checkpoint_ref


@pytest.mark.asyncio
async def test_live_recovery_authority_requires_matching_current_records() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint().model_copy(
        update={
            "provider_lease_ref": "provider-lease",
            "host_lease_ref": "host-lease",
            "omnigent_host_id": "host-1",
            "omnigent_session_id": "session-1",
            "last_bridge_event_cursor": "4",
            "first_message_id": "message-1",
            "first_message_digest": "sha256:" + "a" * 64,
        }
    )
    provider = SimpleNamespace(credential_generation=checkpoint.credential_generation)
    provider_lease = SimpleNamespace(
        lease_id="provider-lease",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar(self):
            return self.value

    class Session:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args):
            return provider

        async def execute(self, _query):
            self.calls += 1
            return Result(provider_lease if self.calls == 1 else 7)

    host = SimpleNamespace(
        omnigent_host_id="host-1",
        omnigent_session_id="session-1",
        bridge_session_id=checkpoint.bridge_session_id,
        status="assigned",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        model_dump=lambda **_kwargs: {
            "leaseId": "host-lease",
            "status": "assigned",
            "credentialGeneration": checkpoint.credential_generation,
        },
    )
    bridge = SimpleNamespace(
        omnigent_host_id="host-1",
        omnigent_session_id="session-1",
        status="active",
        first_message_digest=checkpoint.first_message_digest,
        first_message_item_id="message-1",
        first_message_pending_id=None,
        first_message_state="posted",
    )
    authority = await _resolve_live_recovery_authority(
        checkpoint=checkpoint,
        session_factory=Session,
        host_repository=SimpleNamespace(
            get_host_lease=lambda _lease_id: _async_value(host)
        ),
        run_store=SimpleNamespace(
            get_bridge_session=lambda _bridge_id: _async_value(bridge)
        ),
    )

    assert authority["provider_lease"]["active"] is True
    assert authority["host_registered"] is True
    assert authority["session_valid"] is True
    assert authority["first_message_consistent"] is True
    assert (
        authority["current_credential_generation"]
        == checkpoint.credential_generation
    )


async def _async_value(value):
    return value
