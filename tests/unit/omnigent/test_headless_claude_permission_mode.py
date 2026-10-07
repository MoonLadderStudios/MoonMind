"""Workflow-step Claude sessions launch in an unattended permission mode.

A MoonMind workflow step has no human at the Claude terminal; the operator
authorized the work by launching the workflow. Without an explicit launch mode
Claude Code falls back to the account default (``auto``), whose classifier
refused the batch-pr-resolver helper as "Merge Without Review" and failed
mm:e7413769-f241-4695-af14-0723d9322cbc on every retry.
"""

from types import SimpleNamespace

import pytest

from moonmind.omnigent.bridge_store import WORKFLOW_LAUNCH_DEFAULTS
from moonmind.omnigent.codex_execution_decisions import bind_exact_host
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentResolvedTarget,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
)


def _request(session: dict | None = None) -> AgentExecutionRequest:
    omnigent: dict = {}
    if session is not None:
        omnigent["session"] = session
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm:wf",
        idempotencyKey="mm:wf:node-1",
        parameters={"omnigent": omnigent},
    )


def _bind_profile(
    request: AgentExecutionRequest, harness: str
) -> AgentExecutionRequest:
    return bind_exact_host(
        request,
        host_id="host-1",
        workspace_path="/workspaces/run",
        profile_authorization={},
        harness=harness,
        agent_name=f"{harness}-ui",
    )


def _bind_generic(
    request: AgentExecutionRequest, harness: str
) -> AgentExecutionRequest:
    plan = SimpleNamespace(
        planRef="plan-1",
        payload=SimpleNamespace(
            agentSource={"upstreamId": "agent-1"},
            endpointRef="endpoint-1",
            harnessId=harness,
            hostClassRef="host-class-1",
            launchPolicyRef="launch-1",
            capturePolicy={},
            modelConfig=SimpleNamespace(qualifiedId="model-1", effort="high"),
        ),
    )
    binding = SimpleNamespace(
        bindingId="binding-1",
        providerLeases={},
        hostBindingRef="host-binding-1",
        hostLeaseRef="host-lease-1",
    )
    realizer = object.__new__(GenericOmnigentHostRealizer)
    return realizer._bind_exact_host(
        request,
        plan,
        {"omnigentHostId": "host-1", "workspacePath": "/workspaces/run"},
        binding,
    )


def _launch_args(bound: AgentExecutionRequest) -> list[str]:
    payload = build_omnigent_session_create_payload(
        request=bound,
        selection=build_omnigent_selection(bound),
        target=OmnigentResolvedTarget(agent_id="agent-1", source="agent_id"),
        launch_defaults=WORKFLOW_LAUNCH_DEFAULTS,
    )
    return payload["terminal_launch_args"]


_BINDERS = pytest.mark.parametrize("bind", [_bind_profile, _bind_generic])


@_BINDERS
def test_claude_workflow_session_launches_without_permission_prompts(bind):
    assert _launch_args(bind(_request(), "claude-native")) == [
        "--permission-mode",
        "bypassPermissions",
    ]


@_BINDERS
@pytest.mark.parametrize(
    "explicit",
    [
        ["--permission-mode", "plan"],
        ["--permission-mode=acceptEdits"],
        ["--dangerously-skip-permissions"],
    ],
)
def test_explicit_claude_permission_choice_is_preserved(bind, explicit):
    request = _request({"terminalLaunchArgs": ["--verbose", *explicit]})

    assert _launch_args(bind(request, "claude-native")) == ["--verbose", *explicit]


@_BINDERS
def test_unattended_mode_is_added_beside_other_launch_args(bind):
    request = _request({"terminalLaunchArgs": ["--verbose"]})

    assert _launch_args(bind(request, "claude-native")) == [
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]


@_BINDERS
@pytest.mark.parametrize("harness", ["codex-native", "opencode-native"])
def test_other_harnesses_receive_no_claude_flag(bind, harness):
    assert _launch_args(bind(_request(), harness)) == []


@_BINDERS
@pytest.mark.parametrize("invalid", ["--verbose", {"--verbose": True}, 7])
def test_invalid_launch_arguments_are_rejected_before_defaulting(bind, invalid):
    from moonmind.workflows.adapters.omnigent_agent_adapter import OmnigentAdapterError

    request = _request({"terminalLaunchArgs": invalid})
    with pytest.raises(OmnigentAdapterError, match="must be a string array"):
        _launch_args(bind(request, "claude-native"))


class _WorkerStopped(BaseException):
    """An execution worker disappears after the provider accepted creation."""


@pytest.mark.asyncio
@_BINDERS
@pytest.mark.parametrize("legacy", [True, False])
async def test_session_launch_retries_preserve_pre_upgrade_payload(
    bind, legacy, monkeypatch, tmp_path
):
    import copy

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base, OmnigentBridgeSession
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
    from moonmind.omnigent.execute import run_omnigent_execution

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(sessions)
    authored = _request({"terminalLaunchArgs": ["--verbose"]})
    bound = bind(authored, "claude-native")
    expected = ["--verbose"]
    if not legacy:
        expected += ["--permission-mode", "bypassPermissions"]
    provider_payloads = []

    if legacy:
        row = await store.get_or_create(
            request=bound,
            endpoint_ref="endpoint-1",
            agent_id="agent-1",
            agent_name=None,
            target_metadata={},
        )
        # A row written by the old worker has no launch-default decision. It
        # may have reached the provider even though it has no attached ID yet.
        async with sessions() as session:
            legacy_row = await session.get(OmnigentBridgeSession, row.bridge_session_id)
            legacy_row.metadata_ = {}
            await session.commit()
        old_payload = build_omnigent_session_create_payload(
            request=bound,
            selection=build_omnigent_selection(bound),
            target=OmnigentResolvedTarget(agent_id="agent-1", source="agent_id"),
        )
        old_payload["terminal_launch_args"] = ["--verbose"]
        old_payload["idempotency_key"] = bound.idempotency_key
        old_payload["labels"]["moonmind.issue"] = "MM-1059"
        provider_payloads.append(old_payload)

    class Client:
        def __init__(self, **kwargs):
            pass

        async def list_agents(self):
            return [{"id": "agent-1", "name": "claude-native-ui"}]

        async def create_session(self, payload):
            # The launch decision must already be durable before this effect.
            row = await store.get_existing(bound.idempotency_key)
            assert row is not None
            if not legacy:
                assert row.metadata_["workflowLaunchDefaults"] == {
                    "claude-native": ["--permission-mode", "bypassPermissions"]
                }
            assert payload["terminal_launch_args"] == expected
            if provider_payloads:
                assert payload == provider_payloads[0]
            provider_payloads.append(copy.deepcopy(payload))
            return {"id": "session-1"}

        async def get_session(self, session_id):
            raise _WorkerStopped()

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", Client)
    real_attach = store.attach_session

    async def crash_before_attach(*args, **kwargs):
        raise _WorkerStopped()

    monkeypatch.setattr(store, "attach_session", crash_before_attach)
    try:
        with pytest.raises(_WorkerStopped):
            await run_omnigent_execution(
                bound,
                run_store=store,
                artifact_gateway=LocalOmnigentArtifactGateway(
                    root=tmp_path / "capture"
                ),
            )
        assert (
            await store.get_existing(bound.idempotency_key)
        ).omnigent_session_id is None
        # A replacement worker's changed defaults cannot reinterpret this row.
        monkeypatch.setattr(
            "moonmind.omnigent.bridge_store.WORKFLOW_LAUNCH_DEFAULTS",
            {"claude-native": ["--permission-mode", "plan"]},
        )
        monkeypatch.setattr(store, "attach_session", real_attach)
        with pytest.raises(_WorkerStopped):
            await run_omnigent_execution(
                bind(authored, "claude-native"),
                run_store=store,
                artifact_gateway=LocalOmnigentArtifactGateway(
                    root=tmp_path / "capture"
                ),
            )
        assert (
            await store.get_existing(bound.idempotency_key)
        ).omnigent_session_id == "session-1"
        assert len(provider_payloads) == (3 if legacy else 2)
        assert authored.parameters["omnigent"]["session"]["terminalLaunchArgs"] == [
            "--verbose"
        ]
    finally:
        await engine.dispose()


@_BINDERS
def test_session_payload_without_saved_defaults_preserves_authored_arguments(bind):
    bound = bind(_request({"terminalLaunchArgs": ["--verbose"]}), "claude-native")
    payload = build_omnigent_session_create_payload(
        request=bound,
        selection=build_omnigent_selection(bound),
        target=OmnigentResolvedTarget(agent_id="agent-1", source="agent_id"),
    )
    assert payload["terminal_launch_args"] == ["--verbose"]
