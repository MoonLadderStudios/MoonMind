"""The host deadline must use GNU signal/grace semantics, not PATH lookalikes."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "tools/gnu_timeout.sh"


def _candidate(directory, name, version, status=0):
    path = directory / name
    path.write_text(
        '#!/bin/bash\nif [[ "$1" == --version ]]; then\n'
        f"  printf '%s\\n' '{version}'; exit {status}\nfi\n"
        "printf '<%s>\\n' \"$@\"\nexit 124\n"
    )
    path.chmod(0o755)
    return path


def _run(directory):
    return subprocess.run(
        ["/bin/bash", str(HELPER), "--signal=INT", "--kill-after=30s", "2s", "a b"],
        env={**os.environ, "PATH": str(directory)}, capture_output=True, text=True,
    )


@pytest.mark.parametrize("name", ["timeout", "gnutimeout", "gtimeout"])
def test_selects_available_gnu_and_preserves_arguments_and_status(tmp_path, name):
    selected = _candidate(tmp_path, name, "timeout (GNU coreutils) 9.7")
    result = _run(tmp_path)
    assert result.returncode == 124
    assert result.stdout == "<--signal=INT>\n<--kill-after=30s>\n<2s>\n<a b>\n"
    assert str(selected) in result.stderr
    assert "GNU coreutils" in result.stderr


def test_prefers_gnu_prefixed_binary_over_uutils_default(tmp_path):
    _candidate(tmp_path, "timeout", "timeout (uutils coreutils) 0.10.0")
    selected = _candidate(tmp_path, "gnutimeout", "timeout (GNU coreutils) 9.7")
    result = _run(tmp_path)
    assert result.returncode == 124
    assert str(selected) in result.stderr


@pytest.mark.parametrize(
    "version,status",
    [("timeout (uutils coreutils) 0.10.0", 0), ("timeout (GNU coreutils) 9.7", 1)],
)
def test_rejects_non_gnu_or_failed_version_probe(tmp_path, version, status):
    _candidate(tmp_path, "timeout", version, status)
    result = _run(tmp_path)
    assert result.returncode == 125
    assert not result.stdout
    assert "GNU coreutils timeout is required" in result.stderr


def test_missing_timeout_fails_without_running_unbounded(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 125
    assert not result.stdout
    assert "GNU coreutils timeout is required" in result.stderr


def test_failed_prefixed_probe_falls_back_to_verified_default(tmp_path):
    _candidate(tmp_path, "gnutimeout", "timeout (GNU coreutils) 9.7", 1)
    selected = _candidate(tmp_path, "timeout", "timeout (GNU coreutils) 9.7")
    result = _run(tmp_path)
    assert result.returncode == 124
    assert str(selected) in result.stderr
