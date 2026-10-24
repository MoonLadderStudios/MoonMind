"""The default Compose deployment must declare sources everywhere they gate.

``ContainerJobService.submit()`` resolves the deployment's declared image
sources before it creates durable job identity, so an ``imageSourceRef`` that
only the worker declares is rejected as unconfigured before the request ever
reaches Temporal. That breaks the checked-in ``tactics-test`` Skill on the
documented zero-configuration path, where ``.env`` defines none of these
variables and Compose supplies the derived default.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.unit.workflows.temporal.test_container_job_backend import (
    _recording_runner,
    _request,
)

#: Every service whose process resolves container-backend settings: the API
#: admits the request, the agent-runtime worker launches it.
_DECLARING_SERVICES = ("api", "temporal-worker-agent-runtime")

#: Refs the checked-in ``tactics-test`` Skill selects with no configuration.
_SKILL_IMAGE_SOURCE_REF = "tactics-unreal"
_SKILL_CACHE_REFS = ("unreal-ccache", "unreal-ubt")


def _service_environment(service: str) -> dict[str, str]:
    compose = yaml.safe_load(Path("docker-compose.yaml").read_text(encoding="utf-8"))
    environment = compose["services"][service]["environment"]
    if isinstance(environment, dict):
        return {str(key): str(value) for key, value in environment.items()}
    resolved: dict[str, str] = {}
    for entry in environment:
        key, _, value = str(entry).partition("=")
        resolved[key] = value
    return resolved


def _interpolate(value: str, overrides: dict[str, str] | None = None) -> str:
    """Apply Compose's ``${NAME:-default}`` substitution with an empty ``.env``.

    This is the zero-configuration deployment the Skill documents: nothing is
    set, so every reference collapses to its declared default. Defaults nest and
    contain JSON braces, so the closing brace is matched by depth rather than by
    a regular expression.
    """

    out: list[str] = []
    index = 0
    while index < len(value):
        start = value.find("${", index)
        if start < 0:
            out.append(value[index:])
            break
        out.append(value[index:start])
        depth = 0
        cursor = start + 1
        while cursor < len(value):
            if value[cursor] == "{":
                depth += 1
            elif value[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        assert depth == 0, f"unbalanced Compose interpolation in {value!r}"
        key, separator, default = value[start + 2 : cursor].partition(":-")
        override = (overrides or {}).get(key)
        out.append(override or (_interpolate(default, overrides) if separator else ""))
        index = cursor + 1
    return "".join(out)


@pytest.mark.parametrize("service", _DECLARING_SERVICES)
def test_compose_service_admits_the_checked_in_skill_refs(service: str) -> None:
    environment = _service_environment(service)
    declared = {
        key: _interpolate(environment[key])
        for key in (IMAGE_SOURCES_ENV_KEY, CACHE_SOURCES_ENV_KEY)
    }

    settings = resolve_container_backend_settings(declared)

    assert settings.image_source(_SKILL_IMAGE_SOURCE_REF).image
    for cache_ref in _SKILL_CACHE_REFS:
        assert settings.cache_source(cache_ref).volume_name


def test_compose_declares_identical_sources_for_api_and_worker() -> None:
    """A ref the worker can launch but the API refuses is never reachable."""

    api, worker = (_service_environment(name) for name in _DECLARING_SERVICES)
    for key in (IMAGE_SOURCES_ENV_KEY, CACHE_SOURCES_ENV_KEY):
        assert api[key] == worker[key]


_TACTICS_CACHES = [
    {"cacheRef": "unreal-ccache", "target": "/home/ue4/.ccache", "readOnly": False},
    {
        "cacheRef": "unreal-ubt",
        "target": "/home/ue4/.config/Epic/UnrealBuildTool",
        "readOnly": False,
    },
    {
        "cacheRef": "unreal-ddc",
        "target": "/home/ue4/.cache/UnrealEngine/Common/DerivedDataCache",
        "readOnly": False,
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("service", _DECLARING_SERVICES)
async def test_default_tactics_cache_request_reuses_owner_scoped_ddc(tmp_path, service):
    """Real Compose defaults must admit the repository runner's complete request."""
    environment = _service_environment(service)
    settings = resolve_container_backend_settings(
        {CACHE_SOURCES_ENV_KEY: _interpolate(environment[CACHE_SOURCES_ENV_KEY])}
    )
    (tmp_path / "art_workspace").mkdir()
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=settings,
        command_runner=_recording_runner(commands),
    )
    first = _request(tmp_path, caches=_TACTICS_CACHES)
    second = first.model_copy(
        update={
            "job_id": "container-job:fedcba9876543210fedcba9876543210",
            "ownership_token": "next-attempt",
        }
    )
    other_owner = first.model_copy(
        update={
            "owner": first.owner.model_copy(update={"principal_id": "another-owner"}),
        }
    )
    for request in (first, second, other_owner):
        result = await backend.create_container(request)
        assert result.resolved_cache_refs == tuple(
            c["cacheRef"] for c in _TACTICS_CACHES
        )

    creates = [command for command in commands if command[0] == "create"]
    assert len(creates) == 3
    ddc = settings.cache_source("unreal-ddc")
    assert ddc.volume_name == "unreal_ddc_volume"
    assert ddc.target == _TACTICS_CACHES[2]["target"]
    assert not ddc.read_only
    owner_digest = hashlib.sha256(
        f"{first.owner.principal_type}:{first.owner.principal_id}".encode()
    ).hexdigest()[:20]
    mount = f"type=volume,src=unreal_ddc_volume-{owner_digest},dst={ddc.target}"
    assert mount in creates[0] and mount in creates[1]
    assert mount not in creates[2]
    assert (
        len(
            [
                item
                for item in creates[2]
                if item.startswith("type=volume,src=unreal_ddc_volume-")
            ]
        )
        == 1
    )
    provisioned = [
        command for command in commands if command[:2] == ("volume", "create")
    ]
    assert all(
        "moonmind.kind=container-job-cache" in command for command in provisioned
    )
    assert (
        sum("moonmind.cache_ref=unreal-ddc" in command for command in provisioned) == 3
    )
    assert (
        sum(
            f"moonmind.cache_owner={owner_digest}" in command for command in provisioned
        )
        == 6
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("service", _DECLARING_SERVICES)
@pytest.mark.parametrize("explicit_override", [False, True])
async def test_compose_cache_admission_preserves_explicit_authority(
    tmp_path, service, explicit_override
):
    environment = _service_environment(service)
    declaration = environment[CACHE_SOURCES_ENV_KEY]
    overrides = {}
    requested = [
        {"cacheRef": "unapproved-cache", "target": "/cache", "readOnly": False}
    ]
    if explicit_override:
        original_two = [
            dict(cache, volumeName=cache["cacheRef"].replace("-", "_") + "_volume")
            for cache in _TACTICS_CACHES[:2]
        ]
        overrides[CACHE_SOURCES_ENV_KEY] = json.dumps(original_two)
        requested = _TACTICS_CACHES
    settings = resolve_container_backend_settings(
        {CACHE_SOURCES_ENV_KEY: _interpolate(declaration, overrides)}
    )
    if explicit_override:
        assert len(settings.cache_sources) == 2
    (tmp_path / "art_workspace").mkdir()
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=settings,
        command_runner=_recording_runner(commands),
    )
    with pytest.raises(RuntimeError, match="not deployment-authorized"):
        await backend.create_container(_request(tmp_path, caches=requested))
    assert not any(command[0] == "create" for command in commands)


@pytest.mark.parametrize("service", _DECLARING_SERVICES)
def test_compose_passes_registry_credential_reference_to_api_and_worker(
    service: str,
) -> None:
    environment = _service_environment(service)
    declaration = environment[IMAGE_SOURCES_ENV_KEY].replace(
        "${MOONMIND_UNREAL_ENGINE_REGISTRY_CREDENTIAL_REF:-}", "db://registry-pull"
    )
    settings = resolve_container_backend_settings(
        {IMAGE_SOURCES_ENV_KEY: _interpolate(declaration)}
    )
    assert (
        settings.image_source(_SKILL_IMAGE_SOURCE_REF).registry_credential_ref
        == "db://registry-pull"
    )
