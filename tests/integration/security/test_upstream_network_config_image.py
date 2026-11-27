"""Qualify the deployment's network-config analyzer image against local fixtures.

Runs the image a deployment pulls (``batfish/allinone:latest``, or the
operator's ``MOONMIND_NETWORK_CONFIG_IMAGE``) with ``--network none`` over the
worker-sanitized weakened and corrected fixtures and MoonMind's runner, then
normalizes its answers with the production summary. Nothing is pinned; a new
release is qualified when it arrives, and a failure means that release no
longer satisfies MoonMind's adapter.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from moonmind.schemas.repository_scanner_models import NetworkConfigAssessment
from moonmind.security.network_configs import (
    RULES,
    prepare_exports,
    snapshot_files,
    summarize_network_config,
)
from moonmind.workflows.temporal.container_job_backend import _redact_strings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.skipif(
        os.environ.get("MOONMIND_RUN_UPSTREAM_ASSESSMENT_IMAGES") != "1",
        reason="pulls and runs the upstream analyzer image; dedicated CI workflow",
    ),
]

FIXTURES = Path(__file__).parents[2] / "fixtures" / "security" / "network_config"
IMAGE = os.environ.get("MOONMIND_NETWORK_CONFIG_IMAGE") or "batfish/allinone:latest"


def analyze(tmp_path, name):
    settings = NetworkConfigAssessment.model_validate(
        json.loads((FIXTURES / "assessments.json").read_text())[name]
    )
    prepared = prepare_exports(
        settings,
        {e.file_name: (FIXTURES / e.file_name).read_bytes() for e in settings.exports},
    )
    outputs = tmp_path / name
    for relative, body in snapshot_files(settings, prepared).items():
        path = outputs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    outputs.chmod(0o777)
    (outputs / settings.output_directory).chmod(0o777)
    root = f"/outputs/{settings.output_directory}"
    done = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{outputs}:/outputs",
            "--entrypoint",
            "python3",
            IMAGE,
            "-I",
            f"{root}/runner.py",
            f"{root}/snapshot",
            f"{root}/flows.json",
            f"{root}/native.json",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    # Redacted per string, as the worker does before publishing.
    native = _redact_strings(
        json.loads((outputs / settings.output_directory / "native.json").read_text())
    )
    return summarize_network_config(
        settings, prepared, native, native_ref="art_native", sanitized_refs={}
    )


def test_current_analyzer_release_distinguishes_weakened_and_corrected_exports(
    tmp_path,
):
    weakened, corrected = analyze(tmp_path, "weakened"), analyze(tmp_path, "corrected")
    assert set(weakened.checks) == set(corrected.checks) == set(RULES)
    edge = {f.rule_id for f in weakened.findings if f.file == "edge-weakened.cfg"}
    assert edge == set(RULES)
    assert corrected.findings == [] and corrected.status == "complete"
    gaps = {(gap.code, gap.file) for gap in weakened.gaps}
    assert {
        ("unparsed_lines", "core-partial.cfg"),
        ("version_not_qualified", "branch-legacy.cfg"),
        ("unsupported_format", "acme-unsupported.cfg"),
    } <= gaps
    flows = {(f.host, f.port): {d.action for d in f.decisions} for f in corrected.flows}
    assert flows[("10.10.10.20", 23)] == {"DENY"}
    assert flows[("10.10.10.20", 443)] == {"PERMIT"}
