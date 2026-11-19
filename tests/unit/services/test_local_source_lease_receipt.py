"""A local source result freezes only under its original live pool grant."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services import native_pool_execution_plan as service
from moonmind.workflows.temporal.runtime.initial_repository_source import (
    intent_digest,
    source_intent,
)
from tests.unit.services.test_native_pool_execution_plan import request as make_request


def state():
    request = make_request()
    request.canonical_repository_workspace_handoff = True
    request.parameters = {"publishMode": "none"}
    request.workspace_spec = {
        "workspaceSource": {"kind": "repository"},
        "repository": "/absent/source",
        "branch": "main",
    }
    intent = source_intent(request)
    grant = {
        "version": 1,
        "requestDigest": service._request_digest(request, None),
        "intent": intent,
        "sourceIntentDigest": intent_digest(intent),
        "authority": {"workspaceId": "source"},
        "result": None,
    }
    lease = SimpleNamespace(safe_metadata_json={"initialRepositorySource": grant})
    return request, lease, grant


@asynccontextmanager
async def session_factory():
    yield SimpleNamespace(commit=AsyncMock())


@pytest.mark.asyncio
async def test_saved_source_receipt_returns_without_reopening_vanished_source(
    monkeypatch, tmp_path
):
    request, lease, grant = state()
    result = {
        "artifactRef": "artifact:art_frozen",
        "contentDigest": "sha256:" + "a" * 64,
        "sourceIntentDigest": grant["sourceIntentDigest"],
    }
    grant["result"] = result
    monkeypatch.setattr(
        service, "_locked_pool_request", AsyncMock(return_value=(request, lease))
    )
    capture = AsyncMock(
        side_effect=AssertionError("a frozen source must not be read again")
    )
    observed = await service.export_selected_pool_source(
        session_factory=session_factory,
        runtime_id="codex_cli",
        payload={},
        workspace_root=tmp_path,
        artifact_service=None,
        capture=capture,
    )
    assert observed == result
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_accepted_source_result_wins_concurrent_export(
    monkeypatch, tmp_path
):
    request, lease, grant = state()
    first = {
        "artifactRef": "artifact:art_first",
        "contentDigest": "sha256:" + "a" * 64,
        "sourceIntentDigest": grant["sourceIntentDigest"],
    }
    second = {
        **first,
        "artifactRef": "artifact:art_second",
        "contentDigest": "sha256:" + "b" * 64,
    }
    monkeypatch.setattr(
        service, "_locked_pool_request", AsyncMock(return_value=(request, lease))
    )

    async def capture(**kwargs):
        lease.safe_metadata_json = {
            "initialRepositorySource": {**deepcopy(grant), "result": first}
        }
        return second

    assert (
        await service.export_selected_pool_source(
            session_factory=session_factory,
            runtime_id="codex_cli",
            payload={},
            workspace_root=tmp_path,
            artifact_service=None,
            capture=capture,
        )
        == first
    )


@pytest.mark.asyncio
async def test_released_grant_cannot_accept_export_result(monkeypatch, tmp_path):
    request, lease, grant = state()
    check = AsyncMock(
        side_effect=[
            (request, lease),
            ValueError("pool plan handoff was fenced: released"),
        ]
    )
    monkeypatch.setattr(service, "_locked_pool_request", check)
    capture = AsyncMock(
        return_value={
            "artifactRef": "artifact:art_new",
            "contentDigest": "sha256:" + "a" * 64,
            "sourceIntentDigest": grant["sourceIntentDigest"],
        }
    )
    with pytest.raises(ValueError, match="fenced"):
        await service.export_selected_pool_source(
            session_factory=session_factory,
            runtime_id="codex_cli",
            payload={},
            workspace_root=tmp_path,
            artifact_service=None,
            capture=capture,
        )
    assert lease.safe_metadata_json["initialRepositorySource"]["result"] is None


@pytest.mark.parametrize(
    "field,value",
    [("repository", "/other/source"), ("branch", "other"), ("commitSha", "a" * 40)],
)
def test_source_grant_cannot_replace_frozen_authored_source(field, value):
    request, _, _ = state()
    frozen = {"draft": {"workspaceSpec": deepcopy(request.workspace_spec)}}
    if field == "commitSha":
        request.workspace_spec["workspaceSource"]["repositoryTarget"] = {
            "provider": "git",
            "repository": {"name": "/absent/source"},
            "revision": {"kind": "git_commit", "commitSha": value},
        }
    else:
        request.workspace_spec[field] = value
    with pytest.raises(ValueError, match="frozen"):
        service._validate_local_source_original_input(request, frozen)


def test_literal_node_source_is_checked_against_frozen_step():
    request, _, _ = state()
    frozen = {
        "draft": {
            "workflow": {
                "steps": [
                    {
                        "id": "work",
                        "inputs": {"workspaceSpec": deepcopy(request.workspace_spec)},
                    }
                ]
            }
        }
    }
    service._validate_local_source_original_input(request, frozen)


@pytest.mark.asyncio
async def test_new_source_capability_refusal_is_a_durable_native_decision(
    monkeypatch, tmp_path
):
    from moonmind.workflows.temporal.runtime.initial_repository_source import (
        LocalRepositorySourceUnavailable,
    )

    request, lease, _ = state()
    monkeypatch.setattr(
        service, "_locked_pool_request", AsyncMock(return_value=(request, lease))
    )
    capture = AsyncMock(
        side_effect=LocalRepositorySourceUnavailable("unsupported native feature")
    )
    result = await service.export_selected_pool_source(
        session_factory=session_factory,
        runtime_id="codex_cli",
        payload={},
        workspace_root=tmp_path,
        artifact_service=None,
        capture=capture,
    )
    assert result == {
        "disposition": "native",
        "reason": "local_source_requires_native_owner",
    }
    assert (
        await service.export_selected_pool_source(
            session_factory=session_factory,
            runtime_id="codex_cli",
            payload={},
            workspace_root=tmp_path,
            artifact_service=None,
            capture=capture,
        )
        == result
    )
    assert capture.await_count == 1


@pytest.mark.asyncio
async def test_verified_recorded_source_bypasses_new_source_preflight(monkeypatch):
    """A loader-verified plan checkpoint is authority, not the old filesystem.

    Canonical retries normally replace repository kind with checkpoint kind.
    This defensive accepted repository-kind shape must still retain its source.
    """
    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
    )
    from moonmind.workflows.temporal.runtime import local_repository_authority
    from tests.unit.services.test_native_pool_execution_plan import plan, resolution

    request, _, grant = state()
    frozen_source = {
        "artifactRef": "artifact:art_frozen",
        "contentDigest": "sha256:" + "a" * 64,
        "sourceIntentDigest": grant["sourceIntentDigest"],
    }
    frozen_plan = plan(tier_resolution=resolution())
    frozen_plan = create_execution_plan_envelope(
        frozen_plan.payload.model_copy(
            update={"resolvedTools": {"initialRepositorySource": frozen_source}}
        )
    )
    frozen_profile = {"providerProfileRef": "p1", "frozen": "unchanged"}
    recorded = service._RecordedPoolStep(frozen_plan, frozen_profile, resolution(), {})
    lease = SimpleNamespace(
        safe_metadata_json={}, execution_plan_ref=None, credential_generation=1
    )
    profile = SimpleNamespace(
        enabled=True,
        auth_state="connected",
        runtime_id="codex_cli",
        credential_generation=1,
    )
    canonical = SimpleNamespace(
        memo={"task_input_snapshot_ref": "artifact:art_input"},
        parameters={},
        run_id="parent-run",
    )

    async def get(model, key):
        return (
            canonical if model is service.TemporalExecutionCanonicalRecord else profile
        )

    session = SimpleNamespace(get=AsyncMock(side_effect=get))
    monkeypatch.setattr(
        service, "_locked_pool_request", AsyncMock(return_value=(request, lease))
    )
    monkeypatch.setattr(service, "TemporalArtifactRepository", lambda _: None)
    monkeypatch.setattr(
        service,
        "TemporalArtifactService",
        lambda *a, **kw: SimpleNamespace(
            read=AsyncMock(
                return_value=(SimpleNamespace(created_by_principal="system"), b"{}")
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "resolve_runtime_target_selection",
        lambda **kw: SimpleNamespace(available=True, path_class="generic_omnigent"),
    )
    monkeypatch.setattr(service, "profile_harness_id", lambda _: "codex-native")
    monkeypatch.setattr(
        service, "_recorded_step_plan", AsyncMock(return_value=recorded)
    )
    monkeypatch.setattr(
        service, "_validate_local_source_original_input", lambda *a: True
    )
    source_read = AsyncMock(
        side_effect=AssertionError("recorded source must not be reopened")
    )
    monkeypatch.setattr(
        local_repository_authority, "resolve_source_authority", source_read
    )
    monkeypatch.setattr(
        service,
        "compile_agent_profile_snapshot_parameters",
        lambda parameters, **kw: dict(parameters),
    )

    class FrozenCompileReached(Exception):
        pass

    async def compile(**kwargs):
        assert kwargs["_initial_repository_source_snapshot"] == frozen_source
        assert "_prepare_local_source_digest" not in kwargs
        assert kwargs["agent_profile_snapshot"] is frozen_profile
        assert (
            kwargs["initial_parameters"]["model"]
            == frozen_plan.payload.modelConfig.qualifiedId
        )
        raise FrozenCompileReached

    monkeypatch.setattr(service, "compile_and_persist_execution_plan", compile)
    with pytest.raises(FrozenCompileReached):
        await service.prepare_selected_pool_plan(
            session,
            runtime_id="codex_cli",
            payload={
                "owner_id": "child",
                "runId": "child-run",
                "profile_id": "p1",
                "localSourceHandoff": True,
            },
            client=object(),
        )
    source_read.assert_not_awaited()
