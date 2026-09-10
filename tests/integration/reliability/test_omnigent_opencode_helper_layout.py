"""Replay host-layout drift through the real nested attestation subprocesses."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.attestation import (
    _read_exact_host_model_options,
    _run_exact_host_opencode_command,
)
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from tests.integration.reliability.helpers import load_replay
from tests.unit.omnigent.test_generic_platform_production_services import (
    _upstream_helper_layout,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

MANIFEST = load_replay("omnigent-opencode-helper-layout", "manifest.json")
EXPECTED = load_replay("omnigent-opencode-helper-layout", "expected-outcome.json")


class LocalHostBackend:
    """Replace Docker transport and host paths, preserving production probes."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.skills = root / "skills"
        self.skills.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home)}
        script, environment = OmnigentRuntimeScriptService().build_entrypoint(
            credential_handles=[],
            skill_attachment={"targetPath": str(self.skills)},
            step_execution_id="replay-step",
        )
        # Run the real context projection setup without starting a host daemon.
        setup = script.split("exec omnigent host", 1)[0].replace(
            "/home/app", str(self.home)
        )
        subprocess.run(
            ["/bin/sh", "-c", setup],
            env={**self.env, **environment},
            check=True,
            capture_output=True,
        )

    async def run(self, argv, **kwargs):
        assert argv[:5] == [
            "docker",
            "exec",
            "replay-host",
            "/opt/venv/bin/python",
            "-c",
        ]
        source = argv[5].replace("/home/app", str(self.home))
        result = subprocess.run(
            [sys.executable, "-c", source, *argv[6:]],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=kwargs["timeout_seconds"],
        )
        return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize("layout", MANIFEST["layouts"])
async def test_admitted_host_layout_preserves_shell_and_model_probes(
    tmp_path: Path, layout: str
) -> None:
    _upstream_helper_layout(tmp_path, layout=layout)
    backend = LocalHostBackend(tmp_path)
    for status in EXPECTED["commandExitCodes"]:
        command = (
            "import os, sys; "
            # The upstream fixture filter drops the runner id; the real
            # MoonMind shim restores the mounted Skill and step projection.
            "assert 'OMNIGENT_RUNNER_ID' not in os.environ; "
            f"assert os.environ['MOONMIND_ACTIVE_SKILLS_DIR'] == {str(backend.skills)!r}; "
            "assert os.environ['MOONMIND_STEP_EXECUTION_ID'] == 'replay-step'; "
            f"print('probe reached command'); sys.exit({status})"
        )
        code, stdout, stderr = await _run_exact_host_opencode_command(
            backend=backend,
            container_name="replay-host",
            argv=[sys.executable, "-c", command],
        )
        assert code == status, stderr
        assert stdout.strip() == "probe reached command"
        assert not stderr

    result, source = await _read_exact_host_model_options(
        backend=backend,
        client=SimpleNamespace(),
        container_name="replay-host",
        omnigent_host_id="replay-host",
        harness_id="opencode-native",
    )
    assert result == {"models": [{"id": "replay/model", "layout": layout}]}
    assert source == EXPECTED["modelSource"]


@pytest.mark.parametrize("boundary", ["shell", "model"])
@pytest.mark.parametrize("fault", ["module", "dependency", "helper", "signature"])
async def test_broken_selected_layout_never_uses_other_helpers(
    tmp_path: Path, boundary: str, fault: str
) -> None:
    # Both layouts exist, but the selected 0.13 implementation is broken.
    # The usable 0.12 module must never hide that error.
    _upstream_helper_layout(tmp_path, layout="0.12")
    _upstream_helper_layout(tmp_path, layout="0.13")
    selected = tmp_path / "omnigent/harnesses/opencode_native/app_server.py"
    if fault == "module":
        selected.unlink()
    elif fault == "dependency":
        selected.write_text("import missing_upstream_dependency\n", encoding="utf-8")
    elif fault == "helper":
        selected.write_text("", encoding="utf-8")
    else:
        selected.write_text(
            "def filtered_server_env(required_positional): pass\n"
            "def list_opencode_cli_model_options(required_positional): pass\n",
            encoding="utf-8",
        )
    backend = LocalHostBackend(tmp_path)
    with pytest.raises(HarnessPlatformError) as excinfo:
        if boundary == "shell":
            await _run_exact_host_opencode_command(
                backend=backend, container_name="replay-host", argv=["true"]
            )
        else:
            await _read_exact_host_model_options(
                backend=backend,
                client=SimpleNamespace(),
                container_name="replay-host",
                omnigent_host_id="replay-host",
                harness_id="opencode-native",
            )
    assert excinfo.value.code == EXPECTED["failureCode"]
