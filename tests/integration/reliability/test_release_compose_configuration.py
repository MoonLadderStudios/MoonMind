"""Real Compose interpolation preserves operator authority during image change."""

import json

import pytest

from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_immutable_base_keeps_operator_env_and_override(tmp_path, monkeypatch):
    image = "example.invalid/moonmind@sha256:" + "b" * 64
    (tmp_path / ".env").write_text(
        "AUTH_PROVIDER=local\nMOONMIND_API_PUBLISH_HOST=192.0.2.4\n"
    )
    desired = tmp_path / ".env.deploy"
    desired.write_text("MOONMIND_IMAGE=" + image + "\n")
    base = tmp_path / "image-compose.yaml"
    base.write_text(
        'services:\n  api:\n    image: ${MOONMIND_IMAGE:-wrong}\n    environment:\n      AUTH_PROVIDER: ${AUTH_PROVIDER:-missing}\n    ports:\n      - "${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}:7000:8000"\n'
    )
    override = tmp_path / "docker-compose.override.yaml"
    override.write_text(
        "services:\n  api:\n    labels:\n      deployment-owner: preserved\n"
    )
    for variable in ("AUTH_PROVIDER", "MOONMIND_API_PUBLISH_HOST", "MOONMIND_IMAGE"):
        monkeypatch.delenv(variable, raising=False)
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file=str(base),
        project_name="isolated-render-only",
        env_file=str(desired),
        override_files=(str(override),),
    )
    result = await runner._run_compose_command(
        ("docker", "compose", "config", "--format", "json"), max_stdout_chars=None
    )
    assert result["exitCode"] == 0, result
    service = json.loads(result["stdout"])["services"]["api"]
    assert service["image"] == image
    assert service["environment"]["AUTH_PROVIDER"] == "local"
    assert service["ports"][0]["host_ip"] == "192.0.2.4"
    assert service["labels"]["deployment-owner"] == "preserved"


@pytest.mark.parametrize(
    "running_image,expected", [("candidate", True), ("old", False), ("", False)]
)
async def test_release_verification_joins_images_to_current_services(
    tmp_path, monkeypatch, running_image, expected
):
    """Captured Compose output: an old stopped updater must not defeat B."""

    async def inspect(_self, requested):
        return {
            "Id": "candidate",
            "RepoDigests": ["example/moonmind@sha256:" + "b" * 64],
        }

    async def records(_self, args):
        if args[0] == "images":
            return [
                {
                    "ContainerName": "installed-api",
                    "Repository": "example/moonmind",
                    "ID": running_image,
                },
                {
                    "ContainerName": "retained-worker",
                    "Repository": "example/moonmind",
                    "ID": "old",
                },
                {
                    "ContainerName": "stopped-updater",
                    "Repository": "example/moonmind",
                    "ID": "old",
                },
            ]
        assert args[0] == "ps"
        return [{"Name": "installed-api", "Service": "api", "State": "running"}]

    monkeypatch.setattr(HostDockerComposeRunner, "_inspect_image", inspect)
    monkeypatch.setattr(HostDockerComposeRunner, "_run_compose_json", records)
    result = await HostDockerComposeRunner(project_dir=str(tmp_path)).verify(
        stack="moonmind",
        requested_image="example/moonmind:candidate",
        resolved_digest=None,
    )
    assert result.succeeded is expected
    assert result.updated_services == ("api",)
    assert result.details["matchedImageCount"] == 1
