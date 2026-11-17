"""Auth helpers preserve their Docker invocation and credential mount contracts."""

import json
import os
import pty
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HOST_IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind:latest"
IMAGE_SETTINGS = (
    "MOONMIND_OAUTH_RUNNER_IMAGE",
    "OMNIGENT_SHARED_HOST_IMAGE_REF",
    "OMNIGENT_HOST_IMAGE_REF",
    "OMNIGENT_SHARED_HOST_IMAGE",
    "OMNIGENT_SHARED_HOST_IMAGE_TAG",
)


def _write_fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_CALL_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    docker.chmod(0o755)
    return bin_dir, tmp_path / "docker.args"


def _run_login(
    tmp_path: Path,
    runtime: str,
    *,
    environment: dict[str, str] | None = None,
    dotenv: str | None = None,
) -> list[list[str]]:
    # Run a copy so the project .env beside it is the test's, not the checkout's.
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    script = repo / "tools" / f"auth-{runtime}-volume.sh"
    shutil.copy2(ROOT / "tools" / script.name, script)
    if dotenv is not None:
        (repo / ".env").write_text(dotenv)
    bin_dir, log = _write_fake_docker(tmp_path)
    prefix = runtime.upper()
    retained = {
        key: value
        for key, value in os.environ.items()
        if key not in IMAGE_SETTINGS
        and key != "CODEX_AUTH_COMMAND"
        and not key.startswith((f"{prefix}_AUTH_", f"{prefix}_VOLUME_"))
        and key != f"{prefix}_HOME"
    }
    env = {
        **retained,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_CALL_LOG": str(log),
        "TERM": "xterm-test",
        **(environment or {}),
    }
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            ["/bin/bash", str(script), "--login", "--no-register"],
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            env=env,
            timeout=15,
            check=False,
        )
    finally:
        os.close(slave)
        os.close(master)
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in log.read_text().splitlines()]


def _login_runs(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if call[:3] == ["run", "--rm", "-it"]]


@pytest.mark.parametrize("runtime", ["codex", "claude"])
@pytest.mark.parametrize("path_override", [None, "VOLUME_PATH", "HOME"])
def test_login_runs_cli_in_one_off_shared_host_container(
    tmp_path, runtime, path_override
):
    prefix = runtime.upper()
    auth_path = f"/custom/{runtime}" if path_override else f"/home/app/.{runtime}"
    volume = f"selected-{runtime}-auth"
    environment = {f"{prefix}_VOLUME_NAME": volume}
    if path_override:
        environment[f"{prefix}_{path_override}"] = auth_path
    if runtime == "codex":
        environment["CODEX_AUTH_COMMAND"] = "codex login --device-auth"

    calls = _run_login(tmp_path, runtime, environment=environment)

    # Compose services and profiles for static hosts no longer exist.
    flattened = [arg for call in calls for arg in call]
    assert "compose" not in flattened
    assert not any(f"omnigent-host-{runtime}" in arg for arg in flattened)
    assert "--profile" not in flattened

    # The volume exists and belongs to the CLI user before the login writes it.
    assert calls[0] == ["volume", "inspect", volume]
    prepare = calls[1]
    assert prepare[:-1] == [
        "run",
        "--rm",
        "--mount",
        f"type=volume,source={volume},target={auth_path}",
        "alpine:3.24",
        "sh",
        "-c",
    ]
    assert f"chown -R 1000:1000 '{auth_path}'" in prepare[-1]
    assert f"chmod 0700 '{auth_path}'" in prepare[-1]

    container = [
        "run",
        "--rm",
        "-it",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--mount",
        f"type=volume,source={volume},target={auth_path}",
        "--entrypoint",
        "/bin/bash",
        "-e",
        "HOME=/home/app",
    ]
    if runtime == "codex":
        expected = [
            *container,
            "-e",
            "TERM=xterm-test",
            "-e",
            f"CODEX_HOME={auth_path}",
            "-e",
            f"CODEX_CONFIG_HOME={auth_path}",
            "-e",
            f"CODEX_CONFIG_PATH={auth_path}/config.toml",
            DEFAULT_HOST_IMAGE,
            "-lc",
            'stty sane 2>/dev/null || true; exec "$@"',
            "--",
            "codex",
            "login",
            "--device-auth",
        ]
    else:
        expected = [
            *container,
            "-e",
            "MOONMIND_CLAUDE_CLI_AUTH_MODE=oauth",
            "-e",
            "ANTHROPIC_API_KEY=",
            "-e",
            "CLAUDE_API_KEY=",
            "-e",
            "TERM=xterm-test",
            "-e",
            f"CLAUDE_HOME={auth_path}",
            "-e",
            f"CLAUDE_CONFIG_DIR={auth_path}",
            "-e",
            f"CLAUDE_VOLUME_NAME={volume}",
            DEFAULT_HOST_IMAGE,
            "-lc",
            "unset ANTHROPIC_API_KEY CLAUDE_API_KEY; stty sane 2>/dev/null || true; "
            + 'mkdir -p "${CLAUDE_HOME:-/home/app/.claude}"; exec claude auth login',
        ]
    assert calls[2:] == [expected]


def test_codex_login_runs_each_default_command_in_its_own_container(tmp_path):
    runs = _login_runs(_run_login(tmp_path, "codex"))

    assert [run[run.index("--") + 1 :] for run in runs] == [
        ["codex", "login", "--device-auth"],
        ["codex", "login", "status"],
    ]
    assert {run[run.index("-lc") - 1] for run in runs} == {DEFAULT_HOST_IMAGE}


@pytest.mark.parametrize("runtime", ["codex", "claude"])
@pytest.mark.parametrize(
    ("environment", "dotenv", "image"),
    [
        pytest.param({}, None, DEFAULT_HOST_IMAGE, id="published-latest-default"),
        pytest.param(
            {
                "OMNIGENT_SHARED_HOST_IMAGE": "registry.test/host",
                "OMNIGENT_SHARED_HOST_IMAGE_TAG": "candidate",
            },
            None,
            "registry.test/host:candidate",
            id="image-and-tag",
        ),
        pytest.param(
            {
                "OMNIGENT_HOST_IMAGE_REF": "registry.test/legacy:1",
                "OMNIGENT_SHARED_HOST_IMAGE": "registry.test/host",
            },
            None,
            "registry.test/legacy:1",
            id="legacy-alias-over-image-and-tag",
        ),
        pytest.param(
            {
                "OMNIGENT_SHARED_HOST_IMAGE_REF": "registry.test/shared:2",
                "OMNIGENT_HOST_IMAGE_REF": "registry.test/legacy:1",
            },
            None,
            "registry.test/shared:2",
            id="shared-ref-over-legacy-alias",
        ),
        pytest.param(
            {
                "MOONMIND_OAUTH_RUNNER_IMAGE": "registry.test/runner:3",
                "OMNIGENT_SHARED_HOST_IMAGE_REF": "registry.test/shared:2",
            },
            None,
            "registry.test/runner:3",
            id="oauth-runner-override",
        ),
        pytest.param(
            {},
            'OMNIGENT_SHARED_HOST_IMAGE_REF="registry.test/from-dotenv:4"\n'
            "OMNIGENT_SHARED_HOST_IMAGE_TAG=ignored # comment\n",
            "registry.test/from-dotenv:4",
            id="project-dotenv",
        ),
        pytest.param(
            {},
            "# OMNIGENT_SHARED_HOST_IMAGE_REF=registry.test/commented:0\n"
            "OMNIGENT_SHARED_HOST_IMAGE=registry.test/host # comment\n"
            "OMNIGENT_SHARED_HOST_IMAGE_TAG='5'\n",
            "registry.test/host:5",
            id="project-dotenv-image-and-tag",
        ),
        pytest.param(
            {"OMNIGENT_SHARED_HOST_IMAGE_REF": "registry.test/shell:6"},
            'OMNIGENT_SHARED_HOST_IMAGE_REF="registry.test/from-dotenv:4"\n',
            "registry.test/shell:6",
            id="shell-over-project-dotenv",
        ),
    ],
)
def test_login_image_follows_shared_host_image_selection(
    tmp_path, runtime, environment, dotenv, image
):
    if runtime == "codex":
        environment = {**environment, "CODEX_AUTH_COMMAND": "codex login status"}

    (run,) = _login_runs(
        _run_login(tmp_path, runtime, environment=environment, dotenv=dotenv)
    )

    assert run[run.index("-lc") - 1] == image


@pytest.mark.parametrize("runtime", ["codex", "claude"])
@pytest.mark.parametrize("command", ["--sync", "--check"])
@pytest.mark.parametrize("path_override", [None, "VOLUME_PATH", "HOME"])
def test_auth_copy_helper_uses_supported_image_and_scoped_mounts(
    tmp_path, runtime, command, path_override
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_CALL_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    docker.chmod(0o755)
    log = tmp_path / "docker.args"
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    prefix = runtime.upper()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_CALL_LOG": str(log),
    }
    for suffix in ("VOLUME_PATH", "HOME"):
        env.pop(f"{prefix}_{suffix}", None)
    auth_path = f"/custom/{runtime}" if path_override else f"/home/app/.{runtime}"
    if path_override:
        env[f"{prefix}_{path_override}"] = auth_path
    volume = f"moonmind-test-{runtime}-auth"
    env[f"{prefix}_VOLUME_NAME"] = volume
    env[f"{prefix}_AUTH_HOST_DIR"] = str(credentials)

    result = subprocess.run(
        [
            "/bin/bash",
            str(ROOT / f"tools/auth-{runtime}-volume.sh"),
            command,
            "--no-register",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == ["volume", "inspect", volume]
    (run,) = [call for call in calls if call[0] == "run"]
    assert run[-4:-1] == ["alpine:3.24", "sh", "-c"]
    if command == "--sync":
        assert run[2:-4] == [
            "-v",
            f"{volume}:{auth_path}",
            "-v",
            f"{credentials}:/host-{runtime}-auth:ro",
        ]
    else:
        assert run[2:-4] == ["-v", f"{volume}:{auth_path}:ro"]
