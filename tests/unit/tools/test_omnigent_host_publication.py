"""The shared host publishes verified images when its build inputs change."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/docker-publish-moonmind-host.yml"
SCRIPT = ROOT / "tools/omnigent_host_release_drift.sh"
COMPOSE = ROOT / "docker-compose.yaml"
ENV_TEMPLATE = ROOT / ".env-template"
BASE_DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "b" * 64


def test_scheduled_host_publication_only_rebuilds_on_upstream_changes():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    jobs = workflow["jobs"]
    build = next(step for step in jobs["build"]["steps"] if step.get("id") == "build")
    assert triggers["schedule"] == [{"cron": "37 */6 * * *"}]
    assert jobs["build"]["if"] == "needs.metadata.outputs.rebuild_required == 'true'"
    assert jobs["merge"]["if"] == "needs.metadata.outputs.rebuild_required == 'true'"
    assert (
        jobs["metadata"]["outputs"]["rebuild_required"]
        == "${{ steps.drift.outputs.rebuild_required }}"
    )
    drift = next(
        step for step in jobs["metadata"]["steps"] if step.get("id") == "drift"
    )
    assert "tools/omnigent_host_release_drift.sh" in drift["run"]
    assert '"$EVENT_NAME" != "schedule"' in drift["run"]
    assert build["with"]["pull"] is True
    assert not build["with"].get("no-cache")
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
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "moonmind-host:current",
            "ghcr.io/omnigent-ai/omnigent-host:latest",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == f"rebuild_required={expected}\n"


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
