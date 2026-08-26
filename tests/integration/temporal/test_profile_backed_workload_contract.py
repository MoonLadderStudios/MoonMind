"""Regression coverage for removal of profile-backed public workload tools."""

import pytest

from moonmind.workloads.tool_bridge import (
    CONTAINER_JOB_TOOL_NAMES,
    build_container_job_tool_definition_payload,
    is_container_job_tool,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_legacy_profile_and_raw_tools_are_absent_from_discovery():
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


def test_discovered_container_tool_exposes_no_generic_gpu_resource():
    """The discoverable container tool carries no GPU field yet.

    docs/Workflows/GpuContainerResourcesContract.md section 3.1 documents that
    ``resources.gpu`` has no live caller route until
    MoonLadderStudios/MoonMind#3779 adds it to ``container.run_job`` and the
    trusted container-job backend. Assert it at the discovery boundary so the
    documented deferral is enforced rather than assumed.
    """

    definition = build_container_job_tool_definition_payload(
        name="container.run_job"
    )
    assert "gpu" not in str(definition)
