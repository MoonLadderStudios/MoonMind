"""Deployment nonsecret projection across the generic host boundary."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

SETTING = "MOONMIND_MANAGED_SESSION_NON_SECRET_ENV_KEYS"


def test_project_settings_survive_generic_runtime_and_shell_context(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(SETTING, "PROJECT_ASSET_SERVER,PROJECT_IMAGE_REF")
    expected = {
        "PROJECT_ASSET_SERVER": "asset://assets.example:41337",
        "PROJECT_IMAGE_REF": "images:preparer $(touch SHOULD_NOT_EXIST); 'quoted'",
    }
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow",
            "idempotencyKey": "idem",
            "parameters": {},
        }
    )
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="", signing_secret=""
    ).mint(
        request=request,
        plan=SimpleNamespace(),
        host_lease_ref="lease",
        capability_lifetime_seconds=10,
    )
    assert environment == expected
    script, environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[],
        skill_attachment={"targetPath": str(tmp_path)},
        step_execution_id="step",
        runtime_environment=dict(environment),
    )
    for key, value in expected.items():
        assert environment[key] == value
        assert key in environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",")
        assert f"export {key}=$(cat " in script
    assert (
        subprocess.run(
            ["/bin/sh", "-n"], input=script, text=True, capture_output=True, check=False
        ).returncode
        == 0
    )
    # Execute the actual generated context writer, then restore into a stripped
    # shell through the generated login profile (no runtime Docker required).
    setup = script.split('if [ "${MOONMIND_OPENCODE_RUNTIME:-0}" = 1 ]; then ', 1)[0]
    setup = setup.replace("/home/app", str(tmp_path))
    subprocess.run(
        ["/bin/sh", "-c", setup], env={**os.environ, **environment}, check=True
    )
    restored = subprocess.run(
        ["/bin/sh", "-c", f'. {tmp_path}/.profile; printf "%s" "$PROJECT_IMAGE_REF"'],
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=True,
    )
    assert restored.stdout == expected["PROJECT_IMAGE_REF"]
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("RUBYOPT", "evil"),
        ("PERL5OPT", "evil"),
        ("JAVA_TOOL_OPTIONS", "evil"),
        ("JDK_JAVA_OPTIONS", "evil"),
        ("ENV", "/evil"),
        ("BASH_ENV", "/evil"),
        ("IFS", "evil"),
        ("DYLD_LIBRARY_PATH", "/evil"),
        ("PYTHONSTARTUP", "/evil"),
        ("RES_OPTIONS", "evil"),
        ("SSL_CERT_FILE", "/evil"),
        ("DOCKER_HOST", "tcp://evil"),
        ("PATH", "/evil"),
        ("HOME", "/evil"),
        ("LD_PRELOAD", "/evil"),
        ("PYTHONPATH", "/evil"),
        ("NODE_OPTIONS", "--require=evil"),
        ("GIT_CONFIG_COUNT", "1"),
        ("GH_HOST", "evil"),
        ("SSH_AUTH_SOCK", "/evil"),
        ("http_proxy", "http://evil"),
        ("OMNIGENT_RUNNER_ENV_PASSTHROUGH", "x"),
        ("MOONMIND_CONTAINER_SERVICE_GRANTS", "x"),
        ("PROJECT_ACCESS_TOKEN", "secret"),
        ("PRIVATE_KEY", "secret"),
        ("TLS_PRIVATE_KEY", "secret"),
        ("SIGNING_KEY", "secret"),
        ("ENCRYPTION_KEY", "secret"),
        ("ACCESS_KEY", "secret"),
        ("PROJECT_SIGNING_KEY_FILE", "/secret"),
        ("PROJECT_ACCESS_KEY_ID", "secret"),
        ("BAD-NAME", "x"),
        ("PROJECT_URL", "https://user:password@example.org"),
        ("PROJECT_URL", "https://example.org?token=secret"),
        ("PROJECT_VALUE", "x\ny"),
    ],
)
def test_unsafe_declarations_do_not_reach_runtime(monkeypatch, key, value):
    from moonmind.omnigent.host_services.project_environment import (
        deployment_project_environment,
    )

    monkeypatch.setenv(SETTING, key)
    monkeypatch.setenv(key, value)
    assert deployment_project_environment() == {}


def test_script_rejects_caller_override_of_declared_project_value(monkeypatch):
    monkeypatch.setenv(SETTING, "PROJECT_SERVER")
    monkeypatch.setenv("PROJECT_SERVER", "approved.example")
    with pytest.raises(ValueError):
        OmnigentRuntimeScriptService().build_entrypoint(
            credential_handles=[],
            skill_attachment={"targetPath": "/skills"},
            step_execution_id="step",
            runtime_environment={"PROJECT_SERVER": "forged.example"},
        )


def test_rejected_declaration_never_logs_name_or_value(monkeypatch, caplog):
    from moonmind.omnigent.host_services.project_environment import (
        deployment_project_environment,
    )

    monkeypatch.setenv(SETTING, "accidentally pasted credential=value")
    assert deployment_project_environment() == {}
    assert "credential=value" not in caplog.text


def test_stock_legacy_declaration_does_not_break_generic_workflow(monkeypatch):
    monkeypatch.setenv(SETTING, "MOONMIND_UNREAL_ENGINE_IMAGE")
    monkeypatch.setenv("MOONMIND_UNREAL_ENGINE_IMAGE", "engine:existing-default")
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow",
            "idempotencyKey": "idem",
            "parameters": {},
        }
    )
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="", signing_secret=""
    ).mint(
        request=request,
        plan=SimpleNamespace(),
        host_lease_ref="lease",
        capability_lifetime_seconds=10,
    )
    assert environment == {}  # Same generic behavior as before; legacy is untouched.
    _, actual = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[],
        skill_attachment={"targetPath": "/skills"},
        step_execution_id="step",
        runtime_environment=dict(environment),
    )
    assert "MOONMIND_UNREAL_ENGINE_IMAGE" not in actual


def test_nonsecret_lookup_keys_remain_supported(monkeypatch):
    from moonmind.omnigent.host_services.project_environment import (
        deployment_project_environment,
    )

    monkeypatch.setenv(SETTING, "PROJECT_LOOKUP_KEY,PROJECT_PUBLIC_KEY")
    monkeypatch.setenv("PROJECT_LOOKUP_KEY", "entry-1")
    monkeypatch.setenv("PROJECT_PUBLIC_KEY", "public-data")
    assert deployment_project_environment() == {
        "PROJECT_LOOKUP_KEY": "entry-1",
        "PROJECT_PUBLIC_KEY": "public-data",
    }
