"""Real Compose interpolation preserves operator authority during image change."""

import json
from pathlib import Path

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


@pytest.mark.parametrize("explicit", [False, True])
async def test_default_operator_origin_is_executable_after_real_compose_render(
    tmp_path, monkeypatch, explicit
):
    from moonmind.workflows.skills.deployment_surface import operator_urls

    (tmp_path / ".env").write_text(
        "MOONMIND_API_PUBLISH_HOST=127.0.0.1\nMOONMIND_PUBLIC_BASE_URL=\n"
        if explicit
        else ""
    )
    base = tmp_path / "compose.yaml"
    base.write_text(
        'services:\n  api:\n    image: example/moonmind:test\n    environment:\n      MOONMIND_PUBLIC_BASE_URL: ${MOONMIND_PUBLIC_BASE_URL:-}\n    ports:\n      - "${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}:7000:8000"\n'
    )
    for variable in ("MOONMIND_API_PUBLISH_HOST", "MOONMIND_PUBLIC_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file=str(base),
        project_name="isolated-operator-render",
    )
    result = await runner._run_compose_command(
        ("docker", "compose", "config", "--format", "json"), max_stdout_chars=None
    )
    assert result["exitCode"] == 0, result
    assert operator_urls(json.loads(result["stdout"])) == ["http://127.0.0.1:7000"]


async def test_wildcard_operator_probe_preserves_real_compose_auth_startup(
    tmp_path, monkeypatch
):
    """An update probe must not turn trusted HTTP access into cookie configuration."""
    from fastapi import FastAPI
    from api_service import main as api_main
    from moonmind.security import auth_modes_4120 as auth_modes
    from moonmind.workflows.skills.deployment_surface import operator_urls

    original = (
        "AUTH_PROVIDER=disabled\nMOONMIND_API_PUBLISH_HOST=0.0.0.0\n"
        "MOONMIND_TRUSTED_INGRESS=1\nMOONMIND_PUBLIC_BASE_URL=\n"
    )
    (tmp_path / ".env").write_text(original)
    for variable in ("AUTH_PROVIDER", "MOONMIND_API_PUBLISH_HOST", "MOONMIND_TRUSTED_INGRESS", "MOONMIND_PUBLIC_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file=str(Path(__file__).resolve().parents[3] / "docker-compose.yaml"),
        project_name="moonmind-test-operator-origin",
    )
    result = await runner._run_compose_command(
        ("docker", "compose", "config", "--format", "json"), max_stdout_chars=None
    )
    assert result["exitCode"] == 0, result
    configuration = json.loads(result["stdout"])
    # The default invocation declares nothing and still resolves the probe the
    # wildcard bind itself answers, without touching authentication settings.
    assert operator_urls(configuration) == ["http://127.0.0.1:7000"]
    assert operator_urls(configuration, declared_urls=["http://vpn.example:7000"]) == ["http://vpn.example:7000"]
    environment = configuration["services"]["api"]["environment"]
    assert environment["MOONMIND_PUBLIC_BASE_URL"] == ""
    assert (tmp_path / ".env").read_text() == original
    for key in ("AUTH_PROVIDER", "MOONMIND_API_PUBLISH_HOST", "MOONMIND_TRUSTED_INGRESS", "MOONMIND_PUBLIC_BASE_URL"):
        monkeypatch.setenv(key, environment[key])
    monkeypatch.setenv("MOONMIND_SESSION_SECRET", "release-origin-test-key-32-bytes-long")
    monkeypatch.delenv("MOONMIND_AUTH_MIGRATION_DECISION", raising=False)
    monkeypatch.setattr(api_main.settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", None)
    app = FastAPI()
    await api_main._initialize_oidc_provider(app)
    assert app.state.auth_production_mode == "disabled"


@pytest.mark.parametrize(
    "env,expected",
    [
        pytest.param("", ["http://127.0.0.1:7000"], id="fresh-install-defaults"),
        pytest.param(
            "MOONMIND_API_PUBLISH_HOST=127.0.0.1\n",
            ["http://127.0.0.1:7000"],
            id="documented-loopback",
        ),
        pytest.param(
            "MOONMIND_API_PUBLISH_HOST=192.0.2.10\nMOONMIND_TRUSTED_INGRESS=1\n",
            ["http://192.0.2.10:7000"],
            id="documented-lan-interface",
        ),
        pytest.param(
            "MOONMIND_API_PUBLISH_HOST=0.0.0.0\nMOONMIND_TRUSTED_INGRESS=1\n",
            ["http://127.0.0.1:7000"],
            id="documented-wildcard",
        ),
        pytest.param(
            "MOONMIND_API_PUBLISH_HOST=0.0.0.0\nMOONMIND_API_HOST_PORT=8800\n"
            "MOONMIND_TRUSTED_INGRESS=1\n",
            ["http://127.0.0.1:8800"],
            id="documented-wildcard-custom-port",
        ),
        pytest.param(
            "MOONMIND_PUBLIC_BASE_URL=https://moonmind.example.invalid\n",
            ["https://moonmind.example.invalid"],
            id="documented-public-base-url",
        ),
    ],
)
async def test_every_documented_binding_resolves_without_a_declared_origin(
    tmp_path, monkeypatch, env, expected
):
    """`./tools/update-moonmind.sh` with no arguments must resolve a probe target.

    Each case is a binding combination documented in README.md and .env-template.
    Requiring `--operator-url` for any of them is the defect this guards.
    """
    from moonmind.workflows.skills.deployment_surface import operator_urls

    (tmp_path / ".env").write_text(env)
    for variable in (
        "MOONMIND_API_PUBLISH_HOST",
        "MOONMIND_API_HOST_PORT",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(variable, raising=False)
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        compose_file=str(Path(__file__).resolve().parents[3] / "docker-compose.yaml"),
        project_name="moonmind-test-default-origins",
    )
    result = await runner._run_compose_command(
        ("docker", "compose", "config", "--format", "json"), max_stdout_chars=None
    )
    assert result["exitCode"] == 0, result
    assert operator_urls(json.loads(result["stdout"])) == expected
