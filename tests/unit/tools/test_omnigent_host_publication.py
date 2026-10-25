"""The shared host publishes fresh, verified images on a recurring schedule."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/docker-publish-moonmind-host.yml"
COMPOSE = ROOT / "docker-compose.yaml"
ENV_TEMPLATE = ROOT / ".env-template"


def test_scheduled_host_publication_rebuilds_without_an_upstream_change():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    jobs = workflow["jobs"]
    build = next(step for step in jobs["build"]["steps"] if step.get("id") == "build")
    assert triggers["schedule"] == [{"cron": "37 6 * * *"}]
    assert not jobs["build"].get("if")
    assert not jobs["merge"].get("if")
    assert "rebuild_required" not in jobs["metadata"]["outputs"]
    assert build["with"]["pull"] is True
    assert build["with"]["no-cache"] == "${{ github.event_name == 'schedule' }}"
    assert (
        "OMNIGENT_BUILD_DIGEST=${{ steps.base.outputs.base_digest }}"
        in build["with"]["build-args"]
    )
    assert build["with"]["file"] == "./services/omnigent/moonmind-host/Dockerfile"
    assert {
        entry["platform"] for entry in jobs["build"]["strategy"]["matrix"]["include"]
    } == {"linux/amd64", "linux/arm64"}
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_shared_host_is_the_only_host_image_publisher():
    publishers = [
        path.name
        for path in (ROOT / ".github/workflows").glob("*.yml")
        if "REGISTRY_IMAGE: ghcr.io/moonladderstudios/omnigent-host-"
        in path.read_text()
    ]
    assert publishers == [WORKFLOW.name]


@pytest.fixture
def resolve_metadata(tmp_path):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    metadata = next(
        step
        for step in workflow["jobs"]["metadata"]["steps"]
        if step.get("id") == "meta"
    )
    dockerfile = tmp_path / "services/omnigent/moonmind-host/Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text(
        "ARG CODEX_CLI_VERSION=0.160.0\n"
        "ARG CLAUDE_CLI_VERSION=2.2.0\n"
        "ARG OPENCODE_VERSION=1.18.12\n"
    )
    manifest = tmp_path / "services/omnigent/tools/manifest.lock.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"tools": [{"name": "gh", "version": "2.80.0"}]}))
    output = tmp_path / "output"

    def resolve(**inputs):
        result = subprocess.run(
            ["bash", "-eu", "-o", "pipefail", "-c", metadata["run"]],
            cwd=tmp_path,
            env={
                **os.environ,
                "GITHUB_OUTPUT": str(output),
                "REGISTRY_IMAGE": "ghcr.io/MoonLadderStudios/omnigent-host-moonmind",
                "CODEX_CLI_VERSION_INPUT": "",
                "CLAUDE_CLI_VERSION_INPUT": "",
                "OPENCODE_VERSION_INPUT": "",
                "GH_VERSION_INPUT": "",
                **inputs,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        return result, output

    return resolve


@pytest.mark.parametrize("overrides", [False, True])
def test_publication_versions_follow_build_sources(resolve_metadata, overrides):
    inputs = (
        {
            "CODEX_CLI_VERSION_INPUT": "0.161.0",
            "CLAUDE_CLI_VERSION_INPUT": "2.2.1",
            "OPENCODE_VERSION_INPUT": "1.18.13",
            "GH_VERSION_INPUT": "2.80.0",
        }
        if overrides
        else {}
    )
    result, output = resolve_metadata(**inputs)
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["image_name"] == "ghcr.io/moonladderstudios/omnigent-host-moonmind"
    assert values["codex_version"] == ("0.161.0" if overrides else "0.160.0")
    assert values["claude_version"] == ("2.2.1" if overrides else "2.2.0")
    assert values["opencode_version"] == ("1.18.13" if overrides else "1.18.12")
    assert values["gh_version"] == "2.80.0"
    assert values["version_tag"] == (
        f"{values['opencode_version']}-codex{values['codex_version']}-claude{values['claude_version']}"
    )


@pytest.mark.parametrize(
    ("inputs", "error"),
    [
        ({"GH_VERSION_INPUT": "2.79.0"}, "gh_version must match manifest.lock.json"),
        (
            {"CODEX_CLI_VERSION_INPUT": "latest"},
            "CODEX_CLI_VERSION must be an exact release version",
        ),
        (
            {"OPENCODE_VERSION_INPUT": "1.18.13\nimage_name=injected"},
            "OPENCODE_VERSION must be an exact release version",
        ),
    ],
)
def test_invalid_build_overrides_fail_before_publication(
    resolve_metadata, inputs, error
):
    result, output = resolve_metadata(**inputs)
    assert result.returncode != 0
    assert error in result.stderr
    assert not output.exists()


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
