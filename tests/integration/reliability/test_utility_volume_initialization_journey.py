"""Exercise the shipped initializers and auth helpers on disposable Docker volumes."""

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
IMAGE = "alpine:3.24"
SERVICES = ("agent-workspaces-init", "codex-auth-init", "claude-auth-init")
VOLUMES = ("agent_workspaces", "codex_auth_volume", "claude_auth_volume")
pytestmark = pytest.mark.reliability_journey


def _run(*args, env=None, check=True):
    return subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


@pytest.fixture
def utility_volumes(tmp_path, request):
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true"
    unavailable = pytest.fail if in_ci else pytest.skip
    if shutil.which("docker") is None:
        unavailable("requires Docker for isolated utility volume qualification")
    probe = _run("docker", "info", "--format", "{{.ServerVersion}}", check=False)
    if probe.returncode:
        unavailable(f"requires a reachable Docker daemon: {probe.stderr}")

    project = f"moonmind-test-utility-{uuid.uuid4().hex[:12]}"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CODEX_", "CLAUDE_", "COMPOSE_"))
    }
    env.update(
        {
            "MOONMIND_AGENT_WORKSPACES_VOLUME_NAME": f"{project}-workspaces",
            "CODEX_VOLUME_NAME": f"{project}-codex",
            "CLAUDE_VOLUME_NAME": f"{project}-claude",
        }
    )
    if request.param:
        env.update(
            CODEX_VOLUME_PATH="/custom/codex", CLAUDE_VOLUME_PATH="/custom/claude"
        )

    rendered = json.loads(
        _run(
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "docker-compose.yaml"),
            "--profile",
            "omnigent-host-codex",
            "--profile",
            "omnigent-host-claude",
            "config",
            "--format",
            "json",
            env=env,
        ).stdout
    )
    services = {name: rendered["services"][name] for name in SERVICES}
    volumes = {name: rendered["volumes"][name] for name in VOLUMES}
    assert all(service["image"] == IMAGE for service in services.values())
    assert all(volume["name"].startswith(f"{project}-") for volume in volumes.values())

    # Keep the actual rendered service commands/mounts, but expose only these
    # test-owned volumes to Compose so it cannot create unrelated stack resources.
    config = tmp_path / "compose.json"
    config.write_text(
        json.dumps(
            {
                "services": services,
                "volumes": volumes,
                "networks": {"default": rendered["networks"]["default"]},
            }
        )
    )
    compose = ("docker", "compose", "--project-name", project, "-f", str(config))
    try:
        yield compose, env, services, volumes
    finally:
        _run(*compose, "down", "--volumes")


@pytest.mark.parametrize(
    "utility_volumes", [False, True], indirect=True, ids=["defaults", "paths"]
)
def test_initializers_and_auth_copy_preserve_volume_bytes(utility_volumes, tmp_path):
    compose, env, services, volumes = utility_volumes

    def initialize():
        for service in SERVICES:
            _run(*compose, "run", "--rm", "--no-deps", "-T", service, env=env)

    def volume_command(volume, command, *, readonly=True):
        mount = f"{volumes[volume]['name']}:/fixture" + (":ro" if readonly else "")
        return _run(
            "docker", "run", "--rm", "-v", mount, IMAGE, "sh", "-ec", command
        ).stdout

    def assert_initialized():
        for volume in VOLUMES:
            assert (
                volume_command(volume, "stat -c '%u:%g:%a' /fixture").strip()
                == "1000:1000:775"
            )
        for directory in (
            "artifacts",
            "managed_runs",
            "managed_sessions",
            "workspaces",
        ):
            assert (
                volume_command(
                    "agent_workspaces",
                    f"stat -c '%u:%g:%a' /fixture/{directory}",
                ).strip()
                == "1000:1000:775"
            )

    initialize()
    assert_initialized()
    retained = {}
    for volume in VOLUMES:
        volume_command(
            volume,
            r"umask 077; printf 'saved\000work\377\n' > /fixture/retained.bin",
            readonly=False,
        )
        retained[volume] = volume_command(volume, "sha256sum /fixture/retained.bin")
    for _ in range(2):
        initialize()
        assert_initialized()
        for volume in VOLUMES:
            assert (
                volume_command(volume, "sha256sum /fixture/retained.bin")
                == retained[volume]
            )
            assert (
                volume_command(volume, "stat -c '%a' /fixture/retained.bin").strip()
                == "600"
            )

    for runtime, credential in (
        ("codex", "auth.json"),
        ("claude", ".credentials.json"),
    ):
        prefix = runtime.upper()
        volume = f"{runtime}_auth_volume"
        script = str(ROOT / f"tools/auth-{runtime}-volume.sh")
        missing = _run("bash", script, "--check", env=env, check=False)
        assert missing.returncode == 1
        assert volume_command(volume, "stat -c '%a' /fixture").strip() == "775"

        host = tmp_path / runtime
        host.mkdir(mode=0o700)
        (host / "cache").mkdir(mode=0o700)
        contents = {
            credential: b'{"synthetic":"test-only-credential"}\n',
            "settings.json": b'{"synthetic":true}\n',
            "cache/saved.bin": b"saved\x00credential-cache\xff\n",
        }
        for name, data in contents.items():
            (host / name).write_bytes(data)
            (host / name).chmod(0o600)
        env[f"{prefix}_AUTH_HOST_DIR"] = str(host)
        for _ in range(2):
            _run("bash", script, "--sync", "--no-register", env=env)
            _run("bash", script, "--check", env=env)
            assert (
                volume_command(volume, "stat -c '%u:%g:%a' /fixture").strip()
                == "1000:1000:700"
            )
            assert (
                volume_command(volume, "sha256sum /fixture/retained.bin")
                == retained[volume]
            )
            for name, data in contents.items():
                assert (
                    volume_command(volume, f"sha256sum /fixture/{name}").split()[0]
                    == hashlib.sha256(data).hexdigest()
                )
                assert (
                    volume_command(
                        volume, f"stat -c '%u:%g:%a' /fixture/{name}"
                    ).strip()
                    == "1000:1000:600"
                )
                assert (host / name).read_bytes() == data
                assert (host / name).stat().st_mode & 0o777 == 0o600

        target = services[f"{runtime}-auth-init"]["volumes"][0]["target"]
        assert target == env.get(f"{prefix}_VOLUME_PATH", f"/home/app/.{runtime}")

    for _ in range(2):
        initialize()
        assert_initialized()
        for volume in VOLUMES:
            assert (
                volume_command(volume, "sha256sum /fixture/retained.bin")
                == retained[volume]
            )
        for runtime in ("codex", "claude"):
            volume = f"{runtime}_auth_volume"
            _run(
                "bash",
                str(ROOT / f"tools/auth-{runtime}-volume.sh"),
                "--check",
                env=env,
            )
            for source in (tmp_path / runtime).rglob("*"):
                if source.is_file():
                    name = source.relative_to(tmp_path / runtime)
                    assert (
                        volume_command(volume, f"sha256sum /fixture/{name}").split()[0]
                        == hashlib.sha256(source.read_bytes()).hexdigest()
                    )
                    assert (
                        volume_command(
                            volume, f"stat -c '%u:%g:%a' /fixture/{name}"
                        ).strip()
                        == "1000:1000:600"
                    )
