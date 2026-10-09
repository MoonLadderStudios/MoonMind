"""A recovered Step Execution launches the installed host image (#4627).

An update installs a new host image and replaces the lost attempt's runtime.
The successor Step Execution is a fresh launch, so it follows the installed
runtime selected by the deployment (#4503) even though the run's plan still
names the old image and that image is cached. Ordinary launches keep the
planned image when it satisfies the plan, and an explicit operator pin stays
authoritative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.omnigent.host_runtime import fresh_recovery_attempt
from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.unit.omnigent.test_image_owned_tool_delivery import (
    _ImageContentsBackend,
    _PRE_TOOLS_HOST,
    _TOOLED_HOST,
    _launcher,
    _pinned_host_class,
    _record_deployment_host,
    _tool_launch_spec,
)

_PLANNED = _PRE_TOOLS_HOST
_INSTALLED = _TOOLED_HOST


def _request(ordinal: int, reason: str) -> AgentExecutionRequest:
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": (
                f"workflow-1:run-1:implement:execution:{ordinal}:agent_execute"
            ),
            "stepExecution": {
                "workflowId": "workflow-1",
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": ordinal,
                "stepExecutionId": f"workflow-1:run-1:implement:execution:{ordinal}",
                "runtimeContextPolicy": "fresh_agent_run",
                "reason": reason,
            },
        }
    )


def test_only_a_recovered_successor_is_a_fresh_recovery_attempt():
    assert fresh_recovery_attempt(_request(2, "runtime_recovered")) is True
    assert fresh_recovery_attempt(_request(1, "initial_execution")) is False
    # A continuation of the same attempt is not a new launch decision.
    assert fresh_recovery_attempt(_request(2, "remediation")) is False


def _spec(prefer_installed: bool):
    spec = _tool_launch_spec(_PLANNED)
    return spec.model_copy(update={"preferInstalledImage": prefer_installed})


async def _launch(backend, *, prefer_installed: bool):
    return await _launcher(backend).launch(
        spec=_spec(prefer_installed),
        host_class=_pinned_host_class(_PLANNED),
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefer_installed", "expected"),
    [(True, _INSTALLED), (False, _PLANNED)],
    ids=["recovered-successor", "ordinary-launch"],
)
async def test_recovered_successor_launches_the_installed_image(
    monkeypatch, tmp_path: Path, prefer_installed: bool, expected: str
) -> None:
    _record_deployment_host(monkeypatch, tmp_path, _INSTALLED)
    # Both images are cached and both satisfy the plan: only the launch
    # decision distinguishes them.
    backend = _ImageContentsBackend(
        present={_PLANNED, _INSTALLED}, with_tools={_PLANNED, _INSTALLED}
    )

    result = await _launch(backend, prefer_installed=prefer_installed)

    assert backend.launched_image() == expected
    # The attempt records the image it actually ran on; the old attempt keeps
    # its own record.
    assert result["launchImageRef"] == expected


@pytest.mark.asyncio
async def test_recovered_successor_keeps_planned_image_without_a_qualified_install(
    monkeypatch, tmp_path: Path
) -> None:
    """An installed image that is absent locally is never substituted."""

    _record_deployment_host(monkeypatch, tmp_path, _INSTALLED)
    backend = _ImageContentsBackend(present={_PLANNED}, with_tools={_PLANNED})

    await _launch(backend, prefer_installed=True)

    assert backend.launched_image() == _PLANNED


@pytest.mark.asyncio
async def test_operator_pinned_image_stays_authoritative_for_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    _record_deployment_host(monkeypatch, tmp_path, _INSTALLED)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", _PLANNED)
    backend = _ImageContentsBackend(
        present={_PLANNED, _INSTALLED}, with_tools={_PLANNED, _INSTALLED}
    )

    await _launch(backend, prefer_installed=True)

    assert backend.launched_image() == _PLANNED
