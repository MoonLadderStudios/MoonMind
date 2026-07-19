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
