"""An authored process HOME is separate from enrolled Claude authentication."""

import copy
import hashlib
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.materializers import (
    load_provider_configuration_snapshot,
    provider_configuration_snapshot_bytes,
    snapshot_provider_configuration,
    untranslated_oauth_launch_options,
)
from moonmind.provider_profiles import claude_configuration as configuration
from tests.unit.omnigent.test_oauth_private_configuration import claude_oauth_profile


def profile_with_process_home():
    profile = claude_oauth_profile()
    profile.home_path_overrides["HOME"] = "{{runtime_support_dir}}/process-home"
    profile.file_templates.append(
        {
            "path": "{{runtime_support_dir}}/process-home/NOTES.md",
            "format": "text",
            "permissions": "0600",
            "merge_strategy": "append",
            "content_template": "Work in {{workspace_path}}.\n",
        }
    )
    return profile


def test_process_home_configuration_is_frozen_separately_from_enrolled_files():
    profile = profile_with_process_home()
    assert untranslated_oauth_launch_options(profile) is None
    selected = snapshot_provider_configuration(profile)
    frozen = copy.deepcopy(selected.claudeConfiguration)
    bindings = {
        "primary-model": SimpleNamespace(
            providerProfileRef=profile.profile_id, materializerRef="claude-oauth-home@1"
        )
    }
    content = provider_configuration_snapshot_bytes(
        bindings, {"primary-model": selected}
    )
    profile.home_path_overrides["HOME"] = "{{runtime_support_dir}}/later-home"
    profile.file_templates.clear()
    loaded = load_provider_configuration_snapshot(
        content,
        SimpleNamespace(contentDigest="sha256:" + hashlib.sha256(content).hexdigest()),
        bindings,
    )
    assert loaded["primary-model"].claudeConfiguration == frozen
    files = configuration.render_oauth_files(
        frozen, runtime_support_dir="/support", workspace_path="/work"
    )
    assert "NOTES.md" not in files
    assert "settings.json" in files


def test_private_process_home_is_initialized_once_and_isolated(tmp_path):
    profile = profile_with_process_home()
    config, reason = configuration.oauth_configuration_for_profile(profile)
    assert config is not None, reason
    for sibling in ("one", "two"):
        root = tmp_path / sibling
        root.mkdir()
        for _ in range(2):
            home = configuration.materialize_process_home(
                config, runtime_support_dir=root, workspace_path="/work/" + sibling
            )
        assert home == str(root / "process-home")
        assert (
            root / "process-home/NOTES.md"
        ).read_text() == f"Work in /work/{sibling}.\n"
        assert (root / "process-home").stat().st_mode & 0o777 == 0o700
        assert (root / "process-home/NOTES.md").stat().st_mode & 0o777 == 0o600
        assert not (root / "process-home/.claude").exists()


@pytest.mark.parametrize(
    "home",
    [
        "/tmp/home",
        "{{runtime_support_dir}}",
        "{{runtime_support_dir}}/../escape-home",
        "{{runtime_support_dir}}/claude-home",
        "{{runtime_support_dir}}/codex-home",
        "{{runtime_support_dir}}/nested/process-home",
    ],
)
def test_unowned_process_home_stays_native(home):
    profile = profile_with_process_home()
    profile.home_path_overrides["HOME"] = home
    assert untranslated_oauth_launch_options(profile)


@pytest.mark.parametrize(
    "name,content",
    [
        (".gitconfig", "unsafe"),
        (".claude/settings.json", "{}"),
        ("nested/NOTES.md", "no"),
        ("../escape.md", "no"),
        ("NOTES.md", "{{provider_api_key}}"),
        ("hook.sh", "no"),
    ],
)
def test_process_home_files_cannot_introduce_configuration_authority(name, content):
    profile = profile_with_process_home()
    profile.file_templates[-1].update(
        path="{{runtime_support_dir}}/process-home/" + name, content_template=content
    )
    assert untranslated_oauth_launch_options(profile)


@pytest.mark.parametrize(
    "collision",
    [
        "symlink-home",
        "symlink-parent",
        "unowned-home",
        "insecure-home",
        "symlink-marker",
    ],
)
def test_process_home_rejects_collision_and_symlink(tmp_path, collision):
    config, _ = configuration.oauth_configuration_for_profile(
        profile_with_process_home()
    )
    support = tmp_path / "support"
    support.mkdir()
    home = support / "process-home"
    if collision == "symlink-home":
        home.symlink_to(tmp_path, target_is_directory=True)
    elif collision == "symlink-parent":
        support.rmdir()
        support.symlink_to(tmp_path, target_is_directory=True)
    elif collision == "unowned-home":
        home.mkdir(mode=0o700)
    else:
        configuration.materialize_process_home(
            config, runtime_support_dir=support, workspace_path="/work"
        )
        if collision == "insecure-home":
            home.chmod(0o755)
        else:
            marker = home / ".moonmind-profile-digest"
            marker.unlink()
            marker.symlink_to(tmp_path / "not-owned")
    with pytest.raises(ValueError):
        configuration.materialize_process_home(
            config, runtime_support_dir=support, workspace_path="/work"
        )


def test_session_process_home_roundtrip_fails_closed():
    from moonmind.omnigent.session_launch import (
        OmnigentLaunchReconciliationError,
        validate_session_process_home,
    )

    expected = "/private/process-home"
    validate_session_process_home({"process_home": expected}, expected)
    validate_session_process_home({}, None)
    with pytest.raises(OmnigentLaunchReconciliationError):
        validate_session_process_home({"process_home": expected}, None)
    for snapshot in (None, {}, {"process_home": "/wrong"}):
        with pytest.raises(OmnigentLaunchReconciliationError):
            validate_session_process_home(snapshot, expected)


def test_process_home_session_payload_is_first_class_not_environment_override():
    from moonmind.workflows.adapters.omnigent_agent_adapter import (
        OmnigentResolvedTarget,
        build_omnigent_selection,
        build_omnigent_session_create_payload,
    )
    from tests.unit.omnigent.test_session_launch_reconciliation import _request

    request = _request()
    expected = "/home/app/.omnigent/moonmind/runtime-context/process-home"
    request.parameters["omnigent"]["session"]["processHome"] = expected
    payload = build_omnigent_session_create_payload(
        request=request,
        selection=build_omnigent_selection(request),
        target=OmnigentResolvedTarget(agent_id="agent", source="agent_id"),
    )
    assert payload["process_home"] == expected
    assert "env" not in payload


def test_process_home_handle_keeps_canonical_credential_mount():
    from moonmind.omnigent.credential_materializers import (
        ProfileCredentialHome,
        anticipated_credential_handle,
    )
    from moonmind.omnigent.host_services.runtime_scripts import (
        claude_process_home_from_handles,
    )

    selected = snapshot_provider_configuration(profile_with_process_home())
    acquired = SimpleNamespace(
        provider_profile_ref="profile",
        provider_lease_ref="lease",
        credential_generation=3,
        admission_epoch=1,
    )
    handle = anticipated_credential_handle(
        acquired,
        "claude-oauth-home@1",
        profile_credential_home=ProfileCredentialHome(
            volume_ref="enrolled", target_path="/home/app/.claude"
        ),
        provider_launch_environment=selected,
    )
    assert handle.attachments[0].sourceRef == "enrolled"
    assert handle.attachments[0].targetPath == "/home/app/.claude"
    assert handle.attachments[0].accessMode == "read-write"
    assert "HOME" not in handle.runtimeEnvironment
    path, config = claude_process_home_from_handles([handle.model_dump()])
    assert path.endswith("/runtime-context/process-home")
    assert len(config["fileTemplates"]) == 1
    assert all(not item.targetPath.endswith("NOTES.md") for item in handle.attachments)


@pytest.mark.parametrize(
    "supported", [True, False], ids=["supported-runner", "old-runner"]
)
def test_host_entrypoint_stages_private_home_before_runner_without_changing_global_home(
    tmp_path, supported
):
    import json
    import os
    import subprocess
    import sys

    from moonmind.omnigent.host_services.runtime_scripts import (
        OmnigentRuntimeScriptService,
    )

    runner_home, skills, binaries = (
        tmp_path / name for name in ("runner", "skills", "bin")
    )
    for path in (runner_home, skills, binaries):
        path.mkdir()
    config, _ = configuration.oauth_configuration_for_profile(
        profile_with_process_home()
    )
    private = configuration.process_home_configuration(config)
    handle = {
        "materializerRef": "claude-oauth-home@1",
        "credentialGeneration": 3,
        "attachments": [],
        "claudeProcessHomeConfiguration": private,
    }
    script, environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[handle],
        skill_attachment={"targetPath": str(skills)},
        step_execution_id="step",
        workspace_path="/work",
        enable_claude_runtime=True,
    )
    assert "HOME" not in environment
    assert environment["CLAUDE_CONFIG_DIR"] == "/home/app/.claude"
    (binaries / "python3").symlink_to(sys.executable)
    # The in-image probe is a tested boundary: old parsers must prevent launch.
    probe = binaries / "upstream-python"
    probe.write_text(
        f"#!{sys.executable}\nimport sys\nassert '_parse_os_env_spec' in sys.argv[2]\nassert 'process_home_environment' in sys.argv[2]\nraise SystemExit({0 if supported else 1})\n"
    )
    probe.chmod(0o700)
    (binaries / "claude").write_text("#!/bin/sh\nexit 0\n")
    (binaries / "claude").chmod(0o700)
    runner = binaries / "omnigent"
    runner.write_text(
        f"#!{sys.executable}\nimport os,pathlib,json\np=pathlib.Path({str(runner_home)!r})/'.omnigent/moonmind/runtime-context/process-home'\nassert p.is_dir()\nprint(json.dumps({{'HOME':os.environ['HOME'],'auth':os.environ['CLAUDE_CONFIG_DIR'],'text':(p/'NOTES.md').read_text()}}))\n"
    )
    runner.chmod(0o700)
    script = script.replace("/home/app", str(runner_home)).replace(
        "/opt/venv/bin/python", str(probe)
    )
    environment = {
        key: value.replace("/home/app", str(runner_home))
        for key, value in environment.items()
    }
    result = subprocess.run(
        check=False,
        args=["/bin/sh", "-ceu", script, "--", "http://example.invalid"],
        env={
            **os.environ,
            **environment,
            "HOME": str(runner_home),
            "PATH": f"{binaries}:/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
    )
    if supported:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "HOME": str(runner_home),
            "auth": str(runner_home / ".claude"),
            "text": "Work in /work.\n",
        }
    else:
        assert result.returncode != 0
        assert result.stdout == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "wrong", "unexpected"])
async def test_session_home_mismatch_prevents_task_dispatch(
    monkeypatch, tmp_path, mode
):
    import asyncio

    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.execute import run_omnigent_execution
    from tests.unit.omnigent.test_execute import _RecordingBridgeStore, _request

    posted = []
    expected = "/home/app/.omnigent/moonmind/runtime-context/process-home"

    class Client:
        def __init__(self, **kwargs):
            pass

        async def list_agents(self):
            return {"items": [{"id": "agent-1", "name": "claude-native-ui"}]}

        async def list_sessions(self, **kwargs):
            return {"data": [], "has_more": False}

        async def create_session(self, payload):
            assert payload.get("process_home") == (
                None if mode == "unexpected" else expected
            )
            return {"id": "session-1"}

        async def get_session(self, session_id):
            return {
                "id": session_id,
                "status": "idle",
                "items": [],
                **({} if mode == "missing" else {"process_home": "/foreign-home"}),
            }

        async def post_event(self, *args):
            posted.append(args)

        async def stream_events(self, session_id):
            await asyncio.Event().wait()
            yield {}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", Client)
    request = _request().model_copy(
        update={
            "parameters": {
                "omnigent": {
                    "agent": {
                        "agentName": "claude-native-ui",
                        "harnessOverride": "claude-native",
                    },
                    "session": {
                        "hostType": "external",
                        "hostId": "host",
                        "terminalLaunchArgs": ["--permission-mode", "default"],
                        "workspace": "/work",
                        **({} if mode == "unexpected" else {"processHome": expected}),
                    },
                    "prompt": {"text": "Do the task"},
                }
            }
        }
    )
    result = await run_omnigent_execution(
        request,
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        run_store=_RecordingBridgeStore(),
    )
    assert not posted
    assert "process HOME" in result.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "info",
    [
        {},
        {"session_process_home": False},
        {"session_process_home": "true"},
        {"session_process_home": True, "single_user": True},
    ],
)
async def test_only_advertised_server_process_home_capability_is_admitted(
    monkeypatch, info
):
    from unittest.mock import AsyncMock

    from api_service.services.omnigent_execution_plan_service import (
        _require_process_home_server,
    )
    from moonmind.omnigent.harness_platform.materializers import (
        ProviderConfigurationHandoffUnavailable,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    calls = AsyncMock(return_value=info)
    monkeypatch.setattr(OmnigentHttpClient, "_request", calls)
    configuration = snapshot_provider_configuration(profile_with_process_home())
    if info.get("session_process_home") is True:
        await _require_process_home_server({"primary-model": configuration})
    else:
        with pytest.raises(ProviderConfigurationHandoffUnavailable):
            await _require_process_home_server({"primary-model": configuration})
    calls.assert_awaited_once_with("GET", "/v1/info")
    calls.reset_mock()
    await _require_process_home_server(
        {"primary-model": snapshot_provider_configuration(claude_oauth_profile())}
    )
    calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_key_private_configuration_needs_no_process_home_capability(monkeypatch):
    from unittest.mock import AsyncMock

    from api_service.services.omnigent_execution_plan_service import (
        _require_process_home_server,
    )
    from moonmind.omnigent.harness_platform.materializers import (
        claude_provider_config_endpoint,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
    from tests.unit.omnigent.test_claude_api_key_provider_config import _api_key_profile
    from tests.unit.omnigent.test_claude_profile_configuration import configuration

    config = configuration()
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile(
        home_path_overrides=config["homePathOverrides"],
        file_templates=config["fileTemplates"],
    ))
    assert endpoint is not None, reason
    calls = AsyncMock()
    monkeypatch.setattr(OmnigentHttpClient, "_request", calls)
    await _require_process_home_server({"primary-model": endpoint})
    calls.assert_not_awaited()
    from moonmind.omnigent.credential_materializers import _claude_process_home

    assert _claude_process_home(endpoint) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "info,identity,expected",
    [
        ({"session_process_home": True, "single_user": True}, None, True),
        ({"session_process_home": True, "single_user": False}, {"user_id": "operator", "is_admin": True}, True),
        ({"session_process_home": True, "single_user": False}, {"user_id": "operator", "is_admin": False}, False),
        ({"session_process_home": True}, {"user_id": None, "is_admin": False}, False),
        ({"session_process_home": True}, {"user_id": "operator"}, False),
        ({"session_process_home": True}, {"user_id": "operator", "is_admin": "true"}, False),
        ({"session_process_home": True, "single_user": "true"}, {}, False),
        ({"session_process_home": True}, None, False),
    ],
    ids=["single-user", "admin", "non-admin", "anonymous", "missing-admin", "nonboolean-admin", "nonboolean-local", "missing-identity"],
)
async def test_process_home_readiness_requires_caller_authority(monkeypatch, info, identity, expected):
    from unittest.mock import AsyncMock, call

    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    request = AsyncMock(side_effect=[info, identity])
    client = OmnigentHttpClient(base_url="http://omnigent.test", api_token="test")
    monkeypatch.setattr(client, "_request", request)
    assert await client.supports_session_process_home() is expected
    assert request.await_args_list == [call("GET", "/v1/info")] + (
        [] if info.get("single_user") is True else [call("GET", "/v1/me")]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_endpoint", ["info", "identity"])
async def test_process_home_readiness_refuses_metadata_errors(monkeypatch, failing_endpoint):
    from unittest.mock import AsyncMock

    import httpx

    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    failure = httpx.ConnectError("unavailable")
    responses = [failure] if failing_endpoint == "info" else [{"session_process_home": True}, failure]
    client = OmnigentHttpClient(base_url="http://omnigent.test", api_token="test")
    monkeypatch.setattr(client, "_request", AsyncMock(side_effect=responses))
    assert not await client.supports_session_process_home()
