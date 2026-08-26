"""Regression coverage for removal of profile-backed public workload tools."""

import pytest

from moonmind.workloads.tool_bridge import (
    CONTAINER_JOB_TOOL_NAMES,
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
