"""A fresh default Compose install must resolve the default Omnigent adapter.

Source issue: MoonLadderStudios/MoonMind#4356. The disposable default
first-run journey (``tools/first_run_journey_3938.sh``) runs
``docker-compose.yaml`` with no ``.env``. Every submitted task then failed at
``integration.resolve_adapter_metadata`` with ``No external adapter
registered for agent_id='omnigent'`` because the worker that runs that
activity only received the Omnigent gate from ``.env``. These tests resolve
the Compose defaults an absent ``.env`` collapses to for the worker that owns
each adapter-resolution activity, and build the real adapter registry from
that environment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from moonmind.workflows.adapters.external_adapter_registry import build_default_registry
from moonmind.workflows.temporal import build_all_worker_topologies
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")

#: Activities whose implementation builds the external adapter registry.
_ADAPTER_RESOLUTION_ACTIVITIES = ("integration.resolve_adapter_metadata",)


def _compose_default_environment(service: str) -> dict[str, str]:
    """Return the service environment with no ``.env`` and no host overrides."""

    compose = yaml.safe_load(
        (_REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    environment = compose["services"][service].get("environment") or []
    if isinstance(environment, dict):
        entries = [f"{key}={value}" for key, value in environment.items()]
    else:
        entries = [str(entry) for entry in environment]
    resolved: dict[str, str] = {}
    for entry in entries:
        key, _, value = entry.partition("=")
        resolved[key] = _INTERPOLATION.sub(lambda m: m.group(2) or "", value)
    return resolved


def _service_for_activity(activity_type: str) -> str:
    fleet = build_default_activity_catalog().resolve_activity(activity_type).fleet
    topologies = {
        topology.fleet: topology for topology in build_all_worker_topologies()
    }
    return topologies[fleet].service_name


@pytest.mark.parametrize("activity_type", _ADAPTER_RESOLUTION_ACTIVITIES)
def test_default_compose_worker_registers_omnigent_adapter(activity_type: str) -> None:
    service = _service_for_activity(activity_type)
    registry = build_default_registry(env=_compose_default_environment(service))

    assert "omnigent" in registry, (
        f"{service} runs {activity_type} but the default Compose environment "
        "does not enable the Omnigent adapter"
    )


@pytest.mark.parametrize("activity_type", _ADAPTER_RESOLUTION_ACTIVITIES)
def test_default_compose_omnigent_gate_stays_operator_overridable(
    activity_type: str,
) -> None:
    service = _service_for_activity(activity_type)
    compose = yaml.safe_load(
        (_REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    declared = {
        str(entry).partition("=")[0]: str(entry).partition("=")[2]
        for entry in compose["services"][service]["environment"]
    }

    assert declared["OMNIGENT_ENABLED"].startswith("${OMNIGENT_ENABLED:-")
    assert declared["OMNIGENT_SERVER_URL"].startswith("${OMNIGENT_SERVER_URL:-")
