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
from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security.repository_scanner_runner import run_scanner
from moonmind.security.repository_scanners import RepositoryScan, build_scanner_job
from moonmind.security.scanner_feeds import publish_snapshot
from moonmind.workflows.temporal.container_job_attachments import storage_for
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
    (root / (MARKER + ".txt")).write_text(MARKER)
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
            "worktree": [
                row("tracked.txt"),
                row("untracked.txt"),
                row(MARKER + ".txt"),
            ],
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


def install_fixture_process(
    tmp_path, scanner, monkeypatch, *, failure=None, reports=None
):
    fixture = tmp_path / (scanner + "-fixture")
    fixture.write_text(
        f"""#!{sys.executable}
import json, pathlib, sys, time
args=sys.argv[1:]
if args in (["--version"], ["version"]):
 print("1.2.3"); raise SystemExit(0)
scanner={scanner!r}
reports={native(scanner) if reports is None else reports!r}
if scanner == "gitleaks":
 assert "--redact=100" in args and "--ignore-gitleaks-allow" in args
 assert pathlib.Path("untracked.txt").exists()
 mode="history" if args[0]=="git" else "worktree"
 output=pathlib.Path(args[args.index("--report-path")+1])
elif scanner == "opengrep":
 assert "--disable-nosem" in args and "--no-git-ignore" in args
 assert "--x-ignore-semgrepignore-files" in args
 assert not [a for a in args if a.startswith("--metrics")]
 mode="scan"; output=pathlib.Path(args[args.index("--output")+1])
else:
 assert "--config=" in args and "--ignorefile=" in args
 assert "secret" not in args[args.index("--scanners")+1].split(",")
 assert "trivy:ignore" not in pathlib.Path("main.tf").read_text()
 mode="scan"; output=pathlib.Path(args[args.index("--output")+1])
body=reports[mode]
output.write_bytes(body if isinstance(body, bytes) else json.dumps(body).encode())
print({MARKER!r}); print({MARKER!r}, file=sys.stderr)
if {failure!r} == "timeout": time.sleep(30)
if {failure!r} == "cancel":
 import os, signal
 os.kill(os.getppid(), signal.SIGTERM); time.sleep(30)
raise SystemExit(2 if {failure!r} == "failure" else (1 if scanner=="gitleaks" and reports[mode] else 0))
"""
    )
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
    if scanner == "trivy":
        # A refresh job's published snapshot, selected by content identity.
        stage = db / "staging" / "fixture"
        (stage / "db").mkdir(parents=True)
        (db / "snapshots").mkdir()
        (stage / "db" / "trivy.db").write_text('{"CVE-EXAMPLE":"1.0"}')
        now = datetime.now(UTC).isoformat()
        (stage / "db" / "metadata.json").write_text(
            json.dumps({"Version": 2, "UpdatedAt": now, "DownloadedAt": now})
        )
        snapshot, _ = publish_snapshot(
            db,
            stage,
            components=["vulnerability"],
            acquisition="import",
            upstreams={"vulnerability": "artifact:art_fixture"},
            now=datetime.now(UTC),
        )
        options.update(
            databaseCacheRef="fixture-db", databaseDigest=snapshot.snapshot_id
        )
    scan = RepositoryScan(
        scanner=scanner, outputDirectory=f"artifacts/scan-{MARKER}", **options
    )
    install_fixture_process(tmp_path, scanner, monkeypatch)

    # Admit through the production service, then drive the actual worker's
    # launch, process boundary and artifact ingestion with deterministic data.
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
        if argv[0] == "inspect":
            return 1, b"", b"no such container"
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return 0, f"{timestamp} {MARKER}\n".encode(), b""

    request = ContainerJobActivityRequest(
        jobId=accepted.job_id,
        ownershipToken=f"{accepted.job_id}:v1",
        request=submission,
        resolvedWorkspaceRef=str(root),
        resolvedImageRef="sha256:" + "a" * 64,
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publish,
        log_spool_root=tmp_path / "live",
        # The deployment declares the admitted read-only database cache.
        settings=resolve_container_backend_settings(
            {
                CACHE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "cacheRef": "fixture-db",
                            "volumeName": "fixture-db",
                            "target": "/opt/moonmind-scanner-db",
                            "readOnly": True,
                        }
                    ]
                )
            }
        ),
    )
    await backend.create_container(request)
    storage = storage_for(tmp_path, accepted.job_id)
    output = storage.outputs / scan.output_directory
    code = run_scanner(
        scan,
        source=storage.inputs / "source",
        output=output,
        database=db,
        rules_root=tmp_path / "rules",
    )
    assert before == {name: (root / name).read_bytes() for name in before}
    assert not (root / scan.output_directory).exists()
    execution = json.loads((output / "execution.json").read_bytes())
    if scanner == "gitleaks":
        assert (
            "untracked.txt" in execution["untrackedFiles"]
            if history != "missing"
            else not execution["gitMetadata"]
        )
        assert execution["shallow"] is True if history == "shallow" else True
    assert all(MARKER.encode() not in p.read_bytes() for p in output.iterdir())
    request.container_ref = "fixture-container"
    request.terminal_state = "succeeded" if code == 0 else "failed"
    request.exit_code = code
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
    # Source and IaC/dependency tools examine selected native targets within the
    # larger snapshot. The report must disclose that broader requested scope.
    report_partial = partial or scanner in {"opengrep", "trivy"}
    assert outputs["coverageStatus"] == (
        "stale_or_partial" if report_partial else "complete"
    )
    assert not outputs["diagnostics"]
    assert outputs["scannerSummaries"][0]["nativeRefs"]
    if scanner == "gitleaks" and history == "missing":
        assert outputs["scannerSummaries"][0]["execution"] == {
            "history": "failed",
            "worktree": "succeeded",
        }
        assert "container_job_failed" in outputs["scannerSummaries"][0]["limitations"]
    assert MARKER not in json.dumps(outputs)
    # Read the actual retained Markdown and structured report bytes, including
    # their evidence, through the artifact owner's authorized read boundary.
    for ref in {
        outputs["scanRef"],
        outputs["comparisonRef"],
        outputs["reportBundle"]["primary_report_ref"]["artifact_id"],
    }:
        _, body = await environment.service.read(
            artifact_id=ref, principal="workflow:assessment", allow_restricted_raw=True
        )
        assert MARKER.encode() not in body
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


async def retain_native_fixture(
    environment,
    tmp_path,
    monkeypatch,
    scanner,
    reports,
    key,
    *,
    output_directory="artifacts/scan",
    scan_options=None,
    database=None,
    settings=None,
):
    """Replace the detector process only; retain production admission and storage."""
    case = tmp_path / key
    case.mkdir()
    root = case / "repository"
    repository(root)
    (root / (MARKER + ".txt")).unlink()
    (root / "clean.py").write_text("print('clean')\n")
    (root / "unexamined.py").write_text("eval(user_input)\n")
    options = {"trivyScanners": ["misconfig"]} if scanner == "trivy" else {}
    if scanner == "opengrep":
        rules = case / "rules" / "source.yaml"
        rules.parent.mkdir()
        rules.write_text("rules: []\n")
        options["rules"] = [
            {
                "id": "source",
                "path": "/opt/moonmind-scanner-rules/source.yaml",
                "sha256": hashlib.sha256(rules.read_bytes()).hexdigest(),
                "license": "MIT",
            }
        ]
    options.update(scan_options or {})
    scan = RepositoryScan(scanner=scanner, outputDirectory=output_directory, **options)
    install_fixture_process(case, scanner, monkeypatch, reports=reports)
    workload = build_scanner_job(scan)
    workload.pop("imageSourceRef")
    workload.update(
        image="python:3.13", workspaceRef={"kind": "sandbox", "workspaceId": key}
    )
    submission = ContainerJobSubmitRequest(
        idempotencyKey=key,
        source={"source": "workflow", "workflowId": "assessment"},
        spec=workload,
    )
    owner = OwnerIdentity(principalType="user", principalId="operator")
    jobs = ContainerJobService(environment.session, temporal=AsyncMock())
    accepted = await jobs.submit(owner=owner, request=submission)
    published = {}

    async def publish(_request, name, payload):
        assert MARKER not in name
        assert MARKER.encode() not in payload
        ref = await put(environment.service, payload)
        published[name] = ref
        return ref

    async def runner(argv):
        if argv[0] == "inspect":
            return 1, b"", b"no such container"
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return 0, f"{timestamp} {MARKER}\n".encode(), b""

    request = ContainerJobActivityRequest(
        jobId=accepted.job_id,
        ownershipToken=f"{accepted.job_id}:v1",
        request=submission,
        resolvedWorkspaceRef=str(root),
        resolvedImageRef="sha256:" + "a" * 64,
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publish,
        log_spool_root=case / "live",
        **({"settings": settings} if settings else {}),
    )
    # The production launch captures the source; the fixture scanner then
    # runs against the paths the container sees as /inputs and /outputs.
    await backend.create_container(request)
    storage = storage_for(tmp_path, accepted.job_id)
    code = run_scanner(
        scan,
        source=storage.inputs / "source",
        output=storage.outputs / scan.output_directory,
        rules_root=case / "rules",
        **({"database": database} if database else {}),
    )
    # Reports never land in the examined source.
    assert not (root / scan.output_directory).exists()
    request.container_ref = "fixture-container"
    request.terminal_state = "succeeded" if code == 0 else "failed"
    request.exit_code = code
    await backend._collect_live_logs(request, request.container_ref)
    evidence = await backend.publish_evidence(request)
    assert all(
        MARKER.encode() not in path.read_bytes()
        for path in (case / "live").rglob("*.jsonl")
    )
    record = await jobs.repository.get_for_owner(owner=owner, job_id=accepted.job_id)
    record.logs_ref, record.artifacts_ref = evidence.logs_ref, evidence.artifacts_ref
    await environment.session.flush()
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
    ref = published[f"{accepted.job_id}-output-summary.json"]
    return ref, await read(environment.service, ref)


async def test_missing_feed_snapshot_reaches_report_as_incomplete_coverage(
    environment, tmp_path, monkeypatch
):
    feeds = tmp_path / "feeds"
    (feeds / "snapshots").mkdir(parents=True)
    _, summary = await retain_native_fixture(
        environment,
        tmp_path,
        monkeypatch,
        "trivy",
        {"scan": native("trivy")},
        "issue-5041-absent-feed",
        scan_options={
            "trivyScanners": ["vuln", "misconfig"],
            "databaseCacheRef": "fixture-db",
            "databaseDigest": "e" * 64,
        },
        database=feeds,
        settings=resolve_container_backend_settings(
            {
                CACHE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "cacheRef": "fixture-db",
                            "volumeName": "scanner-feeds",
                            "target": "/opt/moonmind-scanner-db",
                            "readOnly": True,
                        }
                    ]
                )
            }
        ),
    )
    assert summary["coverage"] == "partial"
    assert "feed_snapshot_unavailable" in summary["limitations"]
    # The runner's own record survives; missing data is never a clean result.
    assert "missing_or_invalid_execution_evidence" not in summary["limitations"]
    assert summary["execution"]["databaseDigest"] is None
    assert all(not row["findings"] for row in summary["results"].values())


def retained_assessment(ref, assets):
    return {
        "scope": {"assets": assets},
        "tools": ["retained-report"],
        "reportRefs": [ref],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
    }


@pytest.mark.parametrize("kind", ["truncated", "malformed"])
@pytest.mark.parametrize("valid_sibling", [False, True])
async def test_unreadable_native_reports_withhold_admission_metadata_through_report(
    environment, tmp_path, monkeypatch, kind, valid_sibling
):
    invalid = b'[{"Secret":"' if kind == "truncated" else {}
    valid = [
        {
            "RuleID": "credential",
            "File": "tracked.txt",
            "StartLine": 1,
            "Secret": "[REDACTED]",
        }
    ]
    reports = {"history": invalid, "worktree": valid if valid_sibling else invalid}
    ref, summary = await retain_native_fixture(
        environment,
        tmp_path,
        monkeypatch,
        "gitleaks",
        reports,
        "unsafe-metadata",
        output_directory=f"artifacts/scan-{MARKER}",
    )
    outputs = await invoke(
        environment.service, retained_assessment(ref, ["tracked.txt"])
    )
    assert not outputs["diagnostics"]
    assert MARKER not in json.dumps(summary)
    assert MARKER not in json.dumps(outputs)
    assert summary["coverage"] == "partial"
    assert outputs["coverageStatus"] == "stale_or_partial"
    assert outputs["counts"]["new"] == int(valid_sibling)
    assert bool(summary["nativeRefs"]) == valid_sibling
    for artifact_ref in {
        outputs["scanRef"],
        outputs["comparisonRef"],
        outputs["reportBundle"]["primary_report_ref"]["artifact_id"],
    }:
        _, body = await environment.service.read(
            artifact_id=artifact_ref,
            principal="workflow:assessment",
            allow_restricted_raw=True,
        )
        assert MARKER.encode() not in body


@pytest.mark.parametrize(
    "position", ["first", "middle", "last", "invalid-only", "clean"]
)
async def test_gitleaks_malformed_neighbors_survive_ingestion_and_report(
    environment, tmp_path, monkeypatch, position
):
    valid = [
        {"RuleID": "credential", "File": path, "StartLine": 1}
        for path in ("tracked.txt", "untracked.txt")
    ]
    records = {
        "first": [{}, *valid],
        "middle": [valid[0], {}, valid[1]],
        "last": [*valid, {}],
        "invalid-only": [{}],
        "clean": [],
    }[position]
    ref, summary = await retain_native_fixture(
        environment,
        tmp_path,
        monkeypatch,
        "gitleaks",
        {"history": records, "worktree": []},
        position,
    )
    findings = position in {"first", "middle", "last"}
    assert len(summary["results"]["history"]["findings"]) == (2 if findings else 0)
    assert summary["coverage"] == ("complete" if position == "clean" else "partial")
    outputs = await invoke(
        environment.service,
        retained_assessment(ref, ["tracked.txt", "untracked.txt"]),
        key=position,
    )
    assert not outputs["diagnostics"]
    assert outputs["counts"]["new"] == (2 if findings else 0)
    assert outputs["coverageStatus"] == (
        "complete" if position == "clean" else "stale_or_partial"
    )
    comparison = await read(environment.service, outputs["comparisonRef"])
    assert comparison["observation"]["coverage"][0]["status"] == (
        "complete" if position == "clean" else "partial"
    )
    if findings:
        retained = await read(environment.service, summary["nativeRefs"]["history"])
        assert len([row for row in retained if row.get("RuleID")]) == 2
        assert {row["finding"]["nativeId"] for row in comparison["findings"]} == {
            row["nativeId"] for row in summary["results"]["history"]["findings"]
        }


@pytest.mark.parametrize("kind", ["malformed", "mixed", "clean"])
async def test_trivy_result_structure_remains_honest_through_retained_report(
    environment, tmp_path, monkeypatch, kind
):
    valid = {
        "Target": "main.tf",
        "Misconfigurations": [{"ID": "AVD-EXAMPLE", "Status": "FAIL"}],
    }
    results = (
        [valid, {}]
        if kind == "mixed"
        else (
            [{}]
            if kind == "malformed"
            else [{"Target": "main.tf", "Misconfigurations": []}]
        )
    )
    ref, summary = await retain_native_fixture(
        environment,
        tmp_path,
        monkeypatch,
        "trivy",
        {"scan": {"SchemaVersion": 2, "Results": results}},
        kind,
    )
    outputs = await invoke(
        environment.service, retained_assessment(ref, ["main.tf"]), key=kind
    )
    assert not outputs["diagnostics"]
    assert summary["coverage"] == ("complete" if kind == "clean" else "partial")
    assert outputs["coverageStatus"] == (
        "complete" if kind == "clean" else "stale_or_partial"
    )
    assert outputs["counts"]["new"] == (1 if kind == "mixed" else 0)
    comparison = await read(environment.service, outputs["comparisonRef"])
    assert comparison["observation"]["coverage"][0]["status"] == (
        "complete" if kind == "clean" else "partial"
    )


@pytest.mark.parametrize(
    "case", ["unexamined", "skipped", "parser-failed", "missing-inventory", "clean"]
)
async def test_opengrep_baseline_comparison_uses_native_examined_scope(
    environment, tmp_path, monkeypatch, case
):
    assets = ["clean.py", "unexamined.py"]
    findings = [
        {"check_id": "native-rule", "path": path, "start": {"line": 1}}
        for path in assets
    ]
    baseline_ref, _ = await retain_native_fixture(
        environment,
        tmp_path,
        monkeypatch,
        "opengrep",
        {"scan": {"results": findings, "errors": [], "paths": {"scanned": assets}}},
        "baseline",
    )
    baseline = await invoke(
        environment.service, retained_assessment(baseline_ref, assets), key="baseline"
    )
    current = {
        "results": [] if case == "clean" else findings[:1],
        "errors": [],
        "paths": {
            "scanned": assets if case in {"clean", "parser-failed"} else assets[:1]
        },
    }
    if case == "skipped":
        current["paths"]["skipped"] = [
            {"path": "unexamined.py", "reason": "unsupported"}
        ]
    elif case == "parser-failed":
        current["errors"] = [
            {"type": "ParseError", "spans": [{"file": "unexamined.py"}]}
        ]
    elif case == "missing-inventory":
        current["paths"] = {}
    ref, summary = await retain_native_fixture(
        environment,
        tmp_path,
        monkeypatch,
        "opengrep",
        {"scan": current},
        "current",
        output_directory="artifacts/current",
    )
    request = {
        **retained_assessment(ref, assets),
        "baselineRef": baseline["comparisonRef"],
    }
    outputs = await invoke(environment.service, request, key="current")
    assert not outputs["diagnostics"]
    comparison = await read(environment.service, outputs["comparisonRef"])
    states = {row["finding"]["asset"]: row["state"] for row in comparison["findings"]}
    assert states["unexamined.py"] == (
        "absent_comparable" if case == "clean" else "not_reassessed"
    )
    coverage = comparison["observation"]["coverage"][0]
    assert coverage["assets"] == (
        assets
        if case == "clean"
        else ([] if case == "missing-inventory" else assets[:1])
    )
    assert outputs["coverageStatus"] == (
        "complete" if case == "clean" else "stale_or_partial"
    )
    if case not in {"clean", "missing-inventory"}:
        assert states["clean.py"] == "still_observed"
        assert summary["results"]["scan"]["findings"]


async def test_combined_gitleaks_findings_reach_report_with_partial_coverage(
    environment, tmp_path, monkeypatch
):
    reports = {
        mode: [
            {
                "RuleID": "credential",
                "File": "tracked.txt",
                "StartLine": index + 1,
                "Commit": commit * 40,
            }
            for index in range(2050)
        ]
        for mode, commit in (("history", "a"), ("worktree", "b"))
    }
    ref, summary = await retain_native_fixture(
        environment, tmp_path, monkeypatch, "gitleaks", reports, "combined"
    )
    assert sum(len(row["findings"]) for row in summary["results"].values()) == 4100
    outputs = await invoke(
        environment.service, retained_assessment(ref, ["tracked.txt"])
    )
    assert not outputs["diagnostics"]
    assert outputs["counts"]["new"] == 4096
    assert outputs["coverageStatus"] == "stale_or_partial"
    retained_summary = outputs["scannerSummaries"][0]
    assert retained_summary["nativeRefs"] == summary["nativeRefs"]
    assert retained_summary["coverage"] == "partial"
    assert "combined_finding_bound_exceeded" in retained_summary["limitations"]
    comparison = await read(environment.service, outputs["comparisonRef"])
    assert len(comparison["observation"]["findings"]) == 4096
    _, markdown = await environment.service.read(
        artifact_id=outputs["reportBundle"]["primary_report_ref"]["artifact_id"],
        principal="workflow:assessment",
        allow_restricted_raw=True,
    )
    assert b"combined_finding_bound_exceeded" in markdown
