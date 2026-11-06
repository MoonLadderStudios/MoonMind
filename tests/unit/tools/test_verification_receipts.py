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
    for case in payload["cases"]:
        # Attribution and wall-clock span feed the advisory timing digest.
        assert case["worker"] in ({"gw0", "gw1"} if workers else {"main"})
        assert 0 < case["start"] <= case["stop"]
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


def _timed_case(nodeid, worker, start, setup, call, teardown):
    return {
        "nodeid": nodeid,
        "outcome": "passed",
        "worker": worker,
        "start": start,
        "stop": start + setup + call + teardown,
        "phases": {
            "setup": {"outcome": "passed", "seconds": setup},
            "call": {"outcome": "passed", "seconds": call},
            "teardown": {"outcome": "passed", "seconds": teardown},
        },
    }


def test_timing_digest_reports_phases_workers_and_slowest_files(tmp_path):
    main = tmp_path / "main.receipt.json"
    main.write_text(
        json.dumps(
            {
                "status": "passed",
                "workers": 2,
                "collectionSeconds": 12.0,
                "elapsedSeconds": 100.0,
                "cases": [
                    _timed_case("tests/a.py::test_one", "gw0", 1000.0, 5.0, 20.0, 1.0),
                    _timed_case("tests/a.py::test_two", "gw0", 1026.0, 4.0, 30.0, 2.0),
                    _timed_case("tests/b.py::test_one", "gw1", 1000.0, 1.0, 9.0, 0.0),
                ],
            }
        )
    )
    seed = tmp_path / "storage-seed.receipt.json"
    seed.write_text(
        json.dumps(
            {
                "status": "passed",
                "workers": 1,
                "collectionSeconds": 1.0,
                "elapsedSeconds": 3.0,
                "cases": [
                    _timed_case("tests/s.py::test_seed", "main", 5.0, 0.5, 2.0, 0.1)
                ],
            }
        )
    )

    digest = evidence.timing_digest(
        [main, seed, tmp_path / "storage-verify.receipt.json"],
        resources={"cpus": 4, "memoryGiB": 15.6},
    )

    assert "cpus=4, memoryGiB=15.6" in digest
    assert (
        "| `main.receipt.json` | passed | 3 | 2 | 12.00s | 100.00s | 10.00s | 59.00s | 3.00s |"
        in digest
    )
    assert "| `storage-seed.receipt.json` | passed | 1 | 1 |" in digest
    assert (
        "| `storage-verify.receipt.json` | unavailable: FileNotFoundError |" in digest
    )
    # gw0 is the critical worker; gw1 idles for the 52s it finishes early.
    assert "| gw0 | 2 | 1 | 62.00s | 62.00s | 0.00s |" in digest
    assert "| gw1 | 1 | 1 | 10.00s | 10.00s | 52.00s |" in digest
    files = digest[digest.index("| file |") :]
    assert files.index("`tests/a.py`") < files.index("`tests/b.py`")
    assert "| `tests/a.py` | gw0 | 2 | 9.00s | 50.00s | 3.00s | 62.00s |" in digest


def test_timing_digest_tolerates_receipts_without_worker_attribution(tmp_path):
    receipt = tmp_path / "main.receipt.json"
    case = _timed_case("tests/a.py::test_one", "gw0", 1.0, 1.0, 1.0, 1.0)
    for field in ("worker", "start", "stop"):
        case.pop(field)
    receipt.write_text(json.dumps({"status": "passed", "workers": 1, "cases": [case]}))

    digest = evidence.timing_digest([receipt], resources={})

    assert "worker attribution unavailable" in digest
    assert "| `tests/a.py` | unknown | 1 |" in digest


def test_digest_command_is_advisory_and_appends_the_summary(tmp_path, capsys):
    summary = tmp_path / "summary.md"
    receipt = tmp_path / "main.receipt.json"
    receipt.write_text("{not json")

    status = evidence.digest_main(["--summary", str(summary), str(receipt)])

    assert status == 0
    out = capsys.readouterr().out
    assert "unavailable: JSONDecodeError" in out
    assert summary.read_text(encoding="utf-8") == out
