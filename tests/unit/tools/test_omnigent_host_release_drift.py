"""The scheduled host publication follows its own upstream base image."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/omnigent_host_release_drift.sh"
WORKFLOWS = (
    ROOT / ".github/workflows/docker-publish-moonmind-host.yml",
    ROOT / ".github/workflows/docker-publish-opencode-host.yml",
)
COMPOSE = ROOT / "docker-compose.yaml"
ENV_TEMPLATE = ROOT / ".env-template"
BASE_DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "b" * 64


@pytest.mark.parametrize("workflow_path", WORKFLOWS)
def test_host_workflow_uses_base_image_authority(workflow_path):
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    drift = next(
        step for step in jobs["metadata"]["steps"] if step.get("id") == "drift"
    )
    build = next(step for step in jobs["build"]["steps"] if step.get("id") == "build")
    assert "tools/omnigent_host_release_drift.sh" in drift["run"]
    assert 'HOST_TAG="${IMAGE_NAME}:latest"' in drift["run"]
    assert (
        "OMNIGENT_BUILD_DIGEST=${{ steps.base.outputs.base_digest }}"
        in build["with"]["build-args"]
    )
    assert not any(
        step.get("id") == "omnigent_build" for step in jobs["build"]["steps"]
    )


def test_compose_host_default_uses_published_latest_channel_everywhere():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    shared_images = [
        service["image"]
        for service in services.values()
        if "OMNIGENT_SHARED_HOST_IMAGE_TAG" in service.get("image", "")
    ]
    assert shared_images
    assert all(
        "OMNIGENT_SHARED_HOST_IMAGE_TAG:-latest" in image for image in shared_images
    )
    for name in ("api", "temporal-worker-agent-runtime"):
        env = services[name]["environment"]
        assert (
            "OMNIGENT_OPENCODE_HOST_IMAGE_TAG=${OMNIGENT_OPENCODE_HOST_IMAGE_TAG:-latest}"
            in env
        )
        assert (
            "OMNIGENT_SHARED_HOST_IMAGE_TAG=${OMNIGENT_SHARED_HOST_IMAGE_TAG:-latest}"
            in env
        )


def test_env_template_uses_the_same_auto_refresh_channel():
    template = ENV_TEMPLATE.read_text(encoding="utf-8")
    assert 'OMNIGENT_OPENCODE_HOST_IMAGE_TAG="latest"' in template
    assert 'OMNIGENT_SHARED_HOST_IMAGE_TAG="latest"' in template


@pytest.mark.parametrize(
    ("published_digest", "expected"),
    [(BASE_DIGEST, "false"), (OLD_DIGEST, "true"), (None, "true")],
)
def test_host_drift_uses_base_digest_not_server_version(
    tmp_path, published_digest, expected
):
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        "  'pull ghcr.io/omnigent-ai/omnigent-host:latest') exit 0 ;;\n"
        "  'pull moonmind-host:current') "
        + ("exit 0" if published_digest else "exit 1")
        + " ;;\n"
        f"  'inspect --format={{{{index .RepoDigests 0}}}}') echo 'ghcr.io/omnigent-ai/omnigent-host@{BASE_DIGEST}'; exit 0 ;;\n"
        "  'inspect --format={{index .Config.Labels \"moonmind.omnigent.build_digest\"}}') "
        f"echo '{published_digest or ''}'; exit 0 ;;\n"
        "esac\n"
        "exit 2\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    output = tmp_path / "output"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "moonmind-host:current",
            "ghcr.io/omnigent-ai/omnigent-host:latest",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == f"rebuild_required={expected}\n"
