"""The supported shell login helpers run vendor CLIs on Omnigent hosts."""

import os
import pty
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_login_helper_uses_omnigent_host_service(tmp_path, runtime):
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
    for key in (f"{runtime.upper()}_AUTH_SERVICE", f"{runtime.upper()}_AUTH_PROFILE"):
        env.pop(key, None)
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
