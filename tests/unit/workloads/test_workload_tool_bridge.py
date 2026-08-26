from __future__ import annotations

import pytest

from moonmind.schemas.container_job_models import ContainerJobSubmitRequest
from moonmind.workloads.tool_bridge import (
    CONTAINER_JOB_TOOL_NAMES,
    CONTAINER_RUN_JOB_TOOL,
    build_container_job_tool_definition_payload,
    is_container_job_tool,
)


def test_generic_container_job_is_the_only_discoverable_container_tool() -> None:
    assert CONTAINER_JOB_TOOL_NAMES == {"container.run_job"}
    assert is_container_job_tool(CONTAINER_RUN_JOB_TOOL)
    for legacy in (
        "container.run_workload",
        "container.run_container",
        "container.start_helper",
        "container.stop_helper",
        "container.run_docker",
        "moonmind.integration_ci",
        "unreal.run_tests",
    ):
        assert not is_container_job_tool(legacy)
        with pytest.raises(ValueError):
            build_container_job_tool_definition_payload(name=legacy)


def test_generic_tool_uses_logical_workspace_and_declared_outputs() -> None:
    definition = build_container_job_tool_definition_payload(
        name=CONTAINER_RUN_JOB_TOOL
    )
    schema = definition["inputs"]["schema"]
    assert schema["required"] == ["idempotencyKey", "spec"]
    spec = schema["properties"]["spec"]
    assert "workspaceRef" in spec["properties"]
    assert "workspaceRef" not in spec["required"]
    managed = spec["properties"]["workspaceRef"]["oneOf"][2]
    assert managed["properties"]["kind"] == {"const": "managed_runtime"}
    assert "outputs" in spec["properties"]
    serialized = str(definition)
    for forbidden in ("repoDir", "artifactsDir", "scratchDir", "dockerHost", "privileged"):
        assert forbidden not in serialized


def test_canonical_model_rejects_authority_injection_from_workflow_tool() -> None:
    payload = {
        "idempotencyKey": "workflow:w:r:s",
        "source": {"source": "workflow", "workflowId": "w", "runId": "r", "stepId": "s"},
        "spec": {
            "image": "alpine:3.20",
            "workspaceRef": {"kind": "sandbox", "workspaceId": "workspace-1"},
            "command": ["true"],
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    }
    ContainerJobSubmitRequest.model_validate(payload)
    payload["spec"]["privileged"] = True
    with pytest.raises(ValueError):
        ContainerJobSubmitRequest.model_validate(payload)


def test_canonical_container_tool_defers_generic_gpu_resources_to_3779() -> None:
    """``container.run_job`` exposes no GPU field until the convergence slice.

    docs/Workflows/GpuContainerResourcesContract.md section 3.1 states that the
    generic GPU resource model rides the unrestricted container request only,
    and that MoonLadderStudios/MoonMind#3779 owns adding it to the canonical
    container-job contract and backend. Pin the deferral at the production
    tool-definition builder and the production submission model so a partial
    migration cannot appear without also updating that contract doc.
    """

    definition = build_container_job_tool_definition_payload(
        name=CONTAINER_RUN_JOB_TOOL
    )
    resources = definition["inputs"]["schema"]["properties"]["spec"]["properties"][
        "resources"
    ]
    assert set(resources["properties"]) == {"cpuMillis", "memoryMiB", "pids"}
    assert resources["additionalProperties"] is False

    payload = {
        "idempotencyKey": "workflow:w:r:s",
        "source": {
            "source": "workflow",
            "workflowId": "w",
            "runId": "r",
            "stepId": "s",
        },
        "spec": {
            "image": "alpine:3.20",
            "workspaceRef": {"kind": "sandbox", "workspaceId": "workspace-1"},
            "command": ["true"],
            "resources": {
                "cpuMillis": 100,
                "memoryMiB": 64,
                "gpu": {"vendor": "nvidia", "count": "all"},
            },
        },
    }
    with pytest.raises(ValueError):
        ContainerJobSubmitRequest.model_validate(payload)
