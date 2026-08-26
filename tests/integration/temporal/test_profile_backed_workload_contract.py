"""Which container tools plan discovery may publish, and in which Docker mode."""

from pathlib import Path

import pytest
import yaml

from moonmind.workloads.tool_bridge import (
    CONTAINER_JOB_TOOL_NAMES,
    build_container_job_tool_definition_payload,
    is_container_job_tool,
)
from moonmind.workloads.unrestricted_container_tool import (
    CONTAINER_RUN_CONTAINER_TOOL,
    build_unrestricted_container_tool_definition_payload,
    unrestricted_container_tool_enabled,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

REPO_ROOT = Path(__file__).resolve().parents[3]
# ``plan.generate`` (discovery) runs on the llm fleet; ``mm.tool.execute``
# for a ``docker_workload`` capability (dispatch) runs on the agent_runtime
# fleet. The gating predicate is only single-valued if both read the same
# deployment-owned environment.
DISCOVERY_SERVICE = "temporal-worker-llm"
DISPATCH_SERVICE = "temporal-worker-agent-runtime"


def test_profile_backed_and_raw_tools_are_absent_from_discovery():
    """Profile-backed, helper-lifecycle, and raw-CLI names stay removed."""

    assert CONTAINER_JOB_TOOL_NAMES == {"container.run_job"}
    for name in (
        "container.run_workload",
        "container.run_container",
        "container.start_helper",
        "container.stop_helper",
        "container.run_docker",
        "moonmind.integration_ci",
        "unreal.run_tests",
    ):
        assert not is_container_job_tool(name)


def test_unrestricted_container_tool_is_gated_by_the_deployment_docker_mode():
    """The generic GPU caller route opens only in unrestricted mode.

    MoonLadderStudios/MoonMind#3775 (epic #3774, "Immediate unrestricted path")
    makes ``container.run_container`` the caller route for
    ``resources.gpu``. One predicate gates both discovery and dispatch, so the
    surface can never be advertised in a mode that would refuse to run it.
    """

    assert unrestricted_container_tool_enabled("unrestricted")
    for mode in ("profiles", "disabled"):
        assert not unrestricted_container_tool_enabled(mode)

    definition = build_unrestricted_container_tool_definition_payload(
        name=CONTAINER_RUN_CONTAINER_TOOL
    )
    resources = definition["inputs"]["schema"]["properties"]["resources"]
    assert resources["properties"]["gpu"]["properties"]["vendor"] == {
        "enum": ["nvidia"]
    }
    assert definition["requirements"]["capabilities"] == ["docker_workload"]


def test_canonical_container_job_tool_exposes_no_generic_gpu_resource():
    """``container.run_job`` carries no GPU field yet.

    docs/Workflows/GpuContainerResourcesContract.md section 3.1 documents that
    the versioned generic GPU request rides the unrestricted container request,
    and that MoonLadderStudios/MoonMind#3779 owns carrying it into the canonical
    asynchronous container-job contract and backend. Assert the split at the
    discovery boundary so it is enforced rather than assumed.
    """

    definition = build_container_job_tool_definition_payload(
        name="container.run_job"
    )
    assert "gpu" not in str(definition)


def test_discovery_and_dispatch_workers_read_one_deployment_docker_mode():
    """The gate cannot diverge across the two containers that enforce it.

    ``MOONMIND_WORKFLOW_DOCKER_MODE`` is deployment-owned. If the worker that
    publishes the tool and the worker that runs it could see different values,
    discovery could advertise a surface dispatch refuses. Both services load the
    same ``.env`` and neither pins the variable to a service-local literal.
    """

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    )
    services = compose["services"]

    for name in (DISCOVERY_SERVICE, DISPATCH_SERVICE):
        service = services[name]
        env_files = {entry["path"] for entry in service["env_file"]}
        assert ".env" in env_files, name
        inline = service.get("environment") or []
        assert not [
            item for item in inline if item.startswith("MOONMIND_WORKFLOW_DOCKER_MODE=")
        ], name
