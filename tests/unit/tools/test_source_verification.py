import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools.ci.source_verification import (
    SOURCE_OWNERS,
    artifact_attempt,
    source_receipt,
    validate_source_receipt,
)

TOOL = Path(__file__).resolve().parents[3] / "tools/ci/source_verification.py"
IDENTITY = dict(revision="a" * 40, run_id="123", attempt="2")
SELECTION = {
    **dict.fromkeys(SOURCE_OWNERS, "true"),
    "full_backend": "true",
    "backend_matrix": '{"include":[{"suite":"unit-fast","job_minutes":21}]}',
}


def test_retained_job_artifacts_require_an_explicit_admitted_attempt():
    assert (
        artifact_attempt(
            "source-verification-123-1",
            prefix="source-verification",
            run_id="123",
            current_attempt="2",
        )
        == "1"
    )
    for name in (
        "source-verification-123-3",
        "source-verification-124-1",
        "source-verification-123-0",
        "source-verification-123-1\n",
    ):
        with pytest.raises(ValueError):
            artifact_attempt(
                name, prefix="source-verification", run_id="123", current_attempt="2"
            )


def test_complete_current_source_is_reusable_only_for_the_same_run_and_attempt():
    receipt = source_receipt(**IDENTITY, selection=SELECTION, result="success")
    validate_source_receipt(receipt, **IDENTITY, selection=SELECTION)
    for key, value in (
        ("revision", "b" * 40),
        ("runId", "124"),
        ("attempt", "1"),
        ("status", "skipped"),
        ("owners", list(SOURCE_OWNERS)[:-1]),
        ("selectionDigest", "sha256:" + "0" * 64),
    ):
        changed = deepcopy(receipt)
        changed[key] = value
        with pytest.raises(ValueError):
            validate_source_receipt(changed, **IDENTITY, selection=SELECTION)


def test_receipt_is_bound_to_the_admitted_selection():
    receipt = source_receipt(**IDENTITY, selection=SELECTION, result="success")
    narrowed = {**SELECTION, "backend_matrix": '{"include":[]}'}
    with pytest.raises(ValueError):
        validate_source_receipt(receipt, **IDENTITY, selection=narrowed)
    incomplete = {**SELECTION, "frontend_static": "false"}
    with pytest.raises(ValueError):
        validate_source_receipt(receipt, **IDENTITY, selection=incomplete)


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_incomplete_source_never_yields_a_receipt(result):
    with pytest.raises(ValueError):
        source_receipt(**IDENTITY, selection=SELECTION, result=result)


def test_an_omitted_owner_is_not_covered_by_an_aggregate_success():
    for missing in SOURCE_OWNERS:
        selection = dict(SELECTION)
        selection[missing] = "false"
        with pytest.raises(ValueError):
            source_receipt(**IDENTITY, selection=selection, result="success")


def _cli(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(tmp_path, *, attempt="2"):
    path = tmp_path / f"receipt-{attempt}.json"
    result = _cli(
        "write",
        str(path),
        "--revision",
        IDENTITY["revision"],
        "--run-id",
        IDENTITY["run_id"],
        "--attempt",
        attempt,
        "--selection",
        json.dumps(SELECTION),
        "--result",
        "success",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return path, "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _verify(
    path,
    digest,
    *,
    selection=json.dumps(SELECTION),
    artifact="source-verification-123-2",
    revision=IDENTITY["revision"],
    run_id=IDENTITY["run_id"],
):
    args = [
        "verify",
        str(path),
        "--revision",
        revision,
        "--run-id",
        run_id,
        "--attempt",
        IDENTITY["attempt"],
        "--artifact",
        artifact,
        "--selection",
        selection,
    ]
    if digest is not None:
        args += ["--digest", digest]
    return _cli(*args)


def test_verify_accepts_the_exact_receipt_bytes_of_an_admitted_attempt(tmp_path):
    current, current_digest = _write(tmp_path)
    assert _verify(current, current_digest).returncode == 0
    # Re-running failed release jobs reuses the retained earlier receipt.
    earlier, earlier_digest = _write(tmp_path, attempt="1")
    result = _verify(earlier, earlier_digest, artifact="source-verification-123-1")
    assert result.returncode == 0, result.stdout + result.stderr


def _defect(tmp_path, defect):
    path, digest = _write(tmp_path)
    if defect == "missing digest":
        return _verify(path, None)
    if defect == "wrong digest":
        return _verify(path, "sha256:" + "0" * 64)
    if defect == "corrupt":
        path.write_bytes(path.read_bytes() + b" ")
        return _verify(path, digest)
    if defect == "malformed":
        path.write_text("{not json")
        return _verify(path, "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
    if defect == "unavailable":
        path.unlink()
        return _verify(path, digest)
    if defect == "stale attempt":
        earlier, earlier_digest = _write(tmp_path, attempt="1")
        # The handoff names this attempt; the earlier receipt cannot stand in.
        return _verify(earlier, earlier_digest)
    if defect == "stale bytes":
        earlier, _ = _write(tmp_path, attempt="1")
        return _verify(earlier, digest)
    if defect == "wrong revision":
        return _verify(path, digest, revision="b" * 40)
    if defect == "wrong run":
        return _verify(path, digest, run_id="124", artifact="source-verification-124-2")
    if defect == "wrong selection":
        narrowed = {**SELECTION, "backend_matrix": '{"include":[]}'}
        return _verify(path, digest, selection=json.dumps(narrowed))
    if defect == "missing selection":
        return _verify(path, digest, selection="")
    raise AssertionError(defect)


@pytest.mark.parametrize(
    "defect",
    [
        "missing digest",
        "wrong digest",
        "corrupt",
        "malformed",
        "unavailable",
        "stale attempt",
        "stale bytes",
        "wrong revision",
        "wrong run",
        "wrong selection",
        "missing selection",
    ],
)
def test_verify_fails_closed_with_a_readable_diagnostic(tmp_path, defect):
    result = _defect(tmp_path, defect)
    assert result.returncode != 0
    assert "::error::" in result.stdout, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
