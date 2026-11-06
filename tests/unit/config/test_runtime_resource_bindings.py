"""Installed resources reach backend admission and ordinary runtime shells."""

import json
from types import SimpleNamespace

import pytest

from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    ContainerBackendConfigError,
    LocalImageRecipe,
    resolve_container_backend_settings,
)
from moonmind.omnigent.host_services.project_environment import (
    approved_project_environment,
    deployment_project_environment,
)
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def install(tmp_path, monkeypatch, **values):
    path = tmp_path / "resources.json"
    path.write_text(json.dumps({"schemaVersion": 1, **values}))
    monkeypatch.setenv("MOONMIND_RUNTIME_RESOURCES_FILE", str(path))
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_NON_SECRET_ENV_KEYS", raising=False)
    return path


def test_installed_resources_supply_backend_and_runtime_without_workflow_inputs(
    tmp_path, monkeypatch
):
    install(
        tmp_path,
        monkeypatch,
        imageSources=[
            {"sourceRef": "asset-preparer", "image": "example/preparer:latest"}
        ],
        cacheSources=[
            {
                "cacheRef": "asset-objects",
                "volumeName": "asset_objects",
                "target": "/cache/objects",
            }
        ],
        environment={
            "PROJECT_PREPARATION_IMAGE": "asset-preparer",
            "PROJECT_CACHE": "asset-objects",
        },
    )
    settings = resolve_container_backend_settings()
    assert settings.image_source("asset-preparer").image == "example/preparer:latest"
    assert settings.cache_source("asset-objects").target == "/cache/objects"
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow",
            "idempotencyKey": "request",
            "parameters": {},
        }
    )
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="", signing_secret=""
    ).mint(
        request=request,
        plan=SimpleNamespace(),
        host_lease_ref="lease",
        capability_lifetime_seconds=60,
    )
    assert environment == {
        "PROJECT_PREPARATION_IMAGE": "asset-preparer",
        "PROJECT_CACHE": "asset-objects",
    }
    assert approved_project_environment(environment, reserved=set()) == environment


def test_explicit_existing_settings_override_installed_entries(tmp_path, monkeypatch):
    install(
        tmp_path,
        monkeypatch,
        imageSources=[{"sourceRef": "preparer", "image": "example/default:latest"}],
        environment={"PROJECT_SERVER": "asset://default.example:1234"},
    )
    monkeypatch.setenv(
        IMAGE_SOURCES_ENV_KEY,
        json.dumps(
            [
                {"sourceRef": "preparer", "image": "example/operator:latest"},
                {"sourceRef": "other", "image": "example/other:latest"},
            ]
        ),
    )
    monkeypatch.setenv("MOONMIND_MANAGED_SESSION_NON_SECRET_ENV_KEYS", "PROJECT_SERVER")
    monkeypatch.setenv("PROJECT_SERVER", "asset://operator.example:1234")
    settings = resolve_container_backend_settings()
    assert settings.image_source("preparer").image == "example/operator:latest"
    assert settings.image_source("other").image == "example/other:latest"
    assert deployment_project_environment() == {
        "PROJECT_SERVER": "asset://operator.example:1234"
    }


def test_local_resource_recipe_uses_installed_context_without_named_stage(
    tmp_path, monkeypatch
):
    context = tmp_path / "preparer"
    context.mkdir()
    (context / "Dockerfile").write_text(
        "FROM example/base:latest\nCOPY payload /payload\n"
    )
    (context / "payload").write_text("one")
    install(
        tmp_path,
        monkeypatch,
        imageSources=[
            {
                "sourceRef": "preparer",
                "image": "preparer:local",
                "build": {
                    "context": "preparer",
                    "dockerfile": "Dockerfile",
                    "validationCommand": ["test", "-f", "/payload"],
                },
            }
        ],
    )
    source = resolve_container_backend_settings().image_source("preparer")
    assert isinstance(source, LocalImageRecipe)
    assert source.context_root == context.resolve()
    assert source.target == ""
    assert source.fingerprint_inputs == ("**/*",)


@pytest.mark.parametrize("context", ["../outside", "/outside"])
def test_resource_build_cannot_escape_installed_package(tmp_path, monkeypatch, context):
    install(
        tmp_path,
        monkeypatch,
        imageSources=[
            {
                "sourceRef": "preparer",
                "image": "preparer:local",
                "build": {
                    "context": context,
                    "dockerfile": "Dockerfile",
                    "validationCommand": ["true"],
                },
            }
        ],
    )
    with pytest.raises(ContainerBackendConfigError, match="context"):
        resolve_container_backend_settings()
