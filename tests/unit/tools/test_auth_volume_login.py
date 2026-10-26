"""Auth helpers preserve their Docker invocation and credential mount contracts."""

import json
import os
import pty
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("runtime", ["codex", "claude"])
@pytest.mark.parametrize("path_override", [None, "VOLUME_PATH", "HOME"])
def test_login_helper_uses_omnigent_host_service(tmp_path, runtime, path_override):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$DOCKER_CALL_LOG"\n')
    docker.chmod(0o755)
    log = tmp_path / "docker.args"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_CALL_LOG": str(log),
    }
    prefix = runtime.upper()
    for suffix in ("AUTH_SERVICE", "AUTH_PROFILE", "VOLUME_PATH", "HOME"):
        env.pop(f"{prefix}_{suffix}", None)
    auth_path = f"/custom/{runtime}" if path_override else f"/home/app/.{runtime}"
    if path_override:
        env[f"{prefix}_{path_override}"] = auth_path
    env[f"{prefix}_VOLUME_NAME"] = f"selected-{runtime}-auth"
    env["CODEX_AUTH_COMMAND"] = "codex login --device-auth"
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            [
                "/bin/bash",
                str(ROOT / f"tools/auth-{runtime}-volume.sh"),
                "--login",
                "--no-register",
            ],
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
    args = log.read_text().splitlines()
    service = f"omnigent-host-{runtime}"
    assert service in args
    assert args[:4] == ["compose", "--profile", service, "run"]
    assert "temporal-worker-sandbox" not in args
    assert "1000:1000" in args
    assert f"selected-{runtime}-auth:{auth_path}" in args
    assert f"{prefix}_HOME={auth_path}" in args
    if runtime == "codex":
        assert f"CODEX_CONFIG_HOME={auth_path}" in args
        assert f"CODEX_CONFIG_PATH={auth_path}/config.toml" in args
    else:
        assert f"CLAUDE_CONFIG_DIR={auth_path}" in args


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
