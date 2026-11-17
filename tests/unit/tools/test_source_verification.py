import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.ci.source_verification import (
    REUSE_MAX_AGE,
    SOURCE_OWNERS,
    artifact_attempt,
    reuse_decision,
    source_receipt,
    validate_source_receipt,
)

TOOL = Path(__file__).resolve().parents[3] / "tools/ci/source_verification.py"
IDENTITY = dict(revision="a" * 40, run_id="123", attempt="2")
SELECTION = {
    **dict.fromkeys(SOURCE_OWNERS, "true"),
    "full_backend": "true",
    "backend_matrix": '{"include":[{"suite":"unit-fast","job_minutes":26}]}',
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
        ("selection", {**SELECTION, "backend_matrix": '{"include":[]}'}),
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


REPOSITORY = "fixture/moonmind"
RELEASE = "c" * 40
HEAD = "d" * 40
TREE = "e" * 40
WORKFLOW_COMMIT = "f" * 40
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
FULL_PR_SELECTION = {
    **SELECTION,
    "full_frontend": "true",
    "integration_matrix": '{"suite":["hermetic"]}',
}


def _archive(data: bytes, name: str = "receipt.json") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, data)
    return buffer.getvalue()


def _producer_fixture(candidate: str) -> dict:
    """Responses for a release whose tree a successful pre-merge run tested."""
    head = RELEASE if candidate == "merge queue" else HEAD
    run = {
        "id": 77,
        "run_attempt": 2,
        "path": ".github/workflows/pytest-unit-tests.yml",
        "event": "merge_group" if candidate == "merge queue" else "pull_request",
        "status": "completed",
        "conclusion": "success",
        "head_sha": head,
        "head_repository": {"full_name": REPOSITORY},
        "run_started_at": (NOW - timedelta(hours=2)).isoformat(),
        "html_url": "https://github.com/fixture/moonmind/actions/runs/77",
        "referenced_workflows": [
            {
                "path": f"{REPOSITORY}/.github/workflows/prepare-recovery-images.yml"
                f"@{WORKFLOW_COMMIT}",
                "sha": WORKFLOW_COMMIT,
            }
        ],
    }
    receipt = json.dumps(
        source_receipt(
            revision=head,
            run_id="77",
            attempt="2",
            selection=FULL_PR_SELECTION,
            result="success",
        ),
        indent=2,
    ).encode()
    archive = _archive(receipt)
    pulls = (
        []
        if candidate == "merge queue"
        else [{"merge_commit_sha": RELEASE, "merged_at": "x", "head": {"sha": HEAD}}]
    )
    runs = f"repos/{REPOSITORY}/actions/workflows/pytest-unit-tests.yml/runs"
    # The documented run listing omits referenced_workflows; the run's own
    # resource carries it and is the identity the decision trusts.
    listed = {key: value for key, value in run.items() if key != "referenced_workflows"}
    return {
        # Nothing tested the squash or merge commit itself before it existed.
        f"{runs}?head_sha={RELEASE}&per_page=10": {"workflow_runs": []},
        f"repos/{REPOSITORY}/git/commits/{RELEASE}": {"tree": {"sha": TREE}},
        f"repos/{REPOSITORY}/git/commits/{HEAD}": {"tree": {"sha": TREE}},
        f"repos/{REPOSITORY}/git/commits/{WORKFLOW_COMMIT}": {"tree": {"sha": TREE}},
        f"repos/{REPOSITORY}/commits/{RELEASE}/pulls": pulls,
        f"{runs}?head_sha={head}&per_page=10": {"workflow_runs": [listed]},
        f"repos/{REPOSITORY}/actions/runs/77": run,
        f"repos/{REPOSITORY}/actions/runs/77/artifacts"
        "?name=source-verification-77-2": {
            "artifacts": [
                {
                    "id": 901,
                    "name": "source-verification-77-2",
                    "expired": False,
                    "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
                }
            ]
        },
        f"repos/{REPOSITORY}/actions/artifacts/901/zip": archive,
    }


def _api(responses: dict):
    def api(path: str) -> bytes:
        if path not in responses:
            raise OSError(f"GitHub API {path} failed: HTTP 404")
        value = responses[path]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, bytes) else json.dumps(value).encode()

    return api


def _decide(responses: dict):
    return reuse_decision(
        _api(responses),
        repository=REPOSITORY,
        revision=RELEASE,
        run_id="500",
        attempt="1",
        now=NOW,
    )


def _runs_key(responses: dict) -> str:
    return next(
        key
        for key, value in responses.items()
        if "/runs?head_sha=" in key and value["workflow_runs"]
    )


def _run(responses: dict) -> dict:
    """The run resource behind the first listed candidate."""
    listed = responses[_runs_key(responses)]["workflow_runs"][0]
    return responses[f"repos/{REPOSITORY}/actions/runs/{listed['id']}"]


@pytest.mark.parametrize("candidate", ["squash", "merge", "merge queue"])
def test_matching_tree_evidence_replaces_the_source_phase(candidate):
    responses = _producer_fixture(candidate)
    if candidate == "merge":
        # A merge commit has a different SHA but the same tree as its tested head.
        responses[f"repos/{REPOSITORY}/git/commits/{RELEASE}"]["parents"] = [
            "b" * 40,
            HEAD,
        ]
    decision, receipt = _decide(responses)
    assert decision["reused"] is True, decision["rejections"]
    producer, consumer = decision["producer"], decision["consumer"]
    # The producer receipt is consumed verbatim, not re-issued by the release.
    archive = responses[f"repos/{REPOSITORY}/actions/artifacts/901/zip"]
    assert receipt == zipfile.ZipFile(io.BytesIO(archive)).read("receipt.json")
    assert json.loads(receipt)["runId"] == "77"
    assert producer["receiptDigest"] == "sha256:" + hashlib.sha256(receipt).hexdigest()
    assert producer["receiptArtifact"] == "source-verification-77-2"
    assert producer["workflowCommit"] == WORKFLOW_COMMIT
    assert producer["revision"] == (RELEASE if candidate == "merge queue" else HEAD)
    assert consumer == {
        "repository": REPOSITORY,
        "revision": RELEASE,
        "runId": "500",
        "attempt": "1",
        "tree": TREE,
    }


def _rewrite_receipt(responses: dict, change) -> None:
    archive_key = f"repos/{REPOSITORY}/actions/artifacts/901/zip"
    payload = json.loads(
        zipfile.ZipFile(io.BytesIO(responses[archive_key])).read("receipt.json")
    )
    change(payload)
    archive = _archive(json.dumps(payload).encode())
    responses[archive_key] = archive
    listing = responses[
        f"repos/{REPOSITORY}/actions/runs/77/artifacts?name=source-verification-77-2"
    ]
    listing["artifacts"][0]["digest"] = "sha256:" + hashlib.sha256(archive).hexdigest()


def _inadequate_selection(payload):
    payload["selection"] = {**payload["selection"], "full_backend": "false"}


def _apply_defect(responses: dict, defect: str) -> None:
    run = _run(responses)
    listing = responses[
        f"repos/{REPOSITORY}/actions/runs/77/artifacts?name=source-verification-77-2"
    ]
    archive_key = f"repos/{REPOSITORY}/actions/artifacts/901/zip"
    if defect == "wrong tree":
        responses[f"repos/{REPOSITORY}/git/commits/{HEAD}"] = {
            "tree": {"sha": "1" * 40}
        }
    elif defect == "changed workflow":
        # The test merge commit included base changes the release lacks.
        responses[f"repos/{REPOSITORY}/git/commits/{WORKFLOW_COMMIT}"] = {
            "tree": {"sha": "2" * 40}
        }
    elif defect == "unknown workflow":
        run["referenced_workflows"] = []
    elif defect == "changed input":
        run["event"] = "workflow_dispatch"
    elif defect == "scheduled input":
        run["event"] = "schedule"
    elif defect == "stale attempt":
        # The latest attempt's receipt is required; attempt 1 cannot stand in.
        run["run_attempt"] = 3
    elif defect in {"failed", "cancelled"}:
        run["conclusion"] = "failure" if defect == "failed" else "cancelled"
    elif defect == "in progress":
        run.update(status="in_progress", conclusion=None)
    elif defect == "expired evidence":
        run["run_started_at"] = (NOW - REUSE_MAX_AGE - timedelta(minutes=1)).isoformat()
    elif defect == "expired artifact":
        listing["artifacts"][0]["expired"] = True
    elif defect == "missing receipt":
        listing["artifacts"] = []
    elif defect == "missing digest":
        listing["artifacts"][0]["digest"] = None
    elif defect == "corrupt receipt":
        corrupt = bytearray(responses[archive_key])
        corrupt[len(corrupt) // 2] ^= 0xFF
        responses[archive_key] = bytes(corrupt)
    elif defect == "malformed receipt":
        _rewrite_receipt(responses, lambda payload: payload.clear())
    elif defect == "receipt for another run":
        _rewrite_receipt(responses, lambda payload: payload.update(runId="78"))
    elif defect == "untrusted producer":
        run["head_repository"] = {"full_name": "attacker/moonmind"}
    elif defect == "untrusted workflow":
        run["path"] = ".github/workflows/other.yml"
    elif defect == "inadequate selection":
        _rewrite_receipt(responses, _inadequate_selection)
    elif defect == "unavailable artifact":
        responses[archive_key] = OSError("GitHub API artifact download failed: 410")
    elif defect == "direct push":
        responses[f"repos/{REPOSITORY}/commits/{RELEASE}/pulls"] = []
    elif defect == "lookup unavailable":
        responses[f"repos/{REPOSITORY}/commits/{RELEASE}/pulls"] = OSError("HTTP 502")
    else:
        raise AssertionError(defect)


@pytest.mark.parametrize(
    "defect",
    [
        "wrong tree",
        "changed workflow",
        "unknown workflow",
        "changed input",
        "scheduled input",
        "stale attempt",
        "failed",
        "cancelled",
        "in progress",
        "expired evidence",
        "expired artifact",
        "missing receipt",
        "missing digest",
        "corrupt receipt",
        "malformed receipt",
        "receipt for another run",
        "untrusted producer",
        "untrusted workflow",
        "inadequate selection",
        "unavailable artifact",
        "direct push",
        "lookup unavailable",
    ],
)
def test_unproven_evidence_is_rejected_with_an_auditable_reason(defect):
    responses = _producer_fixture("squash")
    _apply_defect(responses, defect)
    decision, receipt = _decide(responses)
    assert decision["reused"] is False
    assert receipt is None and decision["producer"] is None
    assert decision["rejections"]
    assert all(item["reason"] for item in decision["rejections"])


def test_one_rejected_candidate_does_not_hide_a_complete_one():
    responses = _producer_fixture("squash")
    runs_key = _runs_key(responses)
    newer = {
        **_run(responses),
        "id": 78,
        "conclusion": "failure",
        "run_started_at": (NOW - timedelta(hours=1)).isoformat(),
    }
    responses[runs_key]["workflow_runs"].insert(0, newer)
    responses[f"repos/{REPOSITORY}/actions/runs/78"] = newer
    decision, receipt = _decide(responses)
    assert decision["reused"] is True and receipt is not None
    assert decision["producer"]["runId"] == "77"
    assert [item["runId"] for item in decision["rejections"]] == ["78"]


def test_reuse_cli_writes_the_decision_outputs_and_verbatim_receipt(tmp_path):
    responses = _producer_fixture("squash")
    # The command judges age by the real clock.
    _run(responses)["run_started_at"] = datetime.now(timezone.utc).isoformat()
    fixture = tmp_path / "responses.json"
    fixture.write_text(
        json.dumps(
            {
                key: (
                    {"base64": base64.b64encode(value).decode()}
                    if isinstance(value, bytes)
                    else value
                )
                for key, value in responses.items()
            }
        )
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    # A stand-in for `gh api PATH` serving the recorded responses.
    gh.write_text(
        f"#!{sys.executable}\n"
        "import base64, json, os, sys\n"
        "responses = json.load(open(os.environ['GH_FIXTURE']))\n"
        "value = responses.get(sys.argv[2])\n"
        "if value is None: sys.stderr.write('HTTP 404'); sys.exit(1)\n"
        "if isinstance(value, dict) and 'base64' in value:\n"
        "    sys.stdout.buffer.write(base64.b64decode(value['base64']))\n"
        "else: sys.stdout.write(json.dumps(value))\n"
    )
    gh.chmod(0o755)

    def run(revision):
        name = revision[:1]
        return subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "reuse",
                str(tmp_path / f"decision-{name}"),
                "--repository",
                REPOSITORY,
                "--revision",
                revision,
                "--run-id",
                "500",
                "--attempt",
                "1",
            ],
            env={
                **os.environ,
                "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "GH_FIXTURE": str(fixture),
                "GITHUB_OUTPUT": str(tmp_path / f"outputs-{name}"),
                "GITHUB_STEP_SUMMARY": str(tmp_path / f"summary-{name}"),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    accepted = run(RELEASE)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    folder = tmp_path / "decision-c"
    receipt = (folder / "receipt.json").read_bytes()
    archive = responses[f"repos/{REPOSITORY}/actions/artifacts/901/zip"]
    assert receipt == zipfile.ZipFile(io.BytesIO(archive)).read("receipt.json")
    digest = "sha256:" + hashlib.sha256(receipt).hexdigest()
    assert (tmp_path / "outputs-c").read_text().splitlines() == [
        "reused=true",
        "receipt_artifact=source-verification-77-2",
        f"receipt_digest={digest}",
    ]
    decision = json.loads((folder / "decision.json").read_text())
    assert decision["producer"]["runId"] == "77"
    assert "reused pre-merge verification" in (tmp_path / "summary-c").read_text()

    # An unknown commit falls back without failing the release job.
    rejected = run("9" * 40)
    assert rejected.returncode == 0, rejected.stdout + rejected.stderr
    assert (tmp_path / "outputs-9").read_text().splitlines()[0] == "reused=false"
    assert not (tmp_path / "decision-9" / "receipt.json").exists()
    assert "Candidate lookup failed" in (tmp_path / "summary-9").read_text()
