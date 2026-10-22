from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "pytest-unit-tests.yml"
)


def _os_dependency_script() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["integration-ci"]["steps"]
    matches = [
        step
        for step in steps
        if step.get("name") == "Install the journey browser OS dependencies"
    ]
    assert len(matches) == 1, "Missing dedicated journey OS dependency setup step"
    return matches[0]["run"]


@pytest.fixture
def setup_harness(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    """Run the workflow shell without privilege, package installs, or network."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    capture = tmp_path / "node-call.json"
    apt_directory = tmp_path / "temporary apt config"
    apt_directory.mkdir()
    scripts = {
        "sudo": """
import os
import sys

# Model sudo's environment reset. The workflow must explicitly forward its
# per-command APT_CONFIG, rather than relying on an inherited environment.
environment = {
    key: value
    for key, value in os.environ.items()
    if key in {"PATH", "SETUP_CAPTURE", "SETUP_EXIT", "SETUP_WAIT"}
}
assert sys.argv[1] == "env", sys.argv
os.execvpe(sys.argv[1], sys.argv[1:], environment)
""",
        "node": """
import json
import os
import sys
import time
from pathlib import Path

config = Path(os.environ["APT_CONFIG"])
capture = Path(os.environ["SETUP_CAPTURE"])
pending = capture.with_suffix(".pending")
pending.write_text(json.dumps({
    "argv": sys.argv[1:],
    "config_path": str(config),
    "config": config.read_text(),
}))
pending.replace(capture)
if os.environ.get("SETUP_WAIT") == "1":
    time.sleep(60)
sys.exit(int(os.environ.get("SETUP_EXIT", "0")))
""",
    }
    for name, body in scripts.items():
        executable = binaries / name
        executable.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        executable.chmod(0o755)
    environment = {
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        "SETUP_CAPTURE": str(capture),
    }
    return environment, capture, apt_directory


def _assert_setup_call(capture: Path, apt_directory: Path) -> None:
    recorded = json.loads(capture.read_text(encoding="utf-8"))
    assert recorded["argv"] == [
        "node_modules/playwright/cli.js",
        "install-deps",
        "chromium",
    ]
    # Assert the configuration the privileged child actually reads, not shell
    # spelling or comments. Both HTTP and HTTPS mirrors must be bounded.
    options = dict(re.findall(r'(Acquire::[\w:]+)\s+"(\d+)"\s*;', recorded["config"]))
    assert options == {
        "Acquire::Retries": "5",
        "Acquire::http::Timeout": "30",
        "Acquire::https::Timeout": "30",
    }
    config_path = Path(recorded["config_path"])
    assert config_path.parent == apt_directory
    assert not config_path.exists(), "APT retry settings leaked after setup ended"
    assert list(apt_directory.iterdir()) == []


@pytest.mark.parametrize("exit_code", [0, 71], ids=["success", "install-failure"])
@pytest.mark.parametrize("temp_variable", ["RUNNER_TEMP", "TMPDIR"])
def test_os_dependency_setup_scopes_retries_and_preserves_exit_status(
    tmp_path: Path,
    setup_harness: tuple[dict[str, str], Path, Path],
    temp_variable: str,
    exit_code: int,
) -> None:
    script = _os_dependency_script()
    environment, capture, apt_directory = setup_harness
    environment[temp_variable] = str(apt_directory)
    environment["SETUP_EXIT"] = str(exit_code)

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == exit_code, result.stdout + result.stderr
    _assert_setup_call(capture, apt_directory)


def test_os_dependency_setup_cleans_up_after_termination(
    tmp_path: Path,
    setup_harness: tuple[dict[str, str], Path, Path],
) -> None:
    script = _os_dependency_script()
    environment, capture, apt_directory = setup_harness
    environment["RUNNER_TEMP"] = str(apt_directory)
    environment["SETUP_WAIT"] = "1"

    process = subprocess.Popen(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not capture.exists() and process.poll() is None:
            assert time.monotonic() < deadline, "Setup did not reach the Node child"
            time.sleep(0.01)
        assert capture.exists(), "Setup exited before invoking the Node child"
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 143, stdout + stderr
        _assert_setup_call(capture, apt_directory)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
