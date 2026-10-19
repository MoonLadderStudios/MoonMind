"""Managed Tactics consumes durable receipts through the actual shell entrypoint."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".agents/skills/tactics-test/scripts/run_dood_unreal_tactics.sh"
PASSED = "Test Completed. Result={Success} Name={Ready} Path={Tactics.Unit.Ready}"


def _result(**changes):
    return {
        "job_id": "container-job:1",
        "state": "succeeded",
        "exit_code": 0,
        "failure_class": None,
        "message": None,
        "logs_ref": "artifact:logs",
        "artifacts_ref": "artifact:outputs",
        "log_tail": [PASSED],
        "log_error": None,
        **changes,
    }


@pytest.fixture
def managed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Tactics.uproject").write_text("{}")
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "Tactics.uproject"],
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "candidate",
        ],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "calls.jsonl"
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps(_result()))
    stub = bin_dir / "moonmind"
    stub.write_text(
        f"#!{sys.executable}\n"
        + """import hashlib, json, os, pathlib, sys
args = sys.argv
spec = json.loads(pathlib.Path(args[args.index('--spec') + 1]).read_text())
request_id = args[args.index('--request-id') + 1]
backend = pathlib.Path(os.environ['BACKEND'])
jobs = json.loads(backend.read_text()) if backend.exists() else {}
replayed = request_id in jobs
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps({'args': args, 'spec': spec, 'replayed': replayed}) + '\\n')
if replayed:
    assert jobs[request_id]['spec'] == spec
    result = jobs[request_id]['result']
else:
    result = json.loads(pathlib.Path(os.environ['RESULT']).read_text())
    if result.get('job_id'):
        result['job_id'] = 'container-job:' + hashlib.sha256(request_id.encode()).hexdigest()[:32]
    result['artifacts'] = []
    for arg in spec['command']:
        if arg.startswith('-abslog=') and os.environ.get('WRITE_TEST_LOG') != '0':
            relative = arg.removeprefix('-abslog=/workspace/')
            path = pathlib.Path(os.environ['MOONMIND_REPO_DIR']) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(os.environ.get('FULL_LOG', '\\n'.join(result.get('log_tail', []))))
            result['artifacts'].append({
                'name': 'test-log', 'relativePath': relative,
                'artifactRef': 'artifact:test-log', 'collectionStatus': 'collected',
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'sizeBytes': path.stat().st_size,
            })
    jobs[request_id] = {'spec': spec, 'result': result}
    backend.write_text(json.dumps(jobs))
if spec.get('outputs') and os.environ.get('ARTIFACT_ERROR'):
    result = {**result, 'artifacts': [], 'artifact_error': os.environ['ARTIFACT_ERROR']}
print(json.dumps(result))
"""
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MOONMIND_AGENT_RUN_ID": "run-1",
        "MOONMIND_RUNTIME_ID": "runtime-1",
        "MOONMIND_URL": "http://api:8000",
        "MOONMIND_REPO_DIR": str(repo),
        "CALLS": str(command_log),
        "RESULT": str(result_file),
        "BACKEND": str(tmp_path / "backend.json"),
    }

    def run(*args, result=None, full_log=None, write_log=True, artifact_error=None):
        env.pop("ARTIFACT_ERROR", None)
        if artifact_error:
            env["ARTIFACT_ERROR"] = artifact_error
        env["WRITE_TEST_LOG"] = "1" if write_log else "0"
        env.pop("FULL_LOG", None)
        if full_log is not None:
            env["FULL_LOG"] = full_log
        if result is not None:
            result_file.write_text(json.dumps(result))
        return subprocess.run(
            ["bash", str(SCRIPT), "--repo", str(repo), *args],
            env=env,
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def gate():
        return json.loads(
            (repo / ".artifacts/moonmind-unreal-tactics/latest/gate.json").read_text()
        )

    def calls():
        return (
            [json.loads(line) for line in command_log.read_text().splitlines()]
            if command_log.exists()
            else []
        )

    return repo, run, gate, calls, env


@pytest.mark.parametrize("result", [_result(log_tail=[]), {}])
def test_zero_cli_exit_without_test_evidence_is_incomplete(managed, result):
    _, run, gate, _, _ = managed
    completed = run("--phase", "test", result=result)
    assert completed.returncode != 0
    assert (gate()["status"], gate()["testStatus"]) == ("INCOMPLETE", "incomplete")


def test_managed_preserves_explicit_image_and_structured_receipt(managed):
    _, run, gate, calls, _ = managed
    image = "example.invalid/unreal:chosen"
    completed = run("--phase", "build", "--image", image)
    assert completed.returncode == 0, completed.stderr
    call = calls()[0]
    assert call["spec"]["image"] == image
    assert "imageSourceRef" not in call["spec"]
    assert "--json" in call["args"]
    assert gate()["image"] == image
    assert gate()["testStatus"] == "skipped"
    assert "testLog" not in gate()
    assert json.loads(Path(gate()["buildResult"]).read_text())["state"] == "succeeded"


def test_subdirectory_repo_maps_to_the_admitted_workspace(managed):
    repo, run, _, calls, _ = managed
    project = repo / "nested"
    project.mkdir()
    (project / "Tactics.uproject").write_text("{}")
    completed = run("--repo", str(project), "--phase", "test")
    assert completed.returncode == 0, completed.stderr
    assert calls()[0]["spec"]["workdir"] == "/workspace/nested"
    assert calls()[0]["spec"]["command"][1] == "/workspace/nested/Tactics.uproject"


def test_repo_outside_admitted_workspace_is_not_submitted(managed, tmp_path):
    _, run, _, calls, _ = managed
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "Tactics.uproject").write_text("{}")
    completed = run("--repo", str(outside))
    assert completed.returncode != 0
    assert "outside" in completed.stderr
    assert calls() == []


def test_runs_do_not_collide_and_old_artifacts_are_retained(managed):
    _, run, gate, calls, _ = managed
    assert run("--phase", "build").returncode == 0
    first = gate()
    first_log = Path(first["buildLog"]).read_bytes()
    assert run("--phase", "build").returncode == 0
    assert gate()["resultsDir"] != first["resultsDir"]
    assert Path(first["buildLog"]).read_bytes() == first_log
    ids = [c["args"][c["args"].index("--request-id") + 1] for c in calls()]
    assert len(set(ids)) == 2


def test_resume_reuses_build_and_reconciles_only_missing_test_evidence(managed):
    _, run, gate, calls, _ = managed
    first = run(artifact_error="manifest unavailable")
    assert first.returncode != 0
    current = gate()
    assert current["buildStatus"] == "pass"
    build_log = Path(current["buildLog"]).read_bytes()
    before = calls()
    completed = run("--resume", current["resultsDir"], result=_result())
    assert completed.returncode == 0, completed.stderr
    assert gate()["status"] == "PASS"
    assert gate()["resultsDir"] == current["resultsDir"]
    assert len(calls()) == 4
    assert sum(not call["replayed"] for call in calls()) == 2

    def request_id(call):
        return call["args"][call["args"].index("--request-id") + 1]

    assert request_id(calls()[-1]) == request_id(before[-1])
    assert Path(current["buildLog"]).read_bytes() == build_log


def test_resume_rejects_changed_candidate_before_any_submission(managed):
    _, run, gate, calls, _ = managed
    run(result=_result(log_tail=["Build completed"]))
    current = gate()
    before = len(calls())
    completed = run("--resume", current["resultsDir"], "--configuration", "Shipping")
    assert completed.returncode != 0
    assert "match" in completed.stderr.lower()
    assert len(calls()) == before


def test_terminal_failure_is_not_hidden_by_zero_cli_exit_or_log_error(managed):
    _, run, gate, _, _ = managed
    completed = run(
        "--phase",
        "test",
        result=_result(
            state="failed",
            failure_class="image_not_found",
            message="image absent",
            log_error="unavailable",
        ),
    )
    assert completed.returncode != 0
    assert (gate()["status"], gate()["testStatus"]) == ("FAIL", "fail")
    assert "image absent" in gate()["reason"]


def test_successful_tail_cannot_replace_missing_full_test_log(managed):
    _, run, gate, _, _ = managed
    completed = run("--phase", "test", write_log=False)
    assert completed.returncode != 0
    assert gate()["status"] == "INCOMPLETE"


def test_full_log_failure_outside_bounded_tail_still_fails(managed):
    _, run, gate, _, _ = managed
    full_log = (
        "Test Completed. Result={Fail} Name={Earlier}\n" + "noise\n" * 300 + PASSED
    )
    completed = run("--phase", "test", full_log=full_log)
    assert completed.returncode != 0
    assert (gate()["status"], gate()["testStatus"]) == ("FAIL", "fail")


def test_full_passing_log_survives_auxiliary_log_tail_error(managed):
    _, run, gate, _, _ = managed
    completed = run("--phase", "test", result=_result(log_error="unavailable"))
    assert completed.returncode == 0, completed.stderr
    assert gate()["status"] == "PASS"


@pytest.mark.parametrize("tracked", [True, False])
def test_resume_rejects_modified_source(managed, tracked):
    repo, run, gate, calls, _ = managed
    run(result=_result(log_tail=["Build completed"]))
    current = gate()
    before = len(calls())
    (repo / ("Tactics.uproject" if tracked else "NewSource.cpp")).write_text(
        "changed source"
    )
    completed = run("--resume", current["resultsDir"])
    assert completed.returncode != 0
    assert "match" in completed.stderr.lower()
    assert len(calls()) == before


def test_resume_completed_failure_does_not_repeat_any_job(managed):
    _, run, gate, calls, _ = managed
    run("--phase", "test", result=_result(state="failed", exit_code=1))
    current = gate()
    before = len(calls())
    completed = run("--phase", "test", "--resume", current["resultsDir"])
    assert completed.returncode != 0
    assert gate()["status"] == "FAIL"
    assert len(calls()) == before + 1
    assert calls()[-1]["replayed"]


def test_rejected_resume_invalidates_current_gate_without_changing_archive(managed):
    _, run, gate, calls, _ = managed
    assert run().returncode == 0
    completed = gate()
    archive = Path(completed["resultsDir"]) / "gate.json"
    archived_bytes = archive.read_bytes()
    before = len(calls())
    rejected = run("--resume", completed["resultsDir"], "--configuration", "Shipping")
    assert rejected.returncode != 0
    assert gate()["status"] == "INCOMPLETE"
    assert archive.read_bytes() == archived_bytes
    assert len(calls()) == before


@pytest.mark.parametrize("artifact_parent", [".", "Source"])
def test_custom_artifact_parent_does_not_hide_changed_source(managed, artifact_parent):
    repo, run, _, calls, _ = managed
    source = repo / artifact_parent / "NewSource.cpp"
    source.parent.mkdir(exist_ok=True)
    source.write_text("initial source")
    assert run("--results-subdir", artifact_parent).returncode == 0
    gate_file = repo / artifact_parent / "latest/gate.json"
    original = json.loads(gate_file.read_text())
    source.write_text("changed source")
    before = len(calls())
    completed = run(
        "--results-subdir", artifact_parent, "--resume", original["resultsDir"]
    )
    assert completed.returncode != 0
    assert "match" in completed.stderr.lower()
    assert len(calls()) == before


@pytest.mark.parametrize(
    "artifact_parent", [".artifacts/moonmind-unreal-tactics", ".", "Source"]
)
def test_later_run_artifacts_do_not_invalidate_resume(managed, artifact_parent):
    repo, run, _, calls, _ = managed
    assert run("--phase", "build", "--results-subdir", artifact_parent).returncode == 0
    gate_file = repo / artifact_parent / "latest/gate.json"
    original = json.loads(gate_file.read_text())
    assert run("--phase", "build", "--results-subdir", artifact_parent).returncode == 0
    before = len(calls())
    completed = run(
        "--phase",
        "build",
        "--results-subdir",
        artifact_parent,
        "--resume",
        original["resultsDir"],
    )
    assert completed.returncode == 0, completed.stderr
    assert len(calls()) == before + 1
    assert calls()[-1]["replayed"]


def test_resume_source_snapshot_frames_untracked_names_and_contents(managed):
    repo, run, gate, calls, _ = managed
    (repo / "a").write_text("bc")
    assert run("--phase", "build").returncode == 0
    original = gate()
    (repo / "a").unlink()
    (repo / "ab").write_text("c")
    before = len(calls())
    completed = run("--phase", "build", "--resume", original["resultsDir"])
    assert completed.returncode != 0
    assert "match" in completed.stderr.lower()
    assert len(calls()) == before


@pytest.mark.parametrize("change", ["symlink", "executable"])
def test_resume_source_snapshot_includes_untracked_file_type_and_mode(managed, change):
    repo, run, gate, calls, _ = managed
    source = repo / "build-helper"
    source.write_text("foo")
    assert run("--phase", "build").returncode == 0
    original = gate()
    if change == "symlink":
        source.unlink()
        source.symlink_to("foo")
    else:
        source.chmod(source.stat().st_mode | 0o100)
    before = len(calls())
    completed = run("--phase", "build", "--resume", original["resultsDir"])
    assert completed.returncode != 0
    assert "match" in completed.stderr.lower()
    assert len(calls()) == before


def test_resume_reobserves_owner_instead_of_trusting_forged_build_receipt(managed):
    _, run, gate, calls, _ = managed
    assert run(result=_result(state="failed", exit_code=1)).returncode != 0
    first = gate()
    receipt = Path(first["buildResult"])
    receipt.write_text(json.dumps(_result()))
    forged = receipt.read_bytes()
    completed = run("--resume", first["resultsDir"])
    assert completed.returncode != 0
    assert gate()["buildStatus"] == "fail"
    assert all(call["spec"]["command"][0] == "bash" for call in calls())
    assert calls()[-1]["replayed"]
    assert receipt.read_bytes() == forged


@pytest.mark.parametrize(
    "original",
    [
        "Test Completed. Result={Fail} Name={Earlier}",
        "No automation tests matched",
        "Automation began but no completion evidence",
    ],
)
def test_resume_rejects_tampered_test_log_and_forged_local_hash(managed, original):
    _, run, gate, calls, _ = managed
    assert run("--phase", "test", full_log=original).returncode != 0
    first = gate()
    log = Path(first["testLog"])
    log.write_text(PASSED)
    receipt_path = Path(first["testResult"])
    forged = json.loads(receipt_path.read_text())
    import hashlib

    forged["artifacts"][0]["sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(forged))
    saved = receipt_path.read_bytes()
    completed = run("--phase", "test", "--resume", first["resultsDir"])
    assert completed.returncode != 0
    assert gate()["status"] == "INCOMPLETE"
    assert "integrity" in gate()["reason"].lower()
    assert calls()[-1]["replayed"]
    assert sum(not call["replayed"] for call in calls()) == 1
    assert receipt_path.read_bytes() == saved
    assert log.read_text() == PASSED


def test_resume_recovers_corrupt_local_receipt_from_same_owner_job(managed):
    _, run, gate, calls, _ = managed
    assert run("--phase", "build").returncode == 0
    first = gate()
    Path(first["buildResult"]).write_text("corrupt")
    assert run("--phase", "build", "--resume", first["resultsDir"]).returncode == 0
    assert calls()[-1]["replayed"]
    assert sum(not call["replayed"] for call in calls()) == 1


def test_resume_ignores_redirected_gate_log_path(managed):
    _, run, gate, calls, _ = managed
    assert (
        run("--phase", "test", full_log="No automation tests matched").returncode != 0
    )
    first = gate()
    redirected = Path(first["resultsDir"]) / "forged.log"
    redirected.write_text(PASSED)
    first["testLog"] = str(redirected)
    (Path(first["resultsDir"]) / "gate.json").write_text(json.dumps(first))
    assert run("--phase", "test", "--resume", first["resultsDir"]).returncode != 0
    assert gate()["testStatus"] == "no_tests"
    assert calls()[-1]["replayed"]
    assert redirected.read_text() == PASSED


def test_resume_rejects_changed_run_identity_before_querying_owner(managed):
    _, run, gate, calls, _ = managed
    assert run("--phase", "build").returncode == 0
    first = gate()
    archive = Path(first["resultsDir"]) / "gate.json"
    edited = json.loads(archive.read_text())
    edited["runId"] = "f" * 32
    archive.write_text(json.dumps(edited))
    before = len(calls())
    completed = run("--phase", "build", "--resume", first["resultsDir"])
    assert completed.returncode != 0
    assert "identity" in completed.stderr.lower()
    assert gate()["status"] == "INCOMPLETE"
    assert len(calls()) == before
