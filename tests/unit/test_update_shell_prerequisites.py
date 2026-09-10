"""Host updater prerequisites fail before any deployment or checkout mutation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "script_path",
    [
        "tools/update-moonmind.sh",
        ".agents/skills/update-moonmind/scripts/run-update-moonmind.sh",
    ],
)
@pytest.mark.parametrize("missing", ["python3", "supported_python", "compose_v2"])
def test_updater_validates_host_prerequisites_before_mutation(
    tmp_path: Path, script_path: str, missing: str
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    commands = tmp_path / "commands.log"
    for executable in ("date", "id", "dirname"):
        source = shutil.which(executable)
        assert source is not None
        (fake_bin / executable).symlink_to(source)

    def executable(name: str, body: str) -> None:
        script = fake_bin / name
        script.write_text(f"#!{bash}\n{body}\n", encoding="utf-8")
        script.chmod(0o755)

    executable("git", 'printf "git %s\\n" "$*" >> "$COMMAND_LOG"; exit 0')
    executable(
        "docker",
        'printf "docker %s\\n" "$*" >> "$COMMAND_LOG"; '
        + ("exit 1" if missing == "compose_v2" else "exit 0"),
    )
    executable(
        "docker-compose",
        'printf "legacy-compose %s\\n" "$*" >> "$COMMAND_LOG"; exit 0',
    )
    if missing != "python3":
        executable("python3", "exit 1" if missing == "supported_python" else "exit 0")

    env = dict(os.environ, PATH=str(fake_bin), COMMAND_LOG=str(commands))
    result = subprocess.run(
        [bash, str(ROOT / script_path)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    if missing == "compose_v2":
        assert "Docker Compose V2 is required" in result.stderr
        assert commands.read_text().splitlines() == ["docker compose version"]
    else:
        assert "Python 3.10 or newer is required on the host" in result.stderr
        assert not commands.exists()
