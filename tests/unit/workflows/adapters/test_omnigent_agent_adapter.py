"""MM-990 tests for Omnigent request validation and target resolution."""

from __future__ import annotations

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAdapterError,
    OmnigentExternalAdapter,
    OmnigentResolvedTarget,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
    resolve_omnigent_target,
)


def _request(**overrides: object) -> AgentExecutionRequest:
    payload = {
        "agentKind": "external",
        "agentId": "omnigent",
        "executionProfileRef": "profile:test",
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1",
        "parameters": {"title": "MM-990 task", "omnigent": {}},
    }
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


def test_omnigent_selection_fields_must_be_nested_under_parameters_omnigent() -> None:
    req = _request(parameters={"agent": {"agentId": "ag_top"}, "omnigent": {}})

    with pytest.raises(OmnigentAdapterError) as exc:
        build_omnigent_selection(req)

    assert exc.value.failure_class == "user_error"
    assert "parameters.omnigent" in str(exc.value)


def test_omnigent_selection_does_not_change_top_level_agent_identity() -> None:
    req = _request(
        agentId="omnigent_codex",
        parameters={"omnigent": {"agent": {"agentId": "ag_1"}}},
    )

    with pytest.raises(OmnigentAdapterError) as exc:
        build_omnigent_selection(req)

    assert exc.value.failure_class == "user_error"
    assert "agentId='omnigent'" in str(exc.value)


def test_managed_session_rejects_host_id_and_local_workspace() -> None:
    req = _request(
        parameters={
            "omnigent": {
                "session": {
                    "hostType": "managed",
                    "hostId": "host_1",
                    "workspace": "/work/repo",
                }
            }
        }
    )

    with pytest.raises(OmnigentAdapterError, match="hostId"):
        build_omnigent_selection(req)

    req = _request(
        parameters={
            "omnigent": {
                "session": {
                    "hostType": "managed",
                    "workspace": "/work/repo",
                }
            }
        }
    )

    with pytest.raises(OmnigentAdapterError, match="git repository URL"):
        build_omnigent_selection(req)


def test_managed_repository_task_normalizes_repo_url_and_branch() -> None:
    req = _request(
        workspaceSpec={
            "repository": "https://github.com/MoonLadderStudios/MoonMind.git",
            "branch": "feature/mm-990",
        },
        parameters={
            "title": "Edit repo",
            "omnigent": {"session": {"hostType": "managed"}},
        },
    )

    selection = build_omnigent_selection(req)

    assert (
        selection.session.workspace
        == "https://github.com/MoonLadderStudios/MoonMind.git#feature/mm-990"
    )


def test_managed_repository_task_uses_starting_branch_not_target_branch() -> None:
    req = _request(
        workspaceSpec={
            "repository": "https://github.com/MoonLadderStudios/MoonMind.git",
            "startingBranch": "main",
            "targetBranch": "feature/mm-990",
        },
        parameters={
            "title": "Edit repo",
            "omnigent": {"session": {"hostType": "managed"}},
        },
    )

    selection = build_omnigent_selection(req)

    assert (
        selection.session.workspace
        == "https://github.com/MoonLadderStudios/MoonMind.git#main"
    )


def test_external_session_requires_host_id_absolute_path_and_rejects_repo_url() -> None:
    missing = _request(
        parameters={
            "omnigent": {
                "session": {"hostType": "external", "workspace": "/workspace/repo"}
            }
        }
    )
    with pytest.raises(OmnigentAdapterError, match="hostId"):
        build_omnigent_selection(missing)

    relative = _request(
        parameters={
            "omnigent": {
                "session": {
                    "hostType": "external",
                    "hostId": "host_1",
                    "workspace": "workspace/repo",
                }
            }
        }
    )
    with pytest.raises(OmnigentAdapterError, match="absolute host path"):
        build_omnigent_selection(relative)

    repo_url = _request(
        parameters={
            "omnigent": {
                "session": {
                    "hostType": "external",
                    "hostId": "host_1",
                    "workspace": "https://github.com/org/repo.git",
                }
            }
        }
    )
    with pytest.raises(OmnigentAdapterError, match="not a repository URL"):
        build_omnigent_selection(repo_url)


@pytest.mark.asyncio
async def test_target_resolution_order() -> None:
    agents = [{"id": "ag_named", "name": "codex-native-ui"}]
    calls: list[str] = []

    async def list_agents() -> list[dict[str, str]]:
        calls.append("list")
        return agents

    async def upload(bundle_ref: str) -> dict[str, str]:
        calls.append(f"upload:{bundle_ref}")
        return {"id": "ag_bundle"}

    direct = build_omnigent_selection(
        _request(parameters={"omnigent": {"agent": {"agentId": "ag_direct"}}})
    )
    assert (await resolve_omnigent_target(
        direct,
        list_agents=list_agents,
        upload_agent_bundle=upload,
        default_agent_name=None,
    )).source == "agent_id"
    assert calls == []

    named = build_omnigent_selection(
        _request(parameters={"omnigent": {"agent": {"agentName": "codex-native-ui"}}})
    )
    assert (await resolve_omnigent_target(
        named,
        list_agents=list_agents,
        upload_agent_bundle=upload,
        default_agent_name=None,
    )).agent_id == "ag_named"

    bundle = build_omnigent_selection(
        _request(parameters={"omnigent": {"agent": {"bundleRef": "artifact://bundle"}}})
    )
    assert (await resolve_omnigent_target(
        bundle,
        list_agents=list_agents,
        upload_agent_bundle=upload,
        default_agent_name=None,
    )).source == "bundle_ref"

    default = build_omnigent_selection(_request())
    assert (await resolve_omnigent_target(
        default,
        list_agents=list_agents,
        upload_agent_bundle=upload,
        default_agent_name="codex-native-ui",
    )).source == "default_agent_name"

    with pytest.raises(OmnigentAdapterError) as exc:
        await resolve_omnigent_target(
            default,
            list_agents=list_agents,
            upload_agent_bundle=upload,
            default_agent_name=None,
        )
    assert exc.value.failure_class == "integration_error"


def test_session_create_payload_host_id_rules() -> None:
    managed_req = _request(
        workspaceSpec={
            "repository": "https://github.com/org/repo.git",
            "branch": "main",
        }
    )
    managed_selection = build_omnigent_selection(managed_req)
    payload = build_omnigent_session_create_payload(
        request=managed_req,
        selection=managed_selection,
        target=OmnigentResolvedTarget(agent_id="ag_1", source="agent_id"),
    )

    assert payload["workspace"] == "https://github.com/org/repo.git#main"
    assert payload["host_type"] == "managed"
    assert "host_id" not in payload

    external_req = _request(
        parameters={
            "omnigent": {
                "session": {
                    "hostType": "external",
                    "hostId": "host_1",
                    "workspace": "/workspace/repo",
                }
            }
        }
    )
    external_selection = build_omnigent_selection(external_req)
    payload = build_omnigent_session_create_payload(
        request=external_req,
        selection=external_selection,
        target=OmnigentResolvedTarget(agent_id="ag_1", source="agent_id"),
    )
    assert payload["host_id"] == "host_1"


def test_omnigent_external_adapter_capability_is_streaming_gateway() -> None:
    adapter = OmnigentExternalAdapter()
    cap = adapter.provider_capability

    assert cap.provider_name == "omnigent"
    assert cap.execution_style == "streaming_gateway"
    assert cap.supports_callbacks is False
    assert cap.supports_cancel is False
    assert cap.supports_result_fetch is False


@pytest.mark.asyncio
async def test_omnigent_adapter_accepts_only_canonical_agent_id() -> None:
    adapter = OmnigentExternalAdapter()

    with pytest.raises(RuntimeError, match="integration.omnigent.execute"):
        await adapter.start(_request())

    for alias in (
        "omnigent_session",
        "omnigent_claude",
        "omnigent_codex",
        "omnigent_polly",
    ):
        with pytest.raises(ValueError, match="only supports agent_id"):
            await adapter.start(_request(agentId=alias))


@pytest.mark.asyncio
async def test_omnigent_adapter_rejects_non_external_agent_kind() -> None:
    adapter = OmnigentExternalAdapter()
    request = _request(agentKind="managed")

    with pytest.raises(ValueError, match="only supports external agent_kind"):
        await adapter.start(request)


@pytest.mark.asyncio
async def test_omnigent_unused_polling_hooks_fail_loudly() -> None:
    adapter = OmnigentExternalAdapter()

    with pytest.raises(RuntimeError, match="status polling is unused"):
        await adapter.do_status("run-1")
    with pytest.raises(RuntimeError, match="fetch_result is unused"):
        await adapter.do_fetch_result("run-1")
    with pytest.raises(RuntimeError, match="activity cancellation"):
        await adapter.do_cancel("run-1")
