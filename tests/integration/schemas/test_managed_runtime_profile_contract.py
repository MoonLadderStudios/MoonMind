"""Integration coverage for managed runtime profile validation at launch boundary."""

from __future__ import annotations

import pytest

from moonmind.workflows.adapters.managed_agent_adapter import (
    build_managed_profile_launch_context,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _valid_docker_sidecar_profile() -> dict:
    return {
        "workloadMode": "docker-sidecar",
        "workspace": {
            "volume": "agent_workspaces",
            "mountPath": "/work/agent_jobs",
            "repoEnv": "MOONMIND_REPO_DIR",
            "lifecycle": "session",
        },
        "agent": {
            "image": "moonmind/managed-agent:2026-05-16",
            "workspace": {"mountPath": "/work/agent_jobs"},
            "dockerClient": {
                "enabled": True,
                "composePlugin": True,
                "daemonInAgent": False,
            },
            "env": {
                "DOCKER_HOST": "unix:///var/run/moonmind-docker/docker.sock",
            },
            "mounts": [
                {"name": "workspace", "mountPath": "/work/agent_jobs"},
                {"name": "docker-socket", "mountPath": "/var/run/moonmind-docker"},
            ],
        },
        "dockerSidecar": {
            "enabled": True,
            "mode": "dind",
            "image": "docker:27-dind",
            "socket": {
                "path": "/var/run/moonmind-docker/docker.sock",
                "volumeName": "docker-socket",
            },
            "storage": {
                "volumeName": "docker-graph",
                "mountPath": "/var/lib/docker",
                "lifecycle": "session",
                "daemonScope": "session",
            },
            "workspace": {"mountPath": "/work/agent_jobs"},
            "security": {
                "privileged": True,
                "hostDockerSocket": "forbidden",
                "moonmindDeploymentSecrets": "forbidden",
            },
            "mounts": [
                {"name": "workspace", "mountPath": "/work/agent_jobs"},
                {"name": "docker-socket", "mountPath": "/var/run/moonmind-docker"},
                {"name": "docker-graph", "mountPath": "/var/lib/docker"},
            ],
        },
        "policy": {
            "hostDockerAccess": "forbidden",
            "appContainerControlFromSession": "forbidden",
            "deploymentSecretsInSession": "forbidden",
            "apiContainerWorkloadDockerSocketAccess": False,
        },
    }


def test_launch_context_validates_runtime_profile_before_managed_session_launch() -> None:
    runtime_profile = _valid_docker_sidecar_profile()
    runtime_profile["dockerSidecar"]["image"] = "docker:latest"

    with pytest.raises(ValueError, match="sidecar image must be pinned"):
        build_managed_profile_launch_context(
            profile={
                "profile_id": "codex_default",
                "credential_source": "oauth_volume",
                "runtime_profile": runtime_profile,
            },
            runtime_for_profile="codex_cli",
            workflow_id="wf-agent-run-1",
            default_credential_source="oauth_volume",
        )
