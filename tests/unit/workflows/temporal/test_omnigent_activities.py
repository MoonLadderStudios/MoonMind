import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent import execute as omnigent_execute_module
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import (
    OmnigentBridgeSessionStore,
    _canonical_first_message_frontier,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities import (
    omnigent_activities as omnigent_activities_module,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _checkpoint_branch_from_request,
    _checkpoint_recovery_dimensions,
    _checkpoint_recovery_from_request,
    _resolve_live_recovery_authority,
    omnigent_execute_activity,
)


@pytest.mark.parametrize(
    ("dimension", "attribute", "value"),
    [
        ("provider", "provider", "omnigent"),
        ("instructionDigest", "instruction_digest", "sha256:instructions"),
        ("runtimeId", "runtime_id", "omnigent"),
        ("model", "model", "gpt-5.6"),
        ("effort", "effort", "high"),
        ("compatibilityProfile", "compatibility_profile", "omnigent-bridge-v1"),
        ("providerProfileId", "provider_profile_id", "profile-2"),
        ("launchPolicyRef", "policy_ref", "artifact://policy/2"),
        ("imageManifestRef", "image_manifest_ref", "image://omnigent@sha256:2"),
        ("compatibilityRef", "compatibility_ref", "artifact://compatibility/2"),
        ("repository", "repository", "MoonLadderStudios/MoonMind"),
        ("repositoryBranch", "branch", "feature/changed"),
        ("workspaceRef", "workspace_ref", "workspace-intent:sha256:2"),
        ("publishMode", "publication_mode", "pull_request"),
        ("skillRef", "skill_ref", "remediate-issue"),
        ("runtimeAuthorityRef", "runtime_authority_ref", "omnigent-launch:sha256:2"),
        ("intentDigest", "intent_digest", "sha256:intent-2"),
    ],
)
def test_checkpoint_recovery_compiles_every_requested_immutable_dimension(
    dimension, attribute, value
) -> None:
    requested = {
        "provider": "omnigent",
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "gpt-5.6",
        "effort": "high",
        "compatibilityProfile": "omnigent-bridge-v1",
        "providerProfileId": "profile-2",
        "launchPolicyRef": "artifact://policy/2",
        "imageManifestRef": "image://omnigent@sha256:2",
        "compatibilityRef": "artifact://compatibility/2",
        "repository": "MoonLadderStudios/MoonMind",
        "repositoryBranch": "feature/changed",
        "workspaceRef": "workspace-intent:sha256:2",
        "publishMode": "none",
        "skillRef": "remediate-issue",
        "runtimeAuthorityRef": "omnigent-launch:sha256:2",
        "intentDigest": "sha256:intent-2",
    }
    requested[dimension] = value
    dimensions = _checkpoint_recovery_dimensions(
        {"immutableRequested": requested}
    )
    assert getattr(dimensions, attribute) == value


def test_checkpoint_recovery_dimensions_fail_closed_when_incomplete() -> None:
    with pytest.raises(ValueError, match="immutable authority is incomplete"):
        _checkpoint_recovery_dimensions(
            {"immutableRequested": {"runtimeId": "omnigent"}}
        )


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_omnigent_execute_activity_delegates(
    mock_run, monkeypatch: pytest.MonkeyPatch
):
    expected_result = AgentRunResult(summary="done", output_refs=[])
    heartbeats: list[tuple[object, ...]] = []

    async def delayed_run(*_args, **_kwargs):
        omnigent_execute_module._safe_heartbeat(  # type: ignore[attr-defined]
            {"omnigentSessionId": "session-1", "eventsCaptured": 1}
        )
        await asyncio.sleep(0.035)
        return expected_result

    mock_run.side_effect = delayed_run
    monkeypatch.setattr(
        omnigent_execute_module,
        "_ACTIVITY_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="123",
        idempotencyKey="key",
    )

    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: heartbeats.append(details)
    result = await env.run(omnigent_execute_activity, req)

    assert result == expected_result
    mock_run.assert_called_once()
    called_req = mock_run.call_args.args[0]
    assert called_req == req
    assert isinstance(mock_run.call_args.kwargs["artifact_gateway"], LocalOmnigentArtifactGateway)
    assert isinstance(mock_run.call_args.kwargs["run_store"], OmnigentBridgeSessionStore)
    assert len(heartbeats) >= 2
    heartbeat_payloads = [
        detail
        for callback_args in heartbeats
        for detail in callback_args
        if isinstance(detail, dict)
    ]
    assert any(payload.get("activityAlive") is True for payload in heartbeat_payloads)
    assert all(
        payload.get("omnigentSessionId") == "session-1"
        for payload in heartbeat_payloads
        if payload.get("activityAlive") is True
        and payload.get("eventsCaptured") == 1
    )


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
        checkpointRecovery={
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
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


def test_checkpoint_branch_request_requires_explicit_action_and_new_boundary() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="branch-workflow",
        idempotencyKey="branch-turn-1",
        checkpointRecovery={
            "recoveryAction": "branch_required",
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        },
    )

    parsed = _checkpoint_branch_from_request(request)

    assert parsed is not None
    parsed_checkpoint, candidate = parsed
    assert parsed_checkpoint == checkpoint
    assert candidate.checkpoint_ref == checkpoint.workspace_checkpoint_ref


def test_checkpoint_branch_request_waits_for_canonical_typed_decision() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    source = {
        "instructionDigest": "sha256:old",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": checkpoint.provider_profile_id,
        "launchPolicyRef": checkpoint.launch_policy_ref,
        "repositoryBranch": "main",
        "publishMode": "none",
    }
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="branch-workflow",
        idempotencyKey="branch-turn-derived",
        checkpointRecovery={
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            "immutableSource": source,
            "immutableRequested": {
                **source,
                "instructionDigest": "sha256:new",
            },
            "liveReattachAvailable": True,
            "coldRestoreAvailable": True,
        },
    )

    assert _checkpoint_branch_from_request(request) is None
    assert "recoveryDecision" not in request.checkpoint_recovery


def test_checkpoint_branch_request_rejects_source_idempotency_boundary() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="branch-workflow",
        idempotencyKey=checkpoint.idempotency_key,
        checkpointRecovery={
            "recoveryAction": "branch_required",
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        },
    )

    with pytest.raises(ValueError, match="new idempotency key"):
        _checkpoint_branch_from_request(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "canonical_cursor",
        "frontier_matches",
        "current_generation_offset",
        "expected_cursor",
        "expected_first_message",
        "expected_generation",
    ),
    [
        ("7", True, 0, True, True, True),
        (None, True, 0, False, True, True),
        ("3", True, 0, False, True, True),
        ("7", False, 0, True, False, True),
        ("7", True, 1, True, True, False),
    ],
)
async def test_live_recovery_authority_requires_matching_current_records(
    canonical_cursor,
    frontier_matches,
    current_generation_offset,
    expected_cursor,
    expected_first_message,
    expected_generation,
) -> None:
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
    provider = SimpleNamespace(
        credential_generation=(
            checkpoint.credential_generation + current_generation_offset
        )
    )
    provider_lease = SimpleNamespace(
        lease_id="provider-lease",
        owner_id="owner-1",
        idempotency_key=checkpoint.idempotency_key,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            value = self.value if isinstance(self.value, list) else [self.value]
            return SimpleNamespace(all=lambda: value)

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
            return Result(provider_lease)

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
    canonical = SimpleNamespace(
        host_binding_ref=checkpoint.host_binding_ref,
        host_lease_ref=checkpoint.host_lease_ref,
        provider_profile_id=checkpoint.provider_profile_id,
        provider_session_ref="session-1",
        provider_event_cursor=canonical_cursor,
        snapshot_frontier=(
            _canonical_first_message_frontier(
                "message-1", checkpoint.first_message_digest
            )
            if frontier_matches
            else "sha256:conflicting-first-message"
        ),
        is_terminal=False,
        cleanup_state="pending",
    )
    authority = await _resolve_live_recovery_authority(
        checkpoint=checkpoint,
        session_factory=Session,
        host_repository=SimpleNamespace(
            get_host_lease=lambda _lease_id: _async_value(host)
        ),
        run_store=SimpleNamespace(
            get_canonical_session=lambda _bridge_id: _async_value(canonical)
        ),
    )

    assert authority["provider_lease"]["active"] is True
    assert authority["host_registered"] is True
    assert authority["session_valid"] is expected_cursor
    assert authority["cursor_present"] is expected_cursor
    assert authority["first_message_consistent"] is expected_first_message
    assert (
        authority["current_credential_generation"]
        == checkpoint.credential_generation
    ) is expected_generation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lease_rows",
    [
        [],
        [
            SimpleNamespace(
                lease_id="provider-lease",
                owner_id="owner-1",
                idempotency_key="wrong-boundary",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        ],
        [
            SimpleNamespace(
                lease_id="provider-lease",
                owner_id="owner-1",
                idempotency_key="checkpoint-key",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
            SimpleNamespace(
                lease_id="provider-lease",
                owner_id="owner-2",
                idempotency_key="checkpoint-key",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
        ],
    ],
)
async def test_live_recovery_authority_fails_closed_for_ambiguous_or_mismatched_lease(
    lease_rows,
) -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint().model_copy(
        update={
            "idempotency_key": "checkpoint-key",
            "provider_lease_ref": "provider-lease",
            "host_lease_ref": "host-lease",
            "omnigent_host_id": "host-1",
            "omnigent_session_id": "session-1",
            "last_bridge_event_cursor": "4",
            "first_message_id": "message-1",
            "first_message_digest": "sha256:" + "a" * 64,
        }
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalars(self):
            return SimpleNamespace(all=lambda: self.value)

        def scalar(self):
            return self.value

    class Session:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args):
            return SimpleNamespace(
                credential_generation=checkpoint.credential_generation
            )

        async def execute(self, _query):
            self.calls += 1
            return Result(lease_rows)

    authority = await _resolve_live_recovery_authority(
        checkpoint=checkpoint,
        session_factory=Session,
        host_repository=SimpleNamespace(get_host_lease=lambda _ref: _async_value(None)),
        run_store=SimpleNamespace(
            get_canonical_session=lambda _ref: _async_value(None)
        ),
    )

    assert authority["provider_lease"] is None or not authority["provider_lease"][
        "active"
    ]


async def _async_value(value):
    return value
