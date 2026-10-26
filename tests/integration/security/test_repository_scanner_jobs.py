"""Admitted jobs -> sanitized native artifacts -> reports, GitHub issue #5040.

Native scanner processes are deterministic fixtures at the external binary
boundary. Git, admission, worker ingestion, artifact storage and report dispatch
are production implementations; no provider or vulnerability database fetch runs.
"""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from api_service.services.container_jobs import ContainerJobService
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security.assessments import content_key
from moonmind.security.repository_scanner_runner import run_scanner
from moonmind.security.repository_scanners import RepositoryScan, build_scanner_job
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import invoke, put, read

environment = recurring.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
MARKER = "synthetic-private-marker-5040"


def repository(root):
    root.mkdir()

    def git(*args):
        return (
            subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )

    git("init")
    git("config", "user.name", "Scanner fixture")
    git("config", "user.email", "fixture@example.invalid")
    (root / "removed.txt").write_text(MARKER + "-historical")
    git("add", ".")
    git("commit", "-m", "Historical fixture")
    (root / "removed.txt").unlink()
    for name, body in {
        "tracked.txt": MARKER + "-tracked # gitleaks:allow",
        "source.py": "eval(user_input) # nosemgrep\n",
        "requirements.txt": "fixture-dependency==1.0\n",
        "main.tf": '# trivy:ignore:AVD-EXAMPLE\nresource "example" "public" {}\n',
        "clean.txt": "A clean control\n",
        ".gitleaks.toml": '[allowlist]\npaths = [".*"]\n',
        ".gitleaksignore": "*\n",
        ".gitignore": "untracked.txt\n",
        ".semgrepignore": "*\n",
        "trivy.yaml": "scanners: []\n",
        ".trivyignore": "CVE-EXAMPLE\n",
    }.items():
        (root / name).write_text(body)
    git("add", "-A")
    git("commit", "-m", "Current fixture")
    (root / "untracked.txt").write_text(MARKER + "-untracked")
    return git


def native(scanner):
    if scanner == "gitleaks":

        def row(path):
            return {
                "RuleID": "credential",
                "File": path,
                "StartLine": 1,
                "Secret": MARKER,
                "Match": MARKER,
                "Author": MARKER,
                "Commit": "a" * 40,
            }

        return {
            "history": [row("removed.txt")],
            "worktree": [row("tracked.txt"), row("untracked.txt")],
        }
    if scanner == "opengrep":
        return {
            "scan": {
                "version": "1.2.3",
                "results": [
                    {
                        "check_id": "source",
                        "path": "source.py",
                        "start": {"line": 1, "col": 1},
                        "extra": {"lines": MARKER},
                    }
                ],
                "errors": [],
                "paths": {"scanned": ["source.py"]},
            }
        }
    return {
        "scan": {
            "SchemaVersion": 2,
            "Results": [
                {
                    "Target": "requirements.txt",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-EXAMPLE",
                            "PkgName": "fixture-dependency",
                            "InstalledVersion": "1.0",
                            "Description": MARKER,
                        }
                    ],
                },
                {
                    "Target": "main.tf",
                    "Misconfigurations": [
                        {
                            "ID": "AVD-EXAMPLE",
                            "Status": "FAIL",
                            "CauseMetadata": {"Code": {"Lines": [{"Content": MARKER}]}},
                        }
                    ],
                },
            ],
        }
    }


def install_fixture_process(tmp_path, scanner, monkeypatch, *, failure=None):
    fixture = tmp_path / (scanner + "-fixture")
    fixture.write_text(f"""#!{sys.executable}
import json, pathlib, sys, time
args=sys.argv[1:]
if args in (["--version"], ["version"]):
 print("1.2.3"); raise SystemExit(0)
scanner={scanner!r}
reports={native(scanner)!r}
if scanner == "gitleaks":
 assert "--redact=100" in args and "--ignore-gitleaks-allow" in args
 assert pathlib.Path("untracked.txt").exists()
 mode="history" if args[0]=="git" else "worktree"
 output=pathlib.Path(args[args.index("--report-path")+1])
elif scanner == "opengrep":
 assert "--disable-nosem" in args and "--no-git-ignore" in args
 assert "--x-ignore-semgrepignore-files" in args
 mode="scan"; output=pathlib.Path(args[args.index("--output")+1])
else:
 assert "--config=" in args and "--ignorefile=" in args
 assert "secret" not in args[args.index("--scanners")+1].split(",")
 assert "trivy:ignore" not in pathlib.Path("main.tf").read_text()
 mode="scan"; output=pathlib.Path(args[args.index("--output")+1])
output.write_text(json.dumps(reports[mode]))
print({MARKER!r}); print({MARKER!r}, file=sys.stderr)
if {failure!r} == "timeout": time.sleep(30)
if {failure!r} == "cancel":
 import os, signal
 os.kill(os.getppid(), signal.SIGTERM); time.sleep(30)
raise SystemExit(2 if {failure!r} == "failure" else (1 if scanner=="gitleaks" else 0))
""")
    fixture.chmod(0o755)
    original = subprocess.Popen

    def popen(argv, *args, **kwargs):
        if argv[0] == scanner:
            argv = [str(fixture), *argv[1:]]
        elif len(argv) > 5 and argv[5] == scanner:
            argv = [*argv[:5], str(fixture), *argv[6:]]
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)


@pytest.mark.parametrize("scanner", ["gitleaks", "opengrep", "trivy"])
@pytest.mark.parametrize("history", ["complete", "shallow", "missing"])
async def test_repository_job_retains_sanitized_evidence_and_report(
    environment, tmp_path, monkeypatch, scanner, history
):
    root = tmp_path / "repository"
    git = repository(root)
    if history == "shallow":
        (root / ".git" / "shallow").write_text(git("rev-parse", "HEAD") + "\n")
    elif history == "missing":
        import shutil

        shutil.rmtree(root / ".git")
    before = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }
    options = {}
    if scanner == "opengrep":
        rules = tmp_path / "rules" / "source.yaml"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text("rules: []\n")
        options["rules"] = [
            {
                "id": "source",
                "path": "/opt/moonmind-scanner-rules/source.yaml",
                "sha256": hashlib.sha256(rules.read_bytes()).hexdigest(),
                "license": "MIT",
            }
        ]
    db = tmp_path / "database"
    db.mkdir()
    (db / "fixture-db.json").write_text('{"CVE-EXAMPLE":"1.0"}')
    if scanner == "trivy":
        options.update(
            databaseCacheRef="fixture-db",
            databaseDigest=content_key(
                [
                    [
                        "fixture-db.json",
                        hashlib.sha256(
                            (db / "fixture-db.json").read_bytes()
                        ).hexdigest(),
                    ]
                ]
            ),
        )
    scan = RepositoryScan(scanner=scanner, outputDirectory="artifacts/scan", **options)
    install_fixture_process(tmp_path, scanner, monkeypatch)
    code = run_scanner(
        scan,
        source=root,
        output=root / scan.output_directory,
        database=db,
        rules_root=tmp_path / "rules",
    )
    assert before == {name: (root / name).read_bytes() for name in before}
    execution = json.loads(
        (root / scan.output_directory / "execution.json").read_bytes()
    )
    if scanner == "gitleaks":
        assert (
            "untracked.txt" in execution["untrackedFiles"]
            if history != "missing"
            else not execution["gitMetadata"]
        )
        assert execution["shallow"] is True if history == "shallow" else True
    assert all(
        MARKER.encode() not in p.read_bytes()
        for p in (root / scan.output_directory).iterdir()
    )

    # Admit through the production service, then drive the actual worker's
    # process boundary and artifact ingestion using deterministic native data.
    workload = build_scanner_job(scan)
    workload.pop("imageSourceRef")
    workload["image"] = "python:3.13"
    workload["workspaceRef"] = {"kind": "sandbox", "workspaceId": "repository"}
    submission = ContainerJobSubmitRequest(
        idempotencyKey=f"issue-5040-{scanner}-{history}",
        source={"source": "workflow", "workflowId": "assessment"},
        spec=workload,
    )
    owner = OwnerIdentity(principalType="user", principalId="operator")
    temporal = AsyncMock()
    jobs = ContainerJobService(environment.session, temporal=temporal)
    accepted = await jobs.submit(owner=owner, request=submission)
    published = {}

    async def publish(_request, name, payload):
        assert MARKER.encode() not in payload
        ref = await put(environment.service, payload)
        published[name] = (ref, payload)
        return ref

    async def runner(argv):
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return 0, f"{timestamp} {MARKER}\n".encode(), b""

    request = ContainerJobActivityRequest(
        jobId=accepted.job_id,
        ownershipToken=f"{accepted.job_id}:v1",
        request=submission,
        resolvedWorkspaceRef=str(root),
        containerRef="fixture-container",
        terminalState="succeeded" if code == 0 else "failed",
        exitCode=code,
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publish,
        log_spool_root=tmp_path / "live",
    )
    cursor = await backend._collect_live_logs(request, request.container_ref)
    assert cursor
    assert any((tmp_path / "live").rglob("*"))
    for path in (tmp_path / "live").rglob("*"):
        if path.is_file():
            assert MARKER.encode() not in path.read_bytes()
    result = await backend.publish_evidence(request)
    summary_ref = published[f"{accepted.job_id}-output-summary.json"][0]
    summary = await read(environment.service, summary_ref)
    assert any(row["findings"] for row in summary["results"].values())
    partial = scanner == "gitleaks" and history != "complete"
    assert summary["coverage"] == ("partial" if partial else "complete")
    assets = sorted(
        set(execution["assets"])
        | {f["asset"] for row in summary["results"].values() for f in row["findings"]}
    )
    outputs = await invoke(
        environment.service,
        {
            "scope": {"assets": assets},
            "tools": ["retained-report"],
            "reportRefs": [summary_ref],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
        },
    )
    assert outputs["counts"]["new"] == len(
        {f["nativeId"] for row in summary["results"].values() for f in row["findings"]}
    )
    assert outputs["coverageStatus"] == ("stale_or_partial" if partial else "complete")
    assert not outputs["diagnostics"]
    assert outputs["scannerSummaries"][0]["nativeRefs"]
    record = await jobs.repository.get_for_owner(owner=owner, job_id=accepted.job_id)
    record.logs_ref = result.logs_ref
    record.artifacts_ref = result.artifacts_ref
    await environment.session.flush()
    # The tool's retained log and artifact surfaces must remain safe as well.
    jobs._read_evidence_text = lambda **kw: _evidence_text(
        environment.service, kw["artifact_ref"]
    )
    assert (
        MARKER
        not in (await jobs.logs(owner=owner, job_id=accepted.job_id)).model_dump_json()
    )
    assert (
        MARKER
        not in (
            await jobs.artifacts(owner=owner, job_id=accepted.job_id)
        ).model_dump_json()
    )


async def _evidence_text(service, ref):
    _, raw = await service.read(
        artifact_id=ref, principal="operator", allow_restricted_raw=True
    )
    return raw.decode()


@pytest.mark.parametrize("failure", ["timeout", "failure", "cancel"])
async def test_scanner_process_failure_retains_partial_native_findings(
    tmp_path, monkeypatch, failure
):
    root = tmp_path / "repository"
    repository(root)
    scan = RepositoryScan(
        scanner="gitleaks", outputDirectory="artifacts/scan", timeoutSeconds=1
    )
    install_fixture_process(tmp_path, "gitleaks", monkeypatch, failure=failure)
    assert run_scanner(scan, source=root, output=root / scan.output_directory) == 2
    execution = json.loads(
        (root / scan.output_directory / "execution.json").read_bytes()
    )
    if failure == "timeout":
        assert "timed_out" in execution["states"].values()
    if failure == "cancel":
        assert "canceled" in execution["states"].values()
    assert (root / scan.output_directory / "history.json").exists()
    assert MARKER not in (root / scan.output_directory / "history.json").read_text()
