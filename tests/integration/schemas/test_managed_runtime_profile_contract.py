"""Integration coverage for managed runtime profile validation at launch boundary."""

from __future__ import annotations

import pytest

from moonmind.workflows.adapters.managed_agent_adapter import (
    build_managed_profile_launch_context,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _container_jobs_profile() -> dict:
    return {
        "workloadMode": "container-jobs",
        "workspace": {
            "volume": "agent_workspaces",
            "mountPath": "/work/agent_jobs",
            "repoEnv": "MOONMIND_REPO_DIR",
            "lifecycle": "session",
        },
        "agent": {
            "image": "moonmind/managed-agent:2026-08-04",
            "workspace": {"mountPath": "/work/agent_jobs"},
            "dockerClient": {
                "enabled": False,
                "composePlugin": False,
                "daemonInAgent": False,
            },
            "env": {},
            "mounts": [
                {"name": "workspace", "mountPath": "/work/agent_jobs"},
            ],
        },
        "resources": {
            "session": {"maxRuntimeSeconds": 14400},
            "agent": {"cpu": "2", "memory": "4Gi"},
        },
        "labels": {
            "moonmind.kind": "managed-session",
            "moonmind.workload_mode": "container-jobs",
        },
        "policy": {
            "hostDockerSocket": "forbidden",
            "sharedDaemonAcrossUsers": "forbidden",
            "moonmindDeploymentSecretsInSession": "forbidden",
            "appContainerControlFromSession": "forbidden",
            "apiContainerWorkloadDockerSocketAccess": False,
        },
    }


def _launch(profile: dict) -> None:
    build_managed_profile_launch_context(
        profile={
            "profile_id": "codex_default",
            "credential_source": "oauth_volume",
            "runtime_profile": profile,
        },
        runtime_for_profile="codex_cli",
        workflow_id="wf-agent-run-1",
        default_credential_source="oauth_volume",
    )


def test_launch_context_accepts_api_owned_container_jobs_profile() -> None:
    _launch(_container_jobs_profile())


def test_launch_context_rejects_removed_docker_sidecar_profile() -> None:
    runtime_profile = _container_jobs_profile()
    runtime_profile["workloadMode"] = "docker-sidecar"
    runtime_profile["dockerSidecar"] = {"enabled": True}

    with pytest.raises(ValueError, match="workloadMode|dockerSidecar"):
        _launch(runtime_profile)


def test_launch_context_rejects_raw_docker_endpoint() -> None:
    runtime_profile = _container_jobs_profile()
    runtime_profile["agent"]["dockerClient"]["enabled"] = True
    runtime_profile["agent"]["env"]["DOCKER_HOST"] = "unix:///var/run/docker.sock"

    with pytest.raises(ValueError, match="dockerClient.enabled must be false"):
        _launch(runtime_profile)
