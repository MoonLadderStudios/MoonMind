"""Replay operator network access through real Compose and API startup."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI

from api_service import main
from moonmind.deployment_access import DeploymentAccessError, validate_access
from moonmind.security import auth_modes_4120 as auth_modes

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
REPLAY = json.loads(
    (
        Path(__file__).parent
        / "reliability/replays/api-publish-binding-cutover/manifest.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "overrides,allowed",
    [
        pytest.param({}, True, id="omitted-local-default"),
        pytest.param(
            {"MOONMIND_API_PUBLISH_HOST": "", "MOONMIND_API_HOST_PORT": ""},
            True,
            id="blank-local-default",
        ),
        pytest.param(
            {
                "MOONMIND_API_PUBLISH_HOST": "127.0.0.1",
                "MOONMIND_API_HOST_PORT": "7000",
            },
            True,
            id="explicit-local-default",
        ),
        pytest.param(
            REPLAY["operatorEnvironment"], True, id="preserved-private-interface"
        ),
        pytest.param(
            {"MOONMIND_API_PUBLISH_HOST": "0.0.0.0", "MOONMIND_TRUSTED_INGRESS": "1"},
            True,
            id="explicit-authorized-wildcard",
        ),
        pytest.param(
            {"MOONMIND_API_PUBLISH_HOST": "192.0.2.10"},
            False,
            id="private-interface-without-trust",
        ),
        pytest.param(
            {"MOONMIND_API_PUBLISH_HOST": "0.0.0.0"},
            False,
            id="wildcard-without-trust",
        ),
    ],
)
async def test_rendered_binding_matches_production_startup(
    tmp_path, monkeypatch, overrides, allowed
):
    # Render the production file with an isolated project-owned .env. Never
    # read the developer's secrets or connect to their Docker daemon.
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_bytes((ROOT / "docker-compose.yaml").read_bytes())
    (tmp_path / ".env").write_text(
        "AUTH_PROVIDER=disabled\n"
        + "".join(f"{name}={value}\n" for name, value in overrides.items()),
        encoding="utf-8",
    )
    render_env = {
        name: os.environ[name]
        for name in (
            "PATH",
            "HOME",
            "USERPROFILE",
            "SYSTEMROOT",
            "SystemRoot",
            "TEMP",
            "TMP",
        )
        if name in os.environ
    }
    rendered = await asyncio.to_thread(
        subprocess.run,
        [
            "docker",
            "compose",
            "--project-name",
            "moonmind-test-api-access",
            "--env-file",
            str(tmp_path / ".env"),
            "-f",
            str(compose_path),
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env=render_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    api = json.loads(rendered.stdout)["services"]["api"]
    expected_host = (
        overrides.get("MOONMIND_API_PUBLISH_HOST") or REPLAY["defaultPublishHost"]
    )
    expected_port = overrides.get("MOONMIND_API_HOST_PORT") or REPLAY["defaultHostPort"]
    assert len(api["ports"]) == 1
    assert api["ports"][0]["host_ip"] == expected_host
    assert str(api["ports"][0]["published"]) == expected_port
    assert api["ports"][0]["target"] == 8000
    environment = api["environment"]
    # The deployed binding must survive reconciliation of the rendered config.
    previous = [
        {
            "HostConfig": {
                "PortBindings": {
                    "8000/tcp": [
                        {
                            "HostIp": expected_host,
                            "HostPort": expected_port,
                        }
                    ]
                }
            },
            "Config": {"Env": [f"{key}={value}" for key, value in environment.items()]},
        }
    ]
    candidate = json.loads(rendered.stdout)
    validate_access(candidate, previous)
    if expected_host == "127.0.0.1":
        # Replay the actual unpinned-install cutover, using the real newly
        # rendered local default rather than a synthetic replacement config.
        previous[0]["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = "0.0.0.0"
        with pytest.raises(DeploymentAccessError, match="published interfaces/ports"):
            validate_access(candidate, previous)
        previous[0]["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = (
            expected_host
        )
    previous[0]["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostPort"] = "19999"
    with pytest.raises(DeploymentAccessError, match="published interfaces/ports"):
        validate_access(candidate, previous)
    if expected_host == "127.0.0.1":
        # Replay the actual unpinned-install cutover, using the real newly
        # rendered local default rather than a synthetic replacement config.
        previous[0]["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = "0.0.0.0"
        with pytest.raises(DeploymentAccessError, match="published interfaces/ports"):
            validate_access(candidate, previous)
        previous[0]["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = (
            expected_host
        )
    assert environment["MOONMIND_API_PUBLISH_HOST"] == expected_host
    assert environment["MOONMIND_TRUSTED_INGRESS"] == overrides.get(
        "MOONMIND_TRUSTED_INGRESS", ""
    )

    # Use the rendered security inputs at the real API initialization boundary,
    # including a second startup reusing the same deployment-owned signing key.
    for name in (
        "AUTH_PROVIDER",
        "MOONMIND_API_PUBLISH_HOST",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_SESSION_SECRET",
        "JWT_SECRET",
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_AUTH_MIGRATION_DECISION",
    ):
        monkeypatch.setenv(name, environment.get(name, ""))
    monkeypatch.setattr(main.settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", None)
    key_path = tmp_path / "session-key"
    monkeypatch.setenv("MOONMIND_SESSION_KEY_PATH", str(key_path))
    if not allowed:
        with pytest.raises(RuntimeError, match="trusted-ingress"):
            await main._initialize_oidc_provider(FastAPI())
        return
    app = FastAPI()
    await main._initialize_oidc_provider(app)
    assert app.state.auth_production_mode == "disabled"
    first_key = key_path.read_bytes()
    await main._initialize_oidc_provider(FastAPI())
    assert key_path.read_bytes() == first_key
