"""The default Compose deployment must declare sources everywhere they gate.

``ContainerJobService.submit()`` resolves the deployment's declared image
sources before it creates durable job identity, so an ``imageSourceRef`` that
only the worker declares is rejected as unconfigured before the request ever
reaches Temporal. That breaks the checked-in ``tactics-test`` Skill on the
documented zero-configuration path, where ``.env`` defines none of these
variables and Compose supplies the derived default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
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


def _interpolate(value: str) -> str:
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
        _, separator, default = value[start + 2 : cursor].partition(":-")
        out.append(_interpolate(default) if separator else "")
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
