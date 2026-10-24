"""Exercise API startup without a local agent runtime or writable agent home."""

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[3] / "api_service" / "entrypoint.sh"


@pytest.mark.parametrize("reload", [False, True])
def test_api_startup_does_not_prepare_local_agent_configuration(tmp_path, reload):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A local agent bootstrap must never run before the API server.
    (bin_dir / "python").write_text("#!/bin/sh\nexit 77\n")
    (bin_dir / "uvicorn").write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\nexit 23\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["/bin/bash", str(ENTRYPOINT)],
        env={
            **os.environ,
            "PATH": str(bin_dir),
            "HOME": str(home),
            "FASTAPI_RELOAD": str(reload).lower(),
            "CODEX_TEMPLATE_PATH": str(tmp_path / "missing-template.toml"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 23, result.stderr
    assert result.stdout.splitlines()[1:] == [
        "api_service.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--no-proxy-headers",
        *(["--reload"] if reload else []),
    ]
    assert list(home.iterdir()) == []
