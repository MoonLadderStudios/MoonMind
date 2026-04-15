from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_temporal_type_safety_gate_self_check_covers_acceptance_scenarios() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "tools" / "validate_temporal_type_safety.py"),
            "--self-check",
            "--json",
        ],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    findings = payload["findings"]
    by_target = {finding["target"]: finding for finding in findings}

    assert by_target["fixture:missing-compatibility-evidence"]["status"] == "fail"
    assert by_target["fixture:safe-additive-change"]["status"] == "pass"
    assert by_target["fixture:raw_dict_activity_payload"]["status"] == "fail"
    assert by_target["fixture:provider_shaped_workflow_result"]["status"] == "fail"
    assert by_target["fixture:nested_raw_bytes"]["status"] == "fail"
    assert by_target["fixture:large_workflow_history_state"]["status"] == "fail"
    assert by_target["fixture:invalid-escape-hatch"]["status"] == "fail"
    assert by_target["fixture:valid-escape-hatch"]["status"] == "pass"
    assert all(
        finding["message"] and (finding["status"] == "pass" or finding["remediation"])
        for finding in findings
    )
