"""Keep a lost xdist worker's test identity visible before session shutdown."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("distributed", [True, False])
def test_crash_identity_is_reported_without_changing_pytest_outcome(
    tmp_path: Path, distributed: bool
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    test_source = "def test_survivor():\n    assert True\n"
    if distributed:
        test_source = (
            "import os\n"
            "def test_crash():\n    os._exit(1)\n"
            + test_source
            + "def test_other_survivor():\n    assert True\n"
        )
    (tmp_path / "test_worker.py").write_text(test_source)
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = str(repo_root)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "tests.conftest",
        "-q",
        "test_worker.py",
    ]
    if distributed:
        # The regression exercises the crash hook with real workers. Use load
        # here because xdist 3.8.0's loadfile crash recovery can itself deadlock.
        command.extend(["-p", "xdist.plugin", "-n", "1", "--dist", "load"])
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout
    diagnostics = [
        line for line in output.splitlines() if line.startswith("xdist-crash ")
    ]
    if distributed:
        assert result.returncode == pytest.ExitCode.TESTS_FAILED, output
        assert diagnostics == ["xdist-crash gw0 test_worker.py::test_crash"], output
        assert output.index(diagnostics[0]) < output.index("FAILURES"), output
        assert "1 failed, 2 passed" in result.stdout, output
        assert (
            "worker 'gw0' crashed while running 'test_worker.py::test_crash'" in output
        )
    else:
        assert result.returncode == pytest.ExitCode.OK, output
        assert diagnostics == [], output
        assert "1 passed" in result.stdout, output
