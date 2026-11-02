import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import write_backend_matrix_summary as evidence


@pytest.mark.parametrize("workers", [[], ["-n", "2"]])
def test_receipt_requires_complete_real_collection_and_binds_identity(
    tmp_path, workers
):
    test = tmp_path / "test_witness.py"
    test.write_text(
        "import pytest\ndef test_pass(): pass\n@pytest.mark.skip(reason='external')\ndef test_skip(): pass\n"
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
            "-m",
            "pytest",
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
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(receipt.read_text())
    assert payload["status"] == "passed"
    assert payload["revision"] == "a" * 40
    assert payload["runId"] == "123" and payload["attempt"] == "2"
    assert len(payload["expectedNodeIds"]) == len(payload["cases"]) == 2
    assert {case["outcome"] for case in payload["cases"]} == {"passed", "skipped"}
    assert payload["workers"] == (2 if workers else 1)


def test_missing_or_wrong_candidate_receipt_fails_closed(tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="receipt"):
        evidence.validate_receipt(path, revision="a" * 40, run_id="123", attempt="2")
