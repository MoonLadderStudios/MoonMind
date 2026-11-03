import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools.ci import write_backend_matrix_summary as evidence


@pytest.mark.parametrize("workers", [[], ["-n", "2"]])
@pytest.mark.parametrize("subtest_fails", [False, True])
def test_receipt_requires_complete_real_collection_and_binds_identity(
    tmp_path, workers, subtest_fails
):
    test = tmp_path / "test_witness.py"
    nested = tmp_path / "nested_case.py"
    nested.write_text("def test_nested(): pass\n")
    test.write_text(
        "import os, subprocess, sys\nfrom pathlib import Path\nimport pytest\n"
        "def test_pass(subtests):\n"
        "    for value in (1, 2):\n"
        f"        with subtests.test(value=value): assert value {'== 1' if subtest_fails else '> 0'}\n"
        f"    result = subprocess.run([sys.executable, '-m', 'pytest', {str(nested)!r}, '-q', '-p', 'tools.ci.write_backend_matrix_summary'], capture_output=True)\n"
        "    assert result.returncode == 0\n"
        "    assert not Path(os.environ['MOONMIND_CI_RECEIPT']).exists(), 'nested pytest overwrote its owner receipt'\n"
        "@pytest.mark.skip(reason='external')\ndef test_skip(): pass\n"
    )
    receipt = tmp_path / "receipt.json"
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[3]),
        MOONMIND_CI_RECEIPT=str(receipt),
        MOONMIND_CI_SUITE="witness",
        MOONMIND_CI_REVISION="a" * 40,
        GITHUB_RUN_ID="123",
        GITHUB_RUN_ATTEMPT="2",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(evidence.__file__).resolve()),
            "run",
            "--expected-python",
            f"{sys.version_info.major}.{sys.version_info.minor}",
            "--",
            str(test),
            "-q",
            "-p",
            "tools.ci.write_backend_matrix_summary",
            *workers,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == (1 if subtest_fails else 0), (
        result.stdout + result.stderr
    )
    payload = json.loads(receipt.read_text())
    if subtest_fails:
        assert payload["status"] == "incomplete"
        assert any(case["outcome"] == "failed" for case in payload["cases"])
        with pytest.raises(ValueError):
            evidence.validate_receipt(
                receipt, revision="a" * 40, run_id="123", attempt="2"
            )
        return
    assert payload["status"] == "passed"
    assert payload["revision"] == "a" * 40
    assert payload["runId"] == "123" and payload["attempt"] == "2"
    assert len(payload["expectedNodeIds"]) == len(payload["cases"]) == 2
    assert {case["outcome"] for case in payload["cases"]} == {"passed", "skipped"}
    assert payload["workers"] == (2 if workers else 1)
    passed = next(case for case in payload["cases"] if case["outcome"] == "passed")
    assert len(passed["subtests"]) == 2
    assert all(item["outcome"] == "passed" for item in passed["subtests"])
    evidence.validate_receipt(receipt, revision="a" * 40, run_id="123", attempt="2")
    for field, value in (
        ("python", "2.7.18"),
        ("dependencies", [["pytest", "0.1"]]),
        ("environmentInputs", "another-test-runtime"),
    ):
        changed = deepcopy(payload)
        changed[field] = value
        receipt.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="environment"):
            evidence.validate_receipt(
                receipt, revision="a" * 40, run_id="123", attempt="2"
            )
    receipt.write_text(json.dumps(payload))
    evidence.record_environment(
        receipt,
        expected_python=f"{sys.version_info.major}.{sys.version_info.minor}",
        arguments=payload["arguments"],
        env={**env, "MOONMIND_CI_ENVIRONMENT": "another-immutable-runtime"},
    )
    with pytest.raises(ValueError, match="environment"):
        evidence.validate_receipt(receipt, revision="a" * 40, run_id="123", attempt="2")


def test_missing_or_wrong_candidate_receipt_fails_closed(tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="receipt"):
        evidence.validate_receipt(path, revision="a" * 40, run_id="123", attempt="2")
