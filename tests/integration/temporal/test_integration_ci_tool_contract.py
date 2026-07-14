"""Generic container-job integration contract coverage."""

import pytest

from moonmind.schemas.container_job_models import ContainerJobSubmitRequest
from moonmind.workloads.tool_bridge import (
    CONTAINER_RUN_JOB_TOOL,
    build_container_job_tool_definition_payload,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize(
    ("image", "command"),
    [
        ("alpine:3.20", ["true"]),
        ("mcr.microsoft.com/dotnet/sdk:8.0", ["dotnet", "test"]),
    ],
)
def test_generic_and_dotnet_workloads_share_one_container_job_contract(image, command):
    definition = build_container_job_tool_definition_payload(name=CONTAINER_RUN_JOB_TOOL)
    assert definition["requirements"]["capabilities"] == ["docker_workload"]
    request = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": f"integration:{image}",
            "source": {"source": "workflow", "workflowId": "wf", "runId": "run", "stepId": "step"},
            "spec": {
                "image": image,
                "workspaceRef": {"kind": "sandbox", "workspaceId": "ws"},
                "command": command,
                "resources": {"cpuMillis": 500, "memoryMiB": 512},
            },
        }
    )
    assert request.spec.image == image
    assert request.source.source == "workflow"
